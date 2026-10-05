import io
import tarfile
from dataclasses import dataclass
from uuid import UUID, uuid4

import docker
from requests.exceptions import Timeout

from runner.config import settings
from runner.exceptions import CleanupError, SecurityVerificationError, WorkspaceError
from runner.security import SECURITY_GID, SECURITY_OPT, SECURITY_UID


SOURCE_FILENAMES = {"C": "main.c", "CPP": "main.cpp"}
WORKSPACE_PREPARE_PATH = "/usr/local/bin/codeguard-workspace-prepare"
RUNTIME_MANIFEST_PATH = "/usr/local/share/codeguard/filesystem-runtime.json"
PREPARE_CAPABILITIES = ("CHOWN", "FOWNER", "DAC_OVERRIDE")
MANIFEST_MAX_BYTES = 64 * 1024
MANIFEST_ARCHIVE_MAX_BYTES = 128 * 1024


@dataclass(frozen=True)
class VolumeWorkspace:
    """Independent per-job application, input and scratch volumes."""

    job_id: UUID
    app_volume: str
    input_volume: str
    work_volume: str

    @property
    def volume_name(self) -> str:
        """Read-only compatibility alias, never the complete runtime workspace."""
        return self.app_volume


def create_workspace(client, job_id: UUID) -> VolumeWorkspace:
    # Docker's create-volume endpoint reuses an existing name. A fresh nonce
    # isolates retries/parallel submissions that share the same logical job ID.
    nonce = uuid4().hex
    created = []
    names = {}
    try:
        for purpose in ("app", "input", "work"):
            name = f"{settings.volume_name_prefix}{job_id}-{nonce}-{purpose}"
            volume = client.volumes.create(name=name, labels={
                "codeguard.managed": "true", "codeguard.job_id": str(job_id),
                "codeguard.purpose": purpose,
            })
            created.append(volume)
            names[purpose] = name
    except docker.errors.DockerException as exc:
        cleanup_errors = []
        for volume in created:
            try:
                volume.remove(force=True)
            except docker.errors.NotFound:
                pass
            except docker.errors.DockerException as cleanup_exc:
                cleanup_errors.append(str(cleanup_exc))
        raise WorkspaceError("Job volume creation failed.", details={
            "job_id": str(job_id), "reason": str(exc), "cleanup_errors": cleanup_errors,
        }) from exc
    return VolumeWorkspace(job_id, names["app"], names["input"], names["work"])


def _file_archive(filename: str, data: bytes, mode: int) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(filename)
        member.size, member.mode = len(data), mode
        member.uid, member.gid = SECURITY_UID, SECURITY_GID
        archive.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


def build_source_archive(language: str, code: str) -> bytes:
    language_value = getattr(language, "value", language)
    filename = SOURCE_FILENAMES.get(language_value)
    if filename is None:
        raise WorkspaceError("Unsupported language.", details={"language": str(language_value)})
    return _file_archive(filename, code.encode("utf-8"), 0o600)


def build_stdin_archive(stdin: str = "") -> bytes:
    """Always create the single fixed stdin file, including empty input."""
    return _file_archive("stdin", stdin.encode("utf-8"), 0o444)


def execution_mounts(workspace: VolumeWorkspace) -> list:
    return [
        docker.types.Mount("/workspace/app", workspace.app_volume, type="volume", read_only=True),
        docker.types.Mount("/workspace/input", workspace.input_volume, type="volume", read_only=True),
        docker.types.Mount("/workspace/work", workspace.work_volume, type="volume", read_only=False),
        docker.types.Mount("/run/codeguard-trace", None, type="volume", read_only=False),
    ]


def _verify_prepare_config(container, workspace: VolumeWorkspace, image_id: str | None) -> None:
    """The root helper has a distinct policy from unprivileged job containers."""
    container.reload()
    attrs = container.attrs
    config, host = attrs.get("Config", {}), attrs.get("HostConfig", {})
    checks = {
        "user": config.get("User") == "0:0",
        "command": config.get("Cmd") == [WORKSPACE_PREPARE_PATH],
        "entrypoint": not config.get("Entrypoint"),
        "readonly_rootfs": host.get("ReadonlyRootfs") is True,
        "network": host.get("NetworkMode") == "none",
        "privileged": host.get("Privileged") is False,
        "cap_drop": {str(c).upper() for c in host.get("CapDrop") or []} == {"ALL"},
        "cap_add": {str(c).upper() for c in host.get("CapAdd") or []} == set(PREPARE_CAPABILITIES),
        "no_new_privileges": SECURITY_OPT in (host.get("SecurityOpt") or []),
    }
    if image_id is not None:
        checks["image_id"] = attrs.get("Image") == image_id
    mounts = attrs.get("Mounts", [])
    expected = {"/workspace/app": workspace.app_volume,
                "/workspace/input": workspace.input_volume,
                "/workspace/work": workspace.work_volume}
    checks["mounts"] = len(mounts) == 3 and all(
        sum(m.get("Destination") == target and m.get("Type") == "volume"
            and m.get("Name") == name and m.get("RW") is True for m in mounts) == 1
        for target, name in expected.items())
    failures = [name for name, valid in checks.items() if not valid]
    if failures:
        raise SecurityVerificationError("Workspace helper configuration verification failed.",
                                        details={"checks": failures})


