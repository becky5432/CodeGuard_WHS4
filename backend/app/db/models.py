from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Float, Integer, String, Text

from app.db.database import Base


class Execution(Base):
    __tablename__ = "executions"

    # --- 접수 시 저장 ---
    job_id = Column(String, primary_key=True)              # 백엔드 발급 UUID
    language = Column(String, nullable=False)              # C / CPP
    code = Column(Text, nullable=False)
    stdin = Column(Text, default="", nullable=False)
    status = Column(String, nullable=False, default="PENDING")
    created_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- 적용 정책 (실행 시점 스냅샷) ---
    policy_profile = Column(String, default="basic")       # basic / strict / relaxed
    # timeout_ms = Column(Integer)
    # memory_limit_mb = Column(Integer)
    # process_limit = Column(Integer)
    # cpu_limit = Column(Float)

    # --- Runner 결과 수신 후 갱신 ---
    run_id = Column(String)                                # Runner 발급 실행 ID
    reason_code = Column(String)                           # TIME_LIMIT / MEMORY_LIMIT / ...
    exit_code = Column(Integer)
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    finished_at = Column(DateTime)

    # TODO: compile_log,
    # wall_time_ms / cpu_time_ms / memory_peak_bytes / process_peak