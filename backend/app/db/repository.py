
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import Execution


def create_execution(
    db: Session,
    job_id: str,
    language: str,
    code: str,
    stdin: str,
    policy_profile: str,
    limits: dict,
) -> Execution:
    """실행 요청을 PENDING 상태로 저장"""
    execution = Execution(
        job_id=job_id,
        language=language,
        code=code,
        stdin=stdin,
        status="PENDING",
        policy_profile=policy_profile,
        timeout_ms=limits["timeout_ms"],
        memory_limit_mb=limits["memory_limit_mb"],
        process_limit=limits["process_limit"],
        cpu_limit=limits["cpu_limit"],
    )

    db.add(execution)
    db.commit()
    db.refresh(execution)
    return execution


def get_execution(db: Session, job_id: str) -> Execution | None:
    """job_id로 실행 기록 하나를 조회, 없으면 None"""
    return db.query(Execution).filter(Execution.job_id == job_id).first()


def list_executions(
    db: Session,
    limit: int = 20,
    offset: int = 0,
) -> list[Execution]:
    """실행 기록을 최신순으로 조회"""
    return (
        db.query(Execution)
        .order_by(Execution.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def update_status(db: Session, job_id: str, status: str) -> Execution | None:
    """실행 상태만 변경 (예: PENDING → RUNNING)"""
    execution = get_execution(db, job_id)
    if execution is None:
        return None

    execution.status = status
    db.commit()
    db.refresh(execution)
    return execution


def save_result(
    db: Session,
    job_id: str,
    status: str,
    reason_code: str | None = None,
    run_id: str | None = None,
    exit_code: int | None = None,
    stdout: str = "",
    stderr: str = "",
    compile_log: str = "",
    stage: str | None = None,
    error_message: str | None = None,
) -> Execution | None:
    """Runner 결과를 실행 기록에 반영하고 최종 상태로 갱신

    Runner가 응답하지 않은 경우에도 사용
    (status="ERROR", reason_code="INTERNAL_ERROR")
    """
    execution = get_execution(db, job_id)
    if execution is None:
        return None

    limit = settings.MAX_SAVED_OUTPUT_BYTES

    execution.run_id = run_id
    execution.status = status
    execution.reason_code = reason_code
    execution.exit_code = exit_code
    execution.stdout = (stdout or "")[:limit]
    execution.stderr = (stderr or "")[:limit]
    execution.compile_log = (compile_log or "")[:limit]
    execution.finished_at = datetime.now(timezone.utc)
    execution.stage = stage
    execution.error_message = error_message

    
    db.commit()
    db.refresh(execution)
    return execution