from dataclasses import dataclass
from pathlib import Path

import docker
from requests.exceptions import ReadTimeout

from runner.config import settings
from runner.exceptions import (
    ContainerExecutionError,
    DockerUnavailableError,
    WorkspaceError,
)


@dataclass
class CompileResult:
    """C++ 컴파일 결과."""

    success: bool
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool = False


def get_docker_client():
    """Docker Engine에 연결된 클라이언트를 반환한다."""

    try:
        client = docker.from_env()
        client.ping()
        return client

    except docker.errors.DockerException as exc:
        raise DockerUnavailableError(
            "Docker Engine에 연결할 수 없습니다.",
            details={
                "reason": str(exc),
            },
        ) from exc


def compile_cpp(workspace: Path) -> CompileResult:
    """
    workspace 내부의 main.cpp 파일을 Docker 컨테이너에서 컴파일한다.

    성공하면 workspace/main 실행 파일이 생성된다.
    """

    source_path = workspace / "main.cpp"

    # 컴파일할 소스 파일이 실제로 존재하는지 확인
    if not source_path.is_file():
        raise WorkspaceError(
            "컴파일할 main.cpp 파일이 없습니다.",
            details={
                "path": str(source_path),
            },
        )

    client = get_docker_client()
    container = None

    try:
        # Compile Container 생성 및 실행
        container = client.containers.run(
            image=settings.cpp_image,
            command=[
                "g++",
                "/workspace/main.cpp",
                "-o",
                "/workspace/main",
            ],
            volumes={
                str(workspace.resolve()): {
                    "bind": "/workspace",
                    "mode": "rw",
                }
            },
            network_disabled=True,
            detach=True,
        )

        # 컴파일 종료 대기
        try:
            result = container.wait(
                timeout=settings.compile_timeout_seconds,
            )

        except ReadTimeout:
            # 제한 시간을 넘으면 컨테이너 강제 종료
            container.kill()

            return CompileResult(
                success=False,
                stdout="",
                stderr="Compilation timed out.",
                exit_code=None,
                timed_out=True,
            )

        exit_code = int(result["StatusCode"])

        # stdout 수집
        stdout = container.logs(
            stdout=True,
            stderr=False,
        ).decode(
            "utf-8",
            errors="replace",
        )

        # stderr 수집
        stderr = container.logs(
            stdout=False,
            stderr=True,
        ).decode(
            "utf-8",
            errors="replace",
        )

        return CompileResult(
            success=(exit_code == 0),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            timed_out=False,
        )

    except docker.errors.ImageNotFound as exc:
        raise ContainerExecutionError(
            "컴파일용 Docker 이미지를 찾을 수 없습니다.",
            details={
                "image": settings.cpp_image,
            },
        ) from exc

    except docker.errors.DockerException as exc:
        raise ContainerExecutionError(
            "컴파일 컨테이너 실행에 실패했습니다.",
            details={
                "reason": str(exc),
            },
        ) from exc

    finally:
        # 컴파일이 끝난 뒤 컨테이너 정리
        if container is not None:
            try:
                container.remove(force=True)

            except docker.errors.DockerException:
                pass
