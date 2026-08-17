from dataclasses import dataclass

import docker

from runner.config import settings
from runner.exceptions import (
    ContainerExecutionError,
    DockerUnavailableError,
    WorkspaceError,
)
from runner.pipeline.workspace import VolumeWorkspace, build_source_archive


@dataclass
class CompileResult:
    """Compile Container에서 수집한 컴파일 결과."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    artifact_ready: bool = False

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


def _artifact_exists(container) -> bool:
    try:
        stream, _ = container.get_archive("/workspace/main")
    except docker.errors.NotFound:
        return False

    close = getattr(stream, "close", None)
    if callable(close):
        close()
    return True


def create_compile_container(
    client,
    workspace: VolumeWorkspace,
    language,
):
    """Job Volume을 연결한 컴파일 컨테이너를 생성하고 반환한다."""

    config = _get_compiler_config(language)
    source_filename = config["source_filename"]

    try:
        return client.containers.create(
            image=settings.cpp_image,
            command=[
                config["compiler"],
                config["standard"],
                *COMMON_COMPILE_FLAGS,
                f"/workspace/{source_filename}",
                "-o",
                "/workspace/main",
            ],
            volumes={
                workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "rw",
                }
            },
            detach=True,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(workspace.job_id),
                "codeguard.stage": "compile",
            },
        )
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
        source_archive = build_source_archive(language, code, stdin=stdin)
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
        wait_result = container.wait()
        exit_code = int(wait_result["StatusCode"])
        stdout_bytes = container.logs(stdout=True, stderr=False)
        stderr_bytes = container.logs(stdout=False, stderr=True)
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")

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
