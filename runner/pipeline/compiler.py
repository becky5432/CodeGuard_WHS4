import io
import tarfile
from dataclasses import dataclass
import logging

import docker
from requests.exceptions import ConnectionError, Timeout
from urllib3.exceptions import ReadTimeoutError

from runner.config import settings
from runner.exceptions import (
    ContainerExecutionError,
    DockerUnavailableError,
    RunnerError,
    SecurityVerificationError,
    WorkspaceError,
)
from runner.pipeline.workspace import VolumeWorkspace, build_source_archive
from runner.policies import COMPILE_TIMEOUT_SECONDS
from runner.security import (
    SECURITY_CAP_DROP,
    SECURITY_GID,
    SECURITY_OPT,
    SECURITY_PREFLIGHT_PATH,
    SECURITY_UID,
    parse_security_preflight,
    security_environment,
    verify_container_security_config,
)


logger = logging.getLogger("runner")


@dataclass
class CompileResult:
    """Compile Container에서 수집한 컴파일 결과."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    artifact_ready: bool = False
    timed_out: bool = False

COMMON_COMPILE_FLAGS = [
    "-Wall",
    "-Wextra",
    "-O0",
]

COMPILER_CONFIG = {
    "C": {
        "source_filename": "main.c",
        "compiler": "gcc",
        "standard": "-std=c17",
    },
    "CPP": {
        "source_filename": "main.cpp",
        "compiler": "g++",
        "standard": "-std=c++17",
    },
}


def _is_compile_wait_timeout(exc: BaseException) -> bool:
    """Docker wait가 ConnectionError로 감싼 read timeout인지 확인한다."""

    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(current, (Timeout, ReadTimeoutError)):
            return True

        for linked in (current.__cause__, current.__context__):
            if isinstance(linked, BaseException):
                pending.append(linked)
        pending.extend(
            arg for arg in current.args if isinstance(arg, BaseException)
        )

    return False


def get_docker_client():
    """Docker Engine 연결을 확인한 클라이언트를 반환한다."""

    try:
        client = docker.from_env()
        client.ping()
        return client
    except docker.errors.DockerException as exc:
        raise DockerUnavailableError(
            "Docker Engine에 연결할 수 없습니다.",
            details={"reason": str(exc)},
        ) from exc


def _get_compiler_config(language) -> dict:
    language_value = getattr(language, "value", language)
    config = COMPILER_CONFIG.get(language_value)
    if config is None:
        raise WorkspaceError(
            "지원하지 않는 언어입니다.",
            details={"language": str(language_value)},
        )
    return config


class _ArtifactPrefixReader(io.RawIOBase):
    """Bound tar header parsing without materializing an arbitrary ELF body."""

    def __init__(self, stream):
        self.chunks = iter(stream)
        self.pending = b""
        self.remaining = 16 * 1024

    def read(self, size=-1):
        if size < 0 or size > 16 * 1024:
            raise ValueError("Unbounded artifact archive read")
        parts = []
        while size:
            if not self.pending:
                chunk = next(self.chunks, None)
                if chunk is None:
                    break
                if len(chunk) > self.remaining:
                    raise ValueError("Artifact archive header exceeds limit")
                self.remaining -= len(chunk)
                self.pending = chunk
                if not chunk:
                    continue
            part, self.pending = self.pending[:size], self.pending[size:]
            parts.append(part)
            size -= len(part)
        return b"".join(parts)


def _artifact_exists(container) -> bool:
    try:
        stream, metadata = container.get_archive("/workspace/main", chunk_size=512)
    except docker.errors.NotFound:
        return False
    try:
        if (metadata.get("name") != "main" or metadata.get("linkTarget")
                or type(metadata.get("size")) is not int or metadata["size"] < 4
                or type(metadata.get("mode")) is not int
                or metadata["mode"] & ~0o777 or metadata["mode"] & 0o022
                or not metadata["mode"] & 0o100):
            return False
        reader = _ArtifactPrefixReader(stream)
        with tarfile.open(fileobj=reader, mode="r|", bufsize=512) as archive:
            member = archive.next()
            if (member is None or member.name != "main"
                    or member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE)
                    or member.linkname or member.sparse is not None
                    or member.uid != SECURITY_UID or member.gid != SECURITY_GID
                    or member.mode & ~0o777 or member.mode & 0o022
                    or not member.mode & 0o100 or member.size != metadata["size"]):
                return False
            with archive.extractfile(member) as artifact:
                # read() on ExFileObject's BufferedReader prefetches 8 KiB;
                # read1() requests only this four-byte signature from the tar.
                return artifact.read1(4) == b"\x7fELF"
    except (tarfile.TarError, OSError, ValueError):
        return False
    finally:
        # Cancel/close Docker's HTTP stream, do not drain a huge artifact body.
        close = getattr(stream, "close", None)
        if callable(close):
            close()


def create_compile_container(
    client,
    workspace: VolumeWorkspace,
    language,
    *,
    image_id: str | None = None,
):
    """Job Volume을 연결한 컴파일 컨테이너를 생성하고 반환한다."""

    config = _get_compiler_config(language)
    source_filename = config["source_filename"]

    try:
        container = client.containers.create(
            image=image_id if image_id is not None else settings.cpp_image,
            command=[
                SECURITY_PREFLIGHT_PATH,
                config["compiler"],
                config["standard"],
                *COMMON_COMPILE_FLAGS,
                f"/workspace/{source_filename}",
                "-o",
                "/workspace/main",
            ],
            mounts=[docker.types.Mount("/workspace", workspace.volume_name, type="volume",
                                      read_only=False, no_copy=True)],
            detach=True,
            network_mode="none",
            user=f"{SECURITY_UID}:{SECURITY_GID}",
            cap_drop=list(SECURITY_CAP_DROP),
            security_opt=[SECURITY_OPT],
            environment=security_environment(),
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(workspace.job_id),
                "codeguard.stage": "compile",
            },
        )
        try:
            verify_container_security_config(
                container,
                stage="compile",
                workspace_mode="rw",
                expected_image_id=image_id,
            )
            # Compile must see only this job's single volume.
            mounts = container.attrs.get("Mounts", [])
            if (len(mounts) != 1 or mounts[0].get("Destination") != "/workspace"
                    or mounts[0].get("Type") != "volume"
                    or mounts[0].get("Name") != workspace.volume_name
                    or mounts[0].get("RW") is not True):
                raise SecurityVerificationError("Compile job volume identity verification failed.")
        except RunnerError:
            try:
                container.remove(force=True)
            except docker.errors.DockerException:
                pass
            raise
        return container
    except docker.errors.ImageNotFound as exc:
        raise ContainerExecutionError(
            "컴파일용 Docker 이미지를 찾을 수 없습니다.",
            details={"image": settings.cpp_image},
        ) from exc
    except docker.errors.DockerException as exc:
        raise ContainerExecutionError(
            "컴파일 컨테이너 생성에 실패했습니다.",
            details={"reason": str(exc)},
        ) from exc


def compile_source(
    container,
    workspace: VolumeWorkspace,
    language,
    code: str,
    stdin: str = "",
) -> CompileResult:
    """소스를 전달해 컴파일하고 컨테이너 객체는 정리를 위해 유지한다."""

    _get_compiler_config(language)

    try:
        # Preparation owns stdin in the job volume. Re-uploading the fixed
        # source file here is idempotent for existing compile callers.
        source_archive = build_source_archive(language, code)
        try:
            uploaded = container.put_archive(
                path="/workspace",
                data=source_archive,
            )
        except docker.errors.DockerException as exc:
            raise WorkspaceError(
                "소스 코드 전달에 실패했습니다.",
                details={
                    "job_id": str(workspace.job_id),
                    "reason": str(exc),
                },
            ) from exc

        if not uploaded:
            raise WorkspaceError(
                "소스 코드 전달에 실패했습니다.",
                details={"job_id": str(workspace.job_id)},
            )

        container.start()
        try:
            wait_result = container.wait(timeout=COMPILE_TIMEOUT_SECONDS)
        except (Timeout, ConnectionError) as exc:
            if not _is_compile_wait_timeout(exc):
                raise

            logger.warning(
                "event=compile_timeout timeout_seconds=%s",
                COMPILE_TIMEOUT_SECONDS,
            )
            try:
                container.kill()
            except docker.errors.DockerException:
                # executor의 finally에서 force remove를 다시 수행한다.
                pass

            try:
                container.wait(timeout=2)
            except ConnectionError as exc:
                if not _is_compile_wait_timeout(exc):
                    raise
            except (Timeout, docker.errors.DockerException):
                pass

            return CompileResult(
                success=False,
                stdout="",
                stderr="",
                exit_code=None,
                artifact_ready=False,
                timed_out=True,
            )
        exit_code = int(wait_result["StatusCode"])
        stdout_bytes = container.logs(stdout=True, stderr=False)
        stderr_bytes = container.logs(stdout=False, stderr=True)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        stderr = parse_security_preflight(stderr, stage="compile")

        artifact_ready = exit_code == 0 and _artifact_exists(container)

        return CompileResult(
            success=(exit_code == 0 and artifact_ready),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            artifact_ready=artifact_ready,
        )
    except WorkspaceError:
        raise
    except docker.errors.DockerException as exc:
        raise ContainerExecutionError(
            "컴파일 컨테이너 실행에 실패했습니다.",
            details={"reason": str(exc)},
        ) from exc
