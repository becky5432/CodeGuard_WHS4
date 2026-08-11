import logging

from fastapi import APIRouter


router = APIRouter()
logger = logging.getLogger("runner")


@router.get("/health")
def health_check() -> dict[str, str]:
    logger.info("event=health_check")
    return {
        "status": "ok",
        "service": "runner",
    }
