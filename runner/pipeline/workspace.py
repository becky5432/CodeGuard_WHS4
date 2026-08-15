import io
import tarfile
from dataclasses import dataclass
from uuid import UUID

import docker

from runner.config import settings
from runner.exceptions import CleanupError, WorkspaceError


SOURCE_FILENAMES = {
    "C": "main.c",
    "CPP": "main.cpp",
}


@dataclass(frozen=True)
class VolumeWorkspace:
    """한 Job이 Compile과 Execution에서 공유하는 Docker Volume 정보."""

    job_id: UUID
    volume_name: str


def create_workspace(client, job_id: UUID) -> VolumeWorkspace:
    """Job 전용 Docker Volume을 생성한다."""

    volume_name = f"{settings.volume_name_prefix}{job_id}"

    try:
        volume = client.volumes.create(
            name=volume_name,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(job_id),
                "codeguard.purpose": "workspace",
            },
        )
    except docker.errors.DockerException as exc:
        raise WorkspaceError(
            "Job Volume 생성에 실패했습니다.",
            details={
                "job_id": str(job_id),
                "volume_name": volume_name,
                "reason": str(exc),
            },
        ) from exc

    return VolumeWorkspace(
        job_id=job_id,
        volume_name=volume.name,
    )


def build_source_archive(language: str, code: str, stdin: str = "") -> bytes:
    """소스 코드 한 개를 호스트 파일 없이 메모리 TAR로 만든다."""

    language_value = getattr(language, "value", language)
    filename = SOURCE_FILENAMES.get(language_value)

    if filename is None:
        raise WorkspaceError(
            "지원하지 않는 언어입니다.",
            details={"language": str(language_value)},
        )

    source_bytes = code.encode("utf-8")
    archive_buffer = io.BytesIO()

    with tarfile.open(fileobj=archive_buffer, mode="w") as archive:
        file_info = tarfile.TarInfo(name=filename)
        file_info.size = len(source_bytes)
        file_info.mode = 0o600
        archive.addfile(file_info, io.BytesIO(source_bytes))

        if stdin:
            stdin_bytes = stdin.encode("utf-8")
            stdin_info = tarfile.TarInfo(name="stdin")
            stdin_info.size = len(stdin_bytes)
            stdin_info.mode = 0o444
            archive.addfile(stdin_info, io.BytesIO(stdin_bytes))

    return archive_buffer.getvalue()


def remove_workspace(client, workspace: VolumeWorkspace) -> None:
    """Job Volume을 삭제한다. 이미 사라진 Volume은 정리 완료로 본다."""

    try:
        volume = client.volumes.get(workspace.volume_name)
        volume.remove(force=True)
    except docker.errors.NotFound:
        return
    except docker.errors.DockerException as exc:
        raise CleanupError(
            "Job Volume 삭제에 실패했습니다.",
            details={
                "job_id": str(workspace.job_id),
                "volume_name": workspace.volume_name,
                "reason": str(exc),
            },
        ) from exc
