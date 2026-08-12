import logging

from fastapi import APIRouter

from runner.models.job import RunnerRequest
from runner.models.result import RunnerResponse
from runner.pipeline.executor import execute_compile_job


router = APIRouter()
logger = logging.getLogger("runner")


@router.get("/health")
def health_check() -> dict[str, str]:
    logger.info("event=health_check")
    return {
        "status": "ok",
        "service": "runner",
    }


@router.post("/execute", response_model=RunnerResponse)
def execute(request: RunnerRequest) -> RunnerResponse:
    logger.info(
        "event=execute_requested job_id=%s language=%s",
        request.job_id,
        request.language.value,
    )

    result = execute_compile_job(request)

    logger.info(
        "event=execute_completed job_id=%s run_id=%s status=%s",
        result.job_id,
        result.run_id,
        result.status.value,
    )
    return result
