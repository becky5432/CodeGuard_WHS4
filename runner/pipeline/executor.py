import logging
from datetime import datetime, timezone
from uuid import uuid4

from runner.config import settings
from runner.exceptions import RunnerError, WorkspaceError
from runner.metrics.cgroup_scope import (
    ExecutionCgroupScope,
    validate_docker_cgroup_driver,
)
from runner.models.job import RunnerRequest
from runner.models.result import (
    RunnerReasonCode,
    RunnerResponse,
    ResourceUsage,
    RunnerStage,
    RunnerStatus,
    StageError,
    StageSummary,
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


def _append_stage(stages: list[RunnerStage], stage: RunnerStage) -> None:
    if stage not in stages:
        stages.append(stage)


def _mark_succeeded(summary: StageSummary, stage: RunnerStage) -> None:
    _append_stage(summary.succeeded, stage)


def _mark_skipped(summary: StageSummary, stage: RunnerStage) -> None:
    _append_stage(summary.skipped, stage)


def _mark_failed(
    summary: StageSummary,
    stage: RunnerStage,
    reason_code: RunnerReasonCode,
    message: str,
) -> None:
    _append_stage(summary.failed, stage)
    summary.errors.setdefault(stage, []).append(
        StageError(
            reason_code=reason_code,
            message=message,
        )
    )


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
    execution_cgroup_scope = None
    response = None
    compile_log = ""
    current_stage = RunnerStage.WORKSPACE
    stage_summary = StageSummary()
    cleanup_failed = False

    try:
        client = get_docker_client()
        workspace = create_workspace(client, job.job_id)
        _mark_succeeded(stage_summary, RunnerStage.WORKSPACE)

        # -------------------------
        # Compile
        # -------------------------
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

        if compile_result.timed_out:
            message = "컴파일 시간 제한을 초과했습니다."
            _mark_failed(
                stage_summary,
                RunnerStage.COMPILE,
                RunnerReasonCode.COMPILE_TIMEOUT,
                message,
            )
            _mark_skipped(stage_summary, RunnerStage.EXECUTE)
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.COMPILE_TIMEOUT,
                error_message=message,
                exit_code=compile_result.exit_code,
                compile_log=compile_log,
            )

        # exit code는 성공인데 실행 파일이 생성되지 않은 경우
        elif compile_result.exit_code == 0 and not compile_result.artifact_ready:
            message = "컴파일 실행 파일을 확인하지 못했습니다."
            _mark_failed(
                stage_summary,
                RunnerStage.COMPILE,
                RunnerReasonCode.INTERNAL_ERROR,
                message,
            )
            _mark_skipped(stage_summary, RunnerStage.EXECUTE)
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.INTERNAL_ERROR,
                error_message=message,
                exit_code=compile_result.exit_code,
                compile_log=compile_log,
            )

        # 컴파일 실패
        elif not compile_result.success:
            message = "소스 코드 컴파일에 실패했습니다."
            _mark_failed(
                stage_summary,
                RunnerStage.COMPILE,
                RunnerReasonCode.COMPILE_ERROR,
                message,
            )
            _mark_skipped(stage_summary, RunnerStage.EXECUTE)
            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=RunnerStatus.ERROR,
                reason_code=RunnerReasonCode.COMPILE_ERROR,
                error_message=message,
                exit_code=compile_result.exit_code,
                compile_log=compile_log,
            )

        # -------------------------
        # Execute
        # -------------------------
        else:
            _mark_succeeded(stage_summary, RunnerStage.COMPILE)
            current_stage = RunnerStage.EXECUTE

            if settings.execution_cgroup_enabled:
                cgroup_driver = validate_docker_cgroup_driver(client)
                execution_cgroup_scope = ExecutionCgroupScope.create(
                    root=settings.execution_cgroup_root,
                    run_id=run_id,
                    driver=cgroup_driver,
                )

            create_execution_options = {
                "client": client,
                "workspace": workspace,
                "stdin": job.stdin,
                "job_id": job.job_id,
                "run_id": run_id,
                "memory_limit_mb": job.policy.memory_limit_mb,
                "cpu_limit": job.policy.cpu_limit,
                "pids_limit": job.policy.pids_limit,
            }
            if execution_cgroup_scope is not None:
                create_execution_options["cgroup_scope"] = (
                    execution_cgroup_scope
                )

            execution_container = create_execution_container(
                **create_execution_options,
            )

            execute_options = {
                "container": execution_container,
                "job_id": job.job_id,
                "run_id": run_id,
                "timeout_ms": job.policy.timeout_ms,
                "output_limit_bytes": job.policy.output_limit_bytes,
            }
            if execution_cgroup_scope is not None:
                execute_options["cgroup_scope"] = execution_cgroup_scope

            execution_result = execute_program(**execute_options)

            classification = classify_execution(execution_result)

            if classification.status == RunnerStatus.SUCCESS:
                _mark_succeeded(stage_summary, RunnerStage.EXECUTE)
            else:
                reason_code = (
                    classification.reason_code
                    or RunnerReasonCode.INTERNAL_ERROR
                )
                message = (
                    classification.error_message
                    or "프로그램 실행 단계에서 오류가 발생했습니다."
                )
                _mark_failed(
                    stage_summary,
                    RunnerStage.EXECUTE,
                    reason_code,
                    message,
                )

            response = RunnerResponse(
                job_id=job.job_id,
                run_id=run_id,
                status=classification.status,
                reason_code=classification.reason_code,
                error_message=classification.error_message,
                exit_code=execution_result.exit_code,
                stdout=execution_result.stdout,
                stderr=execution_result.stderr,
                compile_log=compile_log,
                resource_usage=ResourceUsage(
                    wall_time_ms=execution_result.wall_time_ms,
                    memory_peak_bytes=(
                        execution_result.memory_peak_bytes
                    ),
                    pids_peak=execution_result.pids_peak,
                ),
            )

    # Runner에서 정의한 예외
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

        _mark_failed(
            stage_summary,
            error_stage,
            RunnerReasonCode.INTERNAL_ERROR,
            exc.message,
        )
        if error_stage == RunnerStage.WORKSPACE:
            _mark_skipped(stage_summary, RunnerStage.COMPILE)
        if error_stage in {RunnerStage.WORKSPACE, RunnerStage.COMPILE}:
            _mark_skipped(stage_summary, RunnerStage.EXECUTE)

        response = RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=RunnerStatus.ERROR,
            reason_code=RunnerReasonCode.INTERNAL_ERROR,
            error_message=exc.message,
            compile_log=compile_log,
        )

    # 예상하지 못한 예외
    except Exception:
        logger.exception(
            "event=runner_unexpected_error job_id=%s run_id=%s stage=%s",
            job.job_id,
            run_id,
            current_stage.value,
        )

        message = "Runner 내부 오류가 발생했습니다."
        _mark_failed(
            stage_summary,
            current_stage,
            RunnerReasonCode.INTERNAL_ERROR,
            message,
        )
        if current_stage == RunnerStage.WORKSPACE:
            _mark_skipped(stage_summary, RunnerStage.COMPILE)
        if current_stage in {RunnerStage.WORKSPACE, RunnerStage.COMPILE}:
            _mark_skipped(stage_summary, RunnerStage.EXECUTE)

        response = RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=RunnerStatus.ERROR,
            reason_code=RunnerReasonCode.INTERNAL_ERROR,
            error_message=message,
            compile_log=compile_log,
        )

    # -------------------------
    # Cleanup
    # -------------------------
    finally:
        # Execution Container 삭제
        if not _remove_container(
            execution_container,
            "execute",
            job.job_id,
            run_id,
        ):
            cleanup_failed = True
            _mark_failed(
                stage_summary,
                RunnerStage.CLEANUP,
                RunnerReasonCode.INTERNAL_ERROR,
                "Execution Container 정리에 실패했습니다.",
            )

        # Compile Container 삭제
        if not _remove_container(
            compile_container,
            "compile",
            job.job_id,
            run_id,
        ):
            cleanup_failed = True
            _mark_failed(
                stage_summary,
                RunnerStage.CLEANUP,
                RunnerReasonCode.INTERNAL_ERROR,
                "Compile Container 정리에 실패했습니다.",
            )

        # Job Volume 삭제
        if client is not None and workspace is not None:
            try:
                remove_workspace(client, workspace)

            except RunnerError as exc:
                cleanup_failed = True
                _mark_failed(
                    stage_summary,
                    RunnerStage.CLEANUP,
                    RunnerReasonCode.INTERNAL_ERROR,
                    exc.message,
                )

                logger.error(
                    "event=volume_cleanup_error "
                    "job_id=%s run_id=%s code=%s message=%s details=%s",
                    job.job_id,
                    run_id,
                    exc.error_code,
                    exc.message,
                    exc.details,
                )

        # Peak 회수가 끝난 Execution 전용 cgroup을 마지막으로 삭제한다.
        if execution_cgroup_scope is not None:
            try:
                execution_cgroup_scope.remove()
            except RunnerError as exc:
                cleanup_failed = True
                _mark_failed(
                    stage_summary,
                    RunnerStage.CLEANUP,
                    RunnerReasonCode.INTERNAL_ERROR,
                    exc.message,
                )
                logger.error(
                    "event=execution_cgroup_cleanup_error "
                    "job_id=%s run_id=%s code=%s message=%s details=%s",
                    job.job_id,
                    run_id,
                    exc.error_code,
                    exc.message,
                    exc.details,
                )

        if not cleanup_failed:
            _mark_succeeded(stage_summary, RunnerStage.CLEANUP)

    # 예외적인 상황에서 response가 생성되지 않은 경우
    if response is None:
        message = "Runner 결과를 생성하지 못했습니다."
        _mark_failed(
            stage_summary,
            current_stage,
            RunnerReasonCode.INTERNAL_ERROR,
            message,
        )
        response = RunnerResponse(
            job_id=job.job_id,
            run_id=run_id,
            status=RunnerStatus.ERROR,
            reason_code=RunnerReasonCode.INTERNAL_ERROR,
            error_message=message,
            compile_log="",
        )

    response.stage_summary = stage_summary
    response.finished_at = datetime.now(timezone.utc)

    return response


def execute_compile_job(job: RunnerRequest) -> RunnerResponse:
    """기존 API 호출과의 호환성을 위한 별칭."""

    return execute_job(job)
