from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
)
from sqlalchemy.orm import Session
from uuid import UUID


from app.clients.runner_client import HttpRunnerClient
from app.config import settings
from app.db.database import get_db
from app.schemas.execution_schema import (
    ExecutionCreateRequest,
    ExecutionCreateResponse,
    ExecutionResultResponse,
    ExecutionListItem
)
from app.services.execution_service import ExecutionService

# 실행 기록 조회를 위해 추가함
from app.db import repository
from app.schemas.execution_schema import ExecutionListItem

router = APIRouter(
    prefix="/executions",
    tags=["executions"],
)

execution_service = ExecutionService(
    runner_client=HttpRunnerClient(
        base_url=settings.RUNNER_URL,
        timeout=settings.RUNNER_TIMEOUT_SECONDS,
    ),
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

# 실행 기록 조회
@router.get(
    "",
    response_model=list[ExecutionListItem],
)
def list_executions(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    executions = repository.list_executions(
        db=db,
        limit=limit,
        offset=offset,
    )

    return [
        ExecutionListItem( # 실행 기록 목록에 전달하는 값 (필요하면 추가)
            job_id=execution.job_id,
            created_at=execution.created_at,
            language=execution.language,
            status=execution.status,
            reason_code=execution.reason_code,
        )
        for execution in executions
    ]


# 상세 조회
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
