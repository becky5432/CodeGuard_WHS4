from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
)
from sqlalchemy.orm import Session
from uuid import UUID

from app.clients.runner_client import MockRunnerClient
from app.db.database import get_db
from app.schemas.execution_schema import (
    ExecutionCreateRequest,
    ExecutionCreateResponse,
    ExecutionResultResponse,
)
from app.services.execution_service import ExecutionService


router = APIRouter(
    prefix="/executions",
    tags=["executions"],
)

execution_service = ExecutionService(
    runner_client=MockRunnerClient(),
)


@router.post(
    "",
    response_model=ExecutionCreateResponse,
    status_code=202,
)
def create_execution(
    request: ExecutionCreateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    # 실행 요청 접수 및 DB PENDING 저장
    response, runner_request = execution_service.submit(
        request=request,
        db=db,
    )

    # POST 응답 이후 백그라운드에서 Runner 실행
    background_tasks.add_task(
        execution_service.process_execution,
        runner_request,
    )

    return response


@router.get(
    "/{job_id}",
    response_model=ExecutionResultResponse,
)
def get_execution(
    job_id: UUID,
    db: Session = Depends(get_db),
):
    result = execution_service.get_execution(
        job_id=str(job_id),
        db=db,
    )

    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Execution not found",
        )

    return result