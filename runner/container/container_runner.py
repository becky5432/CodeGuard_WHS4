import logging
from dataclasses import dataclass
from uuid import UUID

import docker

from runner.config import settings
from runner.pipeline.workspace import VolumeWorkspace


logger = logging.getLogger("runner")


@dataclass
class ExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    system_error: str | None = None


def execute_program(
    client,
    workspace: VolumeWorkspace,
    stdin: str,
    job_id: UUID,
    run_id: UUID,
) -> ExecutionResult:
    """Volume에 있는 실행파일을 별도 컨테이너에서 실행한다."""

    container = None
    result: ExecutionResult | None = None

    try:
        command = ["/workspace/main"]
        if stdin:
            command = [
                "sh",
                "-c",
                "exec /workspace/main < /workspace/stdin",
            ]

        container = client.containers.create(
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
        result = ExecutionResult(
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
        result = ExecutionResult(
            exit_code=None,
            stdout="",
            stderr="",
            system_error="Execution Container 처리에 실패했습니다.",
        )
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except docker.errors.DockerException as exc:
                logger.error(
                    "event=execution_container_cleanup_failed "
                    "job_id=%s run_id=%s error=%s",
                    job_id,
                    run_id,
                    exc,
                )
                if result is None:
                    result = ExecutionResult(
                        exit_code=None,
                        stdout="",
                        stderr="",
                    )
                result.system_error = "Execution Container 정리에 실패했습니다."

    return result
