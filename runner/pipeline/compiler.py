from dataclasses import dataclass
from time import monotonic, monotonic_ns, sleep

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
    """Compile Container에서 수집한 Runner 내부 결과."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False
    compile_time_ms: int = 0
    log_truncated: bool = False
    artifact_ready: bool = False


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
    """Docker Engine에 연결된 클라이언트를 반환한다."""

    try:
        client = docker.from_env()
        client.ping()
        return client
    except docker.errors.DockerException as exc:
        raise DockerUnavailableError(
            "Docker Engine에 연결할 수 없습니다.",
            details={"reason": str(exc)},
        ) from exc


def _decode_bounded(data: bytes, limit: int) -> tuple[str, bool]:
    truncated = len(data) > limit
    bounded = data[:limit]
    return bounded.decode("utf-8", errors="replace"), truncated


def _artifact_exists(container) -> bool:
    try:
        stream, _ = container.get_archive("/workspace/main")
    except docker.errors.NotFound:
        return False

    close = getattr(stream, "close", None)
    if callable(close):
        close()
    return True


def _wait_for_exit(container, timeout_seconds: float) -> int | None:
    """Poll compile-container state without transport timeout ambiguity."""

    deadline = monotonic() + timeout_seconds
    while True:
        container.reload()
        state = container.attrs.get("State", {})
        if not bool(state.get("Running", False)):
            return int(state.get("ExitCode", 0))
        if monotonic() >= deadline:
            return None
        sleep(min(0.02, max(deadline - monotonic(), 0)))


def compile_source(
    client,
    workspace: VolumeWorkspace,
    language,
    code: str,
    stdin: str = "",
) -> CompileResult:
    """소스를 Job Volume에 업로드하고 C17 또는 C++17로 컴파일한다."""

    language_value = getattr(language, "value", language)
    config = COMPILER_CONFIG.get(language_value)

    if config is None:
        raise WorkspaceError(
            "지원하지 않는 언어입니다.",
            details={"language": str(language_value)},
        )

    source_filename = config["source_filename"]
    compiler = config["compiler"]
    standard = config["standard"]
    container = None
    started_ns = monotonic_ns()

    try:
        container = client.containers.create(
            image=settings.cpp_image,
            command=[
                compiler,
                standard,
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
            network_disabled=True,
            detach=True,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(workspace.job_id),
                "codeguard.stage": "compile",
            },
        )

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

        exit_code = _wait_for_exit(
            container,
            settings.compile_timeout_seconds,
        )
        if exit_code is None:
            container.kill()
            return CompileResult(
                success=False,
                stdout="",
                stderr="Compilation timed out.",
                exit_code=None,
                timed_out=True,
                compile_time_ms=(monotonic_ns() - started_ns) // 1_000_000,
            )

        stdout_bytes = container.logs(stdout=True, stderr=False)
        stderr_bytes = container.logs(stdout=False, stderr=True)
        stdout, stdout_truncated = _decode_bounded(
            stdout_bytes,
            settings.compile_log_limit_bytes,
        )
        stderr, stderr_truncated = _decode_bounded(
            stderr_bytes,
            settings.compile_log_limit_bytes,
        )

        artifact_ready = exit_code == 0 and _artifact_exists(container)

        return CompileResult(
            success=(exit_code == 0 and artifact_ready),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            compile_time_ms=(monotonic_ns() - started_ns) // 1_000_000,
            log_truncated=(stdout_truncated or stderr_truncated),
            artifact_ready=artifact_ready,
        )

    except docker.errors.ImageNotFound as exc:
        raise ContainerExecutionError(
            "컴파일용 Docker 이미지를 찾을 수 없습니다.",
            details={"image": settings.cpp_image},
        ) from exc
    except (WorkspaceError, ContainerExecutionError):
        raise
    except docker.errors.DockerException as exc:
        raise ContainerExecutionError(
            "컴파일 컨테이너 실행에 실패했습니다.",
            details={"reason": str(exc)},
        ) from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except docker.errors.DockerException:
                pass


def compile_cpp(workspace, code: str = "") -> CompileResult:
    """이전 외부 호출을 위한 C++ 호환 함수."""

    client = get_docker_client()
    return compile_source(
        client=client,
        workspace=workspace,
        language="CPP",
        code=code,
    )