def _runtime_manifest(container) -> bytes:
    stream, metadata = container.get_archive(RUNTIME_MANIFEST_PATH, chunk_size=512)
    try:
        if (metadata.get("name") != "filesystem-runtime.json"
                or metadata.get("linkTarget")
                or type(metadata.get("size")) is not int
                or not 0 < metadata["size"] <= MANIFEST_MAX_BYTES
                or type(metadata.get("mode")) is not int
                or metadata["mode"] & ~0o777
                or metadata["mode"] & 0o022):
            raise WorkspaceError("Invalid runtime manifest metadata.")
        buffer = io.BytesIO()
        for chunk in stream:
            if buffer.tell() + len(chunk) > MANIFEST_ARCHIVE_MAX_BYTES:
                raise WorkspaceError("Runtime manifest archive exceeds size limit.")
            buffer.write(chunk)
        buffer.seek(0)
        with tarfile.open(fileobj=buffer, mode="r:") as archive:
            members = archive.getmembers()
            if len(members) != 1:
                raise WorkspaceError("Runtime manifest archive must contain exactly one file.")
            member = members[0]
            if (member.name != "filesystem-runtime.json" or not member.isreg()
                    or member.linkname or member.uid != 0 or member.gid != 0
                    or member.mode & ~0o777 or member.mode & 0o022
                    or not 0 < member.size <= MANIFEST_MAX_BYTES
                    or member.size != metadata["size"]):
                raise WorkspaceError("Invalid runtime manifest archive member.")
            with archive.extractfile(member) as file:
                data = file.read(MANIFEST_MAX_BYTES + 1)
            if len(data) != member.size:
                raise WorkspaceError("Truncated runtime manifest.")
            return data
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise WorkspaceError("Invalid runtime manifest archive.", details={"reason": str(exc)}) from exc
    finally:
        close = getattr(stream, "close", None)
        if callable(close): close()


def prepare_workspace(client, workspace: VolumeWorkspace, language, code: str, stdin: str = "",
                      *, image_id: str | None = None) -> bytes:
    """Upload fixed inputs, run only the trusted helper, return raw manifest bytes.

    The controller resolves and validates the image once, passes its immutable ID
    here and to compile/execution, and parses/binds the returned profile itself.
    """
    source_archive, input_archive = build_source_archive(language, code), build_stdin_archive(stdin)
    container = None
    try:
        container = client.containers.create(
            image=image_id if image_id is not None else settings.cpp_image,
            command=[WORKSPACE_PREPARE_PATH], entrypoint=[],
            mounts=[docker.types.Mount(f"/workspace/{purpose}", name, type="volume", read_only=False)
                    for purpose, name in (("app", workspace.app_volume), ("input", workspace.input_volume),
                                          ("work", workspace.work_volume))],
            detach=True, read_only=True, network_mode="none", user="0:0",
            cap_drop=["ALL"], cap_add=list(PREPARE_CAPABILITIES), security_opt=[SECURITY_OPT],
            labels={"codeguard.managed": "true", "codeguard.job_id": str(workspace.job_id),
                    "codeguard.stage": "workspace-prepare"},
        )
        _verify_prepare_config(container, workspace, image_id)
        for path, data in (("/workspace/app", source_archive), ("/workspace/input", input_archive)):
            if not container.put_archive(path=path, data=data):
                raise WorkspaceError("Workspace input upload failed.", details={"path": path})
        container.start()
        result = container.wait(timeout=5)
        if result.get("StatusCode") != 0:
            raise WorkspaceError("Workspace helper failed.", details={"exit_code": result.get("StatusCode")})
        return _runtime_manifest(container)
    except (docker.errors.DockerException, Timeout) as exc:
        raise WorkspaceError("Workspace preparation failed.", details={"reason": str(exc)}) from exc
    finally:
        if container is not None:
            try:
                # Docker v removes anonymous volumes only; named job volumes stay.
                container.remove(force=True, v=True)
            except docker.errors.NotFound:
                pass
            except docker.errors.DockerException as exc:
                raise CleanupError("Workspace helper cleanup failed.", details={"reason": str(exc)}) from exc


def remove_workspace(client, workspace: VolumeWorkspace) -> None:
    failures = []
    for name in (workspace.app_volume, workspace.input_volume, workspace.work_volume):
        try:
            client.volumes.get(name).remove(force=True)
        except docker.errors.NotFound:
            pass
        except docker.errors.DockerException as exc:
            failures.append({"volume_name": name, "reason": str(exc)})
    if failures:
        raise CleanupError("Job volume cleanup failed.", details={
            "job_id": str(workspace.job_id), "failures": failures,
        })
