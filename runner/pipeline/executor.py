import logging
from datetime import datetime, timezone
from uuid import uuid4

from runner.exceptions import RunnerError, WorkspaceError
from runner.models.job import RunnerRequest
from runner.models.result import (
    RunnerReasonCode,
    RunnerResponse,
    RunnerStage,
    RunnerStatus,
)
from runner.pipeline.compiler import compile_source, get_docker_client
from runner.pipeline.workspace import create_workspace, remove_workspace


logger = logging.getLogger("runner")


def _compile_log(stdout: str, stderr: str) -> str:
    return "\n".join(value for value in (stdout, stderr) if value)


def execute_compile_job(job: RunnerRequest) -> RunnerResponse:
    """Job Volume 생성부터 C/C++ 컴파일과 최종 정리까지 수행한다."""

    run_id = uuid4()
    client = None
    workspace = None
    response = None
    current_stage = RunnerStage.WORKSPACE

    try:
        client = get_docker_client()
        workspace = create_workspace(client, job.job_id)

        current_stage = RunnerStage.COMPILE
        compile_result = compile_source(
            client=client,
            workspace=workspace,
            language=job.language,
            code=job.code,
        )
        compile_log = _compile_log(
            compile_result.stdout,
            compile_result.stderr,
        )

        if compile_result.timed_out:
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.BLOCKED,
                reason_code=RunnerReasonCode.COMPILE_TIMEOUT,
                stage=RunnerStage.COMPILE,
                error_message="컴파일 제한 시간을 초과했습니다.",
                exit_code=None,
                compile_log=compile_log,
            )
        elif compile_result.exit_code == 0 and not compile_result.artifact_ready:
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.INTERNAL_ERROR,
                stage=RunnerStage.COMPILE,
                error_message="컴파일 실행파일을 확인하지 못했습니다.",
                exit_code=compile_result.exit_code,
                compile_log=compile_log,
            )
        elif not compile_result.success:
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.COMPILE_ERROR,
                stage=RunnerStage.COMPILE,
                error_message="소스 코드 컴파일에 실패했습니다.",
                exit_code=compile_result.exit_code,
                compile_log=compile_log,
            )
        else:
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.SUCCESS,
                exit_code=compile_result.exit_code,
                compile_log=compile_log,
            )

    except RunnerError as exc:
        error_stage = (
            RunnerStage.WORKSPACE
            if isinstance(exc, WorkspaceError)
            else current_stage
        )
        logger.error(
            "event=runner_internal_error "
            "job_id=%s run_id=%s code=%s message=%s details=%s",
            job.job_id,
            run_id,
            exc.error_code,
            exc.message,
            exc.details,
        )
        response = RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=RunnerStatus.ERROR,
            reason_code=RunnerReasonCode.INTERNAL_ERROR,
            stage=error_stage,
            error_message=exc.message,
            compile_log="",
        )
    except Exception:
        logger.exception(
            "event=runner_unexpected_error job_id=%s run_id=%s stage=%s",
            job.job_id,
            run_id,
            current_stage.value,
        )
        response = RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=RunnerStatus.ERROR,
            reason_code=RunnerReasonCode.INTERNAL_ERROR,
            stage=current_stage,
            error_message="Runner 내부 오류가 발생했습니다.",
            compile_log="",
        )

    if client is not None and workspace is not None:
        try:
            remove_workspace(client, workspace)
        except RunnerError as exc:
            logger.error(
                "event=volume_cleanup_error "
                "job_id=%s run_id=%s code=%s message=%s details=%s",
                job.job_id,
                run_id,
                exc.error_code,
                exc.message,
                exc.details,
            )
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.INTERNAL_ERROR,
                stage=RunnerStage.CLEANUP,
                error_message="실행 환경 정리에 실패했습니다.",
                compile_log="",
            )

    response.finished_at = datetime.now(timezone.utc)
    return response
