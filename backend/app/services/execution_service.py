from uuid import uuid4

from sqlalchemy.orm import Session

from app.clients.runner_client import MockRunnerClient
from app.db import repository
from app.db.database import SessionLocal
from app.schemas.execution_schema import (
    ExecutionCreateRequest,
    ExecutionCreateResponse,
    ExecutionResultResponse,
    ExecutionStatus,
)
from app.schemas.runner_schema import (
    RunnerLanguage,
    RunnerRequest,
    RunnerResponse,
    RunnerStatus,
)


class ExecutionService:
    def __init__(self, runner_client: MockRunnerClient):
        self.runner_client = runner_client

    # 프론트의 실행 요청 접수
    def submit(
        self,
        request: ExecutionCreateRequest,
        db: Session,
    ) -> tuple[ExecutionCreateResponse, RunnerRequest]:
        job_id = uuid4()

        # PolicyLimits 객체를 DB 저장용 dict로 변환
        limits = request.policy.model_dump()

        try:
            # DB에 PENDING 상태로 저장
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

        # DB에 저장된 created_at을 Runner에도 사용
        runner_request = RunnerRequest(
            job_id=job_id,
            language=RunnerLanguage(request.language.value),
            code=request.code,
            stdin=request.stdin,
            policy=request.policy,
            created_at=execution.created_at,
        )

        response = ExecutionCreateResponse(
            job_id=str(job_id),
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
            # PENDING → RUNNING
            execution = repository.update_status(
                db=db,
                job_id=job_id,
                status=ExecutionStatus.RUNNING.value,
            )

            if execution is None:
                return

            # Runner 호출
            runner_response: RunnerResponse = (
                self.runner_client.execute(runner_request)
            )

            # Runner 상태 → Backend 상태 매핑
            status_mapping = {
                RunnerStatus.SUCCESS: ExecutionStatus.SUCCESS,
                RunnerStatus.BLOCKED: ExecutionStatus.BLOCKED,
                RunnerStatus.ERROR: ExecutionStatus.ERROR,
            }

            mapped_status = status_mapping.get(
                runner_response.status,
            )

            if mapped_status is None:
                raise ValueError(
                    f"지원하지 않는 Runner 상태입니다: "
                    f"{runner_response.status}"
                )

            # 최종 결과 저장
            repository.save_result(
                db=db,
                job_id=job_id,
                status=mapped_status.value,
                reason_code=(
                    runner_response.reason_code.value
                    if runner_response.reason_code
                    else None
                ),
                stage=(
	                  runner_response.stage.value
	                  if runner_response.stage
	                  else None
	              ), 
	              error_message=runner_response.error_message, 
                run_id=str(runner_response.run_id),
                exit_code=runner_response.exit_code,
                stdout=runner_response.stdout,
                stderr=runner_response.stderr,
                compile_log=runner_response.compile_log,
            )

        except Exception:
            db.rollback()

            # Runner 호출 또는 내부 처리 실패
            try:
                repository.save_result(
                    db=db,
                    job_id=job_id,
                    status=ExecutionStatus.ERROR.value,
                    reason_code="INTERNAL_ERROR",
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

        return ExecutionResultResponse(
            job_id=execution.job_id,
            status=ExecutionStatus(execution.status),
            reason_code=execution.reason_code,
            stage=execution.stage,
            error_message=execution.error_message,
            exit_code=execution.exit_code,
            stdout=execution.stdout,
            stderr=execution.stderr,
        )