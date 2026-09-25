from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class Language(str, Enum):
    C = "C"
    CPP = "CPP"


class PolicyLimits(BaseModel):
    # TODO: Runner와 실제 필드/단위/상한 확정 후 범위 검증 추가
    timeout_ms: int = Field(gt=0)
    memory_limit_mb: int = Field(gt=0)
    pids_limit: int = Field(gt=0)     # 프로세스 및 스레드 수 함께 제한
    cpu_bandwidth: float = Field(gt=0)    # CPU 처리량 한도 (quota 제한)
    cpu_time_limit_ms: int = Field(gt=0)    # CPU 시간 제한
    output_limit_bytes: int = Field(gt=0)   # 실행 단계 stdout·stderr 합산 출력 제한



class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"


class ExecutionStage(str, Enum):
    WORKSPACE = "WORKSPACE"
    COMPILE = "COMPILE"
    EXECUTE = "EXECUTE"
    CLEANUP = "CLEANUP"


class ExecutionReasonCode(str, Enum):
    TIME_LIMIT = "TIME_LIMIT"
    CPU_TIME_LIMIT = "CPU_TIME_LIMIT"  # CPU 시간 제한
    MEMORY_LIMIT = "MEMORY_LIMIT"
    PIDS_LIMIT = "PIDS_LIMIT"      # 프로세스 + 스레드 수 제한
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    FILESYSTEM_LIMIT = "FILESYSTEM_LIMIT"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    COMPILE_ERROR = "COMPILE_ERROR"
    COMPILE_TIMEOUT = "COMPILE_TIMEOUT"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    SECURITY_VERIFICATION_FAILED = "SECURITY_VERIFICATION_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class CpuUsageSample(BaseModel):
    """실행 중 수집한 CPU 사용량 그래프용 시간 구간 샘플"""
    elapsed_ms: int = Field(ge=0)  # 실행 시작 후 해당 구간 종료 시점까지의 누적 경과 시간
    interval_ms: int = Field(gt=0)  # 직전 샘플 이후 실제로 경과한 시간
    cpu_time_delta_ms: int = Field(ge=0)  # 해당 구간에서 프로그램과 하위 task가 실제 CPU를 사용한 시간


class MemoryUsageSample(BaseModel):
    """실행 중 수집한 메모리 사용량 그래프용 시점 샘플"""
    elapsed_ms: int = Field(ge=0)
    memory_bytes: int = Field(ge=0)


class ResourceUsage(BaseModel):
    wall_time_ms: int | None = None       # 전체 실행 시간
    cpu_time_ms: int | None = None        # 해당 실행 동안의 누적 CPU 사용 시간(ms)
    memory_peak_bytes: int | None = None  # 최대 메모리 (bytes 단위 주의)
    pids_peak: int | None = None          # 최대 프로세스 및 스레드 수 
    output_bytes: int | None = None       # stdout·stderr 합산 출력 크기
    cpu_usage_samples: list[CpuUsageSample] | None = None
    memory_usage_samples: list[MemoryUsageSample] | None = None
    user_task_peak: int | None = None                       # 사용자 코드의 최대 task 수
    process_at_user_task_peak: int | None = None            # 해당 시점의 프로세스 수
    thread_at_user_task_peak: int | None = None             # 해당 시점의 스레드 수
    
    
class StageError(BaseModel):
    reason_code: ExecutionReasonCode
    message: str


class StageSummary(BaseModel):
    succeeded: list[ExecutionStage] = Field(default_factory=list)
    failed: list[ExecutionStage] = Field(default_factory=list)
    skipped: list[ExecutionStage] = Field(default_factory=list)
    errors: dict[
        ExecutionStage,
        list[StageError],
    ] = Field(default_factory=dict)


class ExecutionCreateRequest(BaseModel):
    language: Language
    code: str = Field(min_length=1)
    stdin: str = ""
    policy: PolicyLimits | None = None  # 없으면 Backend 기본 정책 적용


class ExecutionCreateResponse(BaseModel): # 실행 요청 직후 응답
    job_id: UUID
    status: ExecutionStatus


class ExecutionResultResponse(BaseModel): # 상태/결과 조회
    # 필요한 부분은 나중에 추가하기
    job_id: UUID
    status: ExecutionStatus
    reason_code: ExecutionReasonCode | None = None
    error_message: str | None = None
    exit_code: int | None = None
    stdout: str | None = None
    stderr: str | None = None
    compile_log: str | None = None
    resource_usage: ResourceUsage | None = None
    stage_summary: StageSummary | None = None
    finished_at: datetime | None = None
