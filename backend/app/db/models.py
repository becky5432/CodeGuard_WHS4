from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Float, Integer, String, Text

from app.db.database import Base


class Execution(Base):
    __tablename__ = "executions"

    # --- 접수 시 저장 ---
    job_id = Column(String(36), primary_key=True)              # 백엔드 발급 UUID
    language = Column(String(8), nullable=False)               # C / CPP
    code = Column(Text, nullable=False)                        # 입력 검증: 64KB 상한
    stdin = Column(Text, default="", nullable=False)           # 입력 검증: 10KB 상한
    status = Column(String(16), nullable=False, default="PENDING")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # --- 적용 정책 (실행 시점 스냅샷) ---
    policy_profile = Column(String(16), default="basic")       # basic / strict / relaxed
    timeout_ms = Column(Integer)                               # 최대 실행 시간(ms)
    memory_limit_mb = Column(Integer)                          # 최대 메모리(MB)
    pids_limit = Column(Integer)                               # 최대 프로세스·스레드 수
    cpu_limit = Column(Float)                                  # CPU 코어 수

    # --- Runner 결과 수신 후 갱신 ---
    run_id = Column(String(36))                                # Runner 발급 실행 ID
    reason_code = Column(String(32))                           # COMPILE_ERROR / RUNTIME_ERROR ...
    error_message = Column(String(512))
    exit_code = Column(Integer)
    stdout = Column(Text, default="")                          # 저장 시 64KB 절단
    stderr = Column(Text, default="")                          # 저장 시 64KB 절단
    compile_log = Column(Text, default="")                     # 저장 시 64KB 절단
    stage_summary = Column(JSON)                               # 단계별 성공·실패·오류
    finished_at = Column(DateTime(timezone=True))

    # --- 자원 사용량 (제한값 대비 비교용) ---
    wall_time_ms = Column(Integer)                             # 전체 실행 시간 ↔ timeout_ms
    cpu_time_ms = Column(Integer)                              # CPU 누적 사용 시간 (제한 없음, 참고 지표)
    memory_peak_bytes = Column(Integer)                        # 최대 메모리 ↔ memory_limit_mb
    pids_peak = Column(Integer)                                # 최대 프로세스·스레드 수 ↔ pids_limit