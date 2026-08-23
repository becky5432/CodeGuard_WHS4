from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.config import POLICY_PRESETS

from app.db import repository
from app.db.database import SessionLocal
from app.schemas.execution_schema import (
    ExecutionCreateRequest,
    ExecutionCreateResponse,
    ExecutionReasonCode,
    ExecutionResultResponse,
    ExecutionStage,
    ExecutionStatus,
    PolicyLimits,
    ResourceUsage,
    StageError,
    StageSummary as ExecutionStageSummary,
)
from app.schemas.runner_schema import (
    RunnerLanguage,
    RunnerRequest,
    RunnerResponse,
    RunnerStatus,
    StageSummary as RunnerStageSummary,
)


class ExecutionService:
    def __init__(self, runner_client):
        self.runner_client = runner_client

    def convert_stage_summary(
        self,
        summary: RunnerStageSummary,
    ) -> ExecutionStageSummary:
        return ExecutionStageSummary.model_validate(
            summary.model_dump(mode="json")
        )

    # 정책 프리셋 설정파일에서 제한값 가져옴
    def resolve_policy(
        self,
        request: ExecutionCreateRequest,
    ) -> PolicyLimits:
        policy_values = POLICY_PRESETS.get(
            request.policy_profile.value
        )

        if policy_values is None:
            raise ValueError(
                f"지원하지 않는 정책 프로파일입니다: "
                f"{request.policy_profile.value}"
            )

        return PolicyLimits.model_validate(policy_values)     

    # 프론트의 실행 요청 접수
    def submit(
        self,
        request: ExecutionCreateRequest,
        db: Session,
    ) -> tuple[ExecutionCreateResponse, RunnerRequest]:
        job_id = uuid4()

        resolved_policy = self.resolve_policy(request)
        limits = resolved_policy.model_dump()

        try:
            execution = repository.create_execution(
                db=db,
                job_id=str(job_id),
                language=request.language.value,
                code=request.code,
                stdin=request.stdin,
                policy_profile=request.policy_profile.value,
                limits=limits,
            )
        except Exception:
            db.rollback()
            raise

        runner_request = RunnerRequest(
            job_id=job_id,
            language=RunnerLanguage(request.language.value),
            code=request.code,
            stdin=request.stdin,
            policy=resolved_policy,
            created_at=execution.created_at,
        )

        response = ExecutionCreateResponse(
            job_id=job_id,
            status=ExecutionStatus.PENDING,
        )

        return response, runner_request

    # submit() 이후 백그라운드에서 실행
    def process_execution(
        self,
        runner_request: RunnerRequest,
    ) -> None:
        db = SessionLocal()
        job_id = str(runner_request.job_id)

        try:
            execution = repository.update_status(
                db=db,
                job_id=job_id,
                status=ExecutionStatus.RUNNING.value,
            )

            if execution is None:
                return

            try:
                runner_response: RunnerResponse = (
                    self.runner_client.execute(runner_request)
                )
            except Exception:
                db.rollback()

                repository.save_result(
                    db=db,
                    job_id=job_id,
                    status=ExecutionStatus.ERROR.value,
                    reason_code=ExecutionReasonCode.INTERNAL_ERROR.value,
                    error_message="Runner 서버로부터 실행 결과를 "
                            "받지 못했습니다.", 
                )

                return

            status_mapping = {
                RunnerStatus.SUCCESS: ExecutionStatus.SUCCESS,
                RunnerStatus.BLOCKED: ExecutionStatus.BLOCKED,
                RunnerStatus.ERROR: ExecutionStatus.ERROR,
            }

            mapped_status = status_mapping.get(runner_response.status)

            if mapped_status is None:
                raise ValueError(
                    f"지원하지 않는 Runner 상태입니다: "
                    f"{runner_response.status}"
                )

            usage = runner_response.resource_usage

            stage_summary = self.convert_stage_summary(
                runner_response.stage_summary
            )

            repository.save_result(
                db=db,
                job_id=job_id,
                status=mapped_status.value,
                reason_code=(
                    runner_response.reason_code.value
                    if runner_response.reason_code
                    else None
                ),
                error_message=runner_response.error_message,
                run_id=str(runner_response.run_id),
                exit_code=runner_response.exit_code,
                stdout=runner_response.stdout,
                stderr=runner_response.stderr,
                compile_log=runner_response.compile_log,
                stage_summary=stage_summary.model_dump(mode="json"),
                finished_at=runner_response.finished_at,
                wall_time_ms=(
                    usage.wall_time_ms
                    if usage
                    else None
                ),
                cpu_time_ms=(
                    usage.cpu_time_ms
                    if usage
                    else None
                ),
                memory_peak_bytes=(
                    usage.memory_peak_bytes
                    if usage
                    else None
                ),
                pids_peak=(
                    usage.pids_peak
                    if usage
                    else None
                ),
            )

        # DB 오류, 응답 변환 오류 등 Backend 내부 오류
        except Exception:
            db.rollback()

            try:
                repository.save_result(
                    db=db,
                    job_id=job_id,
                    status=ExecutionStatus.ERROR.value,
                    reason_code=ExecutionReasonCode.INTERNAL_ERROR.value,
                    error_message="Backend 내부 오류가 발생했습니다.",
                )
            except Exception:
                db.rollback()

            raise

        finally:
            db.close()

    # DB에서 실행 상태와 결과 조회
    def get_execution(
        self,
        job_id: str,
        db: Session,
    ) -> ExecutionResultResponse | None:
        execution = repository.get_execution(
            db=db,
            job_id=job_id,
        )

        if execution is None:
            return None

        metric_values = (
            execution.wall_time_ms,
            execution.cpu_time_ms,
            execution.memory_peak_bytes,
            execution.pids_peak,
        )

        resource_usage = (
            ResourceUsage(
                wall_time_ms=execution.wall_time_ms,
                cpu_time_ms=execution.cpu_time_ms,
                memory_peak_bytes=execution.memory_peak_bytes,
                pids_peak=execution.pids_peak,
            )
            if any(value is not None for value in metric_values)
            else None
        )

        stage_summary = (
            ExecutionStageSummary.model_validate(
                execution.stage_summary
            )
            if execution.stage_summary
            else None
        )

        return ExecutionResultResponse(
            job_id=UUID(execution.job_id),
            status=ExecutionStatus(execution.status),
            reason_code=(
                ExecutionReasonCode(execution.reason_code)
                if execution.reason_code
                else None
            ),
            error_message=execution.error_message,
            exit_code=execution.exit_code,
            stdout=execution.stdout,
            stderr=execution.stderr,
            compile_log=execution.compile_log,
            resource_usage=resource_usage,
            stage_summary=stage_summary,
            finished_at=execution.finished_at,
        )