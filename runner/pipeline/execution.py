import logging
from dataclasses import dataclass
from uuid import UUID

import docker

from runner.config import settings
from runner.exceptions import ContainerExecutionError
from runner.pipeline.workspace import VolumeWorkspace


logger = logging.getLogger("runner")


@dataclass
class ExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    system_error: str | None = None


def create_execution_container(
    client,
    workspace: VolumeWorkspace,
    stdin: str,
    job_id: UUID,
    run_id: UUID,
):
    """Job Volume을 연결한 실행 컨테이너를 생성하고 반환한다."""

    command = ["/workspace/main"]
    if stdin:
        command = [
            "sh",
            "-c",
            "exec /workspace/main < /workspace/stdin",
        ]

    try:
        return client.containers.create(
            image=settings.cpp_image,
            command=command,
            volumes={
                workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "rw",
                }
            },
            detach=True,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(job_id),
                "codeguard.run_id": str(run_id),
                "codeguard.stage": "execute",
            },
        )
    except docker.errors.DockerException as exc:
        raise ContainerExecutionError(
            "실행 컨테이너 생성에 실패했습니다.",
            details={"reason": str(exc)},
        ) from exc


def execute_program(container, job_id: UUID, run_id: UUID) -> ExecutionResult:
    """실행 컨테이너를 시작하고 종료 정보와 출력을 수집한다."""

    try:
        container.start()
        wait_result = container.wait()
        exit_code = int(wait_result["StatusCode"])
        stdout = container.logs(stdout=True, stderr=False).decode(
            "utf-8",
            errors="replace",
        )
        stderr = container.logs(stdout=False, stderr=True).decode(
            "utf-8",
            errors="replace",
        )
        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
        )
    except docker.errors.DockerException as exc:
        logger.error(
            "event=execution_container_error job_id=%s run_id=%s error=%s",
            job_id,
            run_id,
            exc,
        )
        return ExecutionResult(
            exit_code=None,
            stdout="",
            stderr="",
            system_error="실행 컨테이너 처리에 실패했습니다.",
        )
