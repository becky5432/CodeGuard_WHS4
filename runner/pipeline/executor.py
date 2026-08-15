import logging
from datetime import datetime, timezone
from uuid import uuid4

from runner.exceptions import RunnerError
from runner.models.job import RunnerRequest
from runner.models.result import (
    RunnerReasonCode,
    RunnerResponse,
    RunnerStage,
    RunnerStatus,
)
from runner.pipeline.compiler import compile_source
from runner.pipeline.workspace import create_workspace, remove_workspace, write_source


logger = logging.getLogger("runner")


def execute_compile_job(job: RunnerRequest) -> RunnerResponse:
    """Workspace 생성부터 Docker C/C++ 컴파일과 정리까지 수행한다."""

    run_id = uuid4()
    workspace = None
    response = None
    current_stage = RunnerStage.WORKSPACE

    try:
        workspace = create_workspace(job.job_id)

        write_source(
            workspace=workspace,
            language=job.language,
            code=job.code,
        )

        current_stage = RunnerStage.COMPILE
        compile_result = compile_source(
            workspace=workspace,
            language=job.language,
        )

        if compile_result.timed_out:
            status = RunnerStatus.BLOCKED
            reason_code = RunnerReasonCode.COMPILE_TIMEOUT
            stage = RunnerStage.COMPILE
            error_message = "컴파일 제한 시간을 초과했습니다."

        elif not compile_result.success:
            status = RunnerStatus.ERROR
            reason_code = RunnerReasonCode.COMPILE_ERROR
            stage = RunnerStage.COMPILE
            error_message = "소스 코드 컴파일에 실패했습니다."

        else:
            status = RunnerStatus.SUCCESS
            reason_code = None
            stage = None
            error_message = None

        compile_log = "\n".join(
            value
            for value in (compile_result.stdout, compile_result.stderr)
            if value
        )

        response = RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=status,
            reason_code=reason_code,
            stage=stage,
            error_message=error_message,
            stdout="",
            stderr="",
            compile_log=compile_log,
            exit_code=compile_result.exit_code,
        )

    except RunnerError as exc:
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
            stage=current_stage,
            error_message=exc.message,
            stdout="",
            stderr="",
            compile_log="",
            exit_code=None,
        )

    if workspace is not None:
        try:
            remove_workspace(workspace)

        except RunnerError as exc:
            logger.error(
                "event=workspace_cleanup_error "
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
                stdout="",
                stderr="",
                compile_log="",
                exit_code=None,
            )

    response.finished_at = datetime.now(timezone.utc)
    return response
