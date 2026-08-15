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
from runner.pipeline.classifier import classify_execution
from runner.pipeline.compiler import (
    compile_source,
    create_compile_container,
    get_docker_client,
)
from runner.pipeline.execution import (
    create_execution_container,
    execute_program,
)
from runner.pipeline.workspace import create_workspace, remove_workspace


logger = logging.getLogger("runner")


def _compile_log(stdout: str, stderr: str) -> str:
    return "\n".join(value for value in (stdout, stderr) if value)


def _remove_container(container, stage: str, job_id, run_id) -> bool:
    if container is None:
        return True

    try:
        container.remove(force=True)
        return True
    except Exception as exc:
        logger.error(
            "event=container_cleanup_error "
            "job_id=%s run_id=%s stage=%s error=%s",
            job_id,
            run_id,
            stage,
            exc,
        )
        return False


def execute_job(job: RunnerRequest) -> RunnerResponse:
    """컴파일과 실행을 수행하고 생성한 Docker 자원을 최종 정리한다."""

    run_id = uuid4()
    client = None
    workspace = None
    compile_container = None
    execution_container = None
    response = None
    compile_log = ""
    current_stage = RunnerStage.WORKSPACE
    cleanup_failed = False

    try:
        client = get_docker_client()
        workspace = create_workspace(client, job.job_id)

        current_stage = RunnerStage.COMPILE
        compile_container = create_compile_container(
            client=client,
            workspace=workspace,
            language=job.language,
        )
        compile_result = compile_source(
            container=compile_container,
            workspace=workspace,
            language=job.language,
            code=job.code,
            stdin=job.stdin,
        )
        compile_log = _compile_log(
            compile_result.stdout,
            compile_result.stderr,
        )

        if compile_result.exit_code == 0 and not compile_result.artifact_ready:
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.INTERNAL_ERROR,
                stage=RunnerStage.COMPILE,
                error_message="컴파일 실행 파일을 확인하지 못했습니다.",
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
            current_stage = RunnerStage.EXECUTE
            execution_container = create_execution_container(
                client=client,
                workspace=workspace,
                stdin=job.stdin,
                job_id=job.job_id,
                run_id=run_id,
            )
            execution_result = execute_program(
                container=execution_container,
                job_id=job.job_id,
                run_id=run_id,
            )
            classification = classify_execution(execution_result)
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=classification.status,
                reason_code=classification.reason_code,
                stage=classification.stage,
                error_message=classification.error_message,
                exit_code=execution_result.exit_code,
                stdout=execution_result.stdout,
                stderr=execution_result.stderr,
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
            compile_log=compile_log,
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
            compile_log=compile_log,
        )
    finally:
        if not _remove_container(
            execution_container,
            "execute",
            job.job_id,
            run_id,
        ):
            cleanup_failed = True

        if not _remove_container(
            compile_container,
            "compile",
            job.job_id,
            run_id,
        ):
            cleanup_failed = True

        if client is not None and workspace is not None:
            try:
                remove_workspace(client, workspace)
            except RunnerError as exc:
                cleanup_failed = True
                logger.error(
                    "event=volume_cleanup_error "
                    "job_id=%s run_id=%s code=%s message=%s details=%s",
                    job.job_id,
                    run_id,
                    exc.error_code,
                    exc.message,
                    exc.details,
                )

        if cleanup_failed:
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.INTERNAL_ERROR,
                stage=RunnerStage.CLEANUP,
                error_message="실행 환경 정리에 실패했습니다.",
                compile_log="",
            )

    if response is None:
        response = RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=RunnerStatus.ERROR,
            reason_code=RunnerReasonCode.INTERNAL_ERROR,
            stage=current_stage,
            error_message="Runner 결과를 생성하지 못했습니다.",
            compile_log="",
        )

    response.finished_at = datetime.now(timezone.utc)
    return response


def execute_compile_job(job: RunnerRequest) -> RunnerResponse:
    """기존 API 호출과의 호환성을 위한 별칭."""

    return execute_job(job)
