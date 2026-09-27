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
    limits: dict,
) -> Execution:
    """실행 요청을 PENDING 상태로 저장"""
    execution = Execution(
        job_id=job_id,
        language=language,
        code=code,
        stdin=stdin,
        status="PENDING",
        timeout_ms=limits["timeout_ms"],
        memory_limit_mb=limits["memory_limit_mb"],
        pids_limit=limits["pids_limit"],
        cpu_bandwidth=limits["cpu_bandwidth"],
        cpu_time_limit_ms=limits["cpu_time_limit_ms"],
        output_limit_bytes=limits["output_limit_bytes"],
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


def _truncate_bytes(value: str | None, limit: int) -> str:
    """UTF-8 바이트 기준으로 절단한다.

    문자 수가 아니라 바이트 수로 잘라, 러너의 출력 제한(바이트 기준)과
    단위를 맞춘다. 멀티바이트 글자가 경계에서 잘려도 깨진 조각은 버린다.
    """
    encoded = (value or "").encode("utf-8")
    if len(encoded) <= limit:
        return value or ""
    return encoded[:limit].decode("utf-8", errors="ignore")


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
    stage_summary: dict | None = None,
    error_message: str | None = None,
    wall_time_ms: int | None = None,
    cpu_time_ms: int | None = None,
    memory_peak_bytes: int | None = None,
    pids_peak: int | None = None,
    output_bytes: int | None = None,
    cpu_usage_samples: list[dict[str, int]] | None = None,
    memory_usage_samples: list[dict[str, int]] | None = None,
    user_task_peak: int | None = None,
    process_at_user_task_peak: int | None = None,
    thread_at_user_task_peak: int | None = None,
    finished_at: datetime | None = None,
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
    execution.error_message = error_message
    execution.exit_code = exit_code
    execution.stdout = _truncate_bytes(stdout, limit)
    execution.stderr = _truncate_bytes(stderr, limit)
    execution.compile_log = _truncate_bytes(compile_log, limit)
    execution.stage_summary = stage_summary
    execution.finished_at = finished_at or datetime.now(timezone.utc)

    execution.wall_time_ms = wall_time_ms
    execution.cpu_time_ms = cpu_time_ms
    execution.memory_peak_bytes = memory_peak_bytes
    execution.pids_peak = pids_peak
    execution.output_bytes = output_bytes
    execution.cpu_usage_samples = cpu_usage_samples
    execution.memory_usage_samples = memory_usage_samples
    execution.user_task_peak = user_task_peak
    execution.process_at_user_task_peak = process_at_user_task_peak
    execution.thread_at_user_task_peak = thread_at_user_task_peak

    db.commit()
    db.refresh(execution)
    return execution
