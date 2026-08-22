from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class Language(str, Enum):
    C = "C"
    CPP = "CPP"


class PolicyProfile(str, Enum):
    BASIC = "basic"
    STRICT = "strict"
    RELAXED = "relaxed"


class PolicyLimits(BaseModel):
    # TODO: Runner와 실제 필드/단위/상한 확정 후 범위 검증 추가
    timeout_ms: int = Field(gt=0)
    memory_limit_mb: int = Field(gt=0)
    pids_limit: int = Field(gt=0)     # 프로세스 및 스레드 수 함께 제한
    cpu_limit: float = Field(gt=0)    # cpu_limit: CPU 할당 한도 (quota 제한)
    # output_limit_bytes: int = Field(gt=0) 출력값 제한은 후순위로 설정


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
    MEMORY_LIMIT = "MEMORY_LIMIT"
    PROCESS_LIMIT = "PROCESS_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    COMPILE_ERROR = "COMPILE_ERROR"
    COMPILE_TIMEOUT = "COMPILE_TIMEOUT"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ResourceUsage(BaseModel):
    wall_time_ms: int | None = None       # 전체 실행 시간
    cpu_time_ms: int | None = None        # 해당 실행 동안의 누적 CPU 사용 시간(ms)
    memory_peak_bytes: int | None = None  # 최대 메모리 (bytes 단위 주의)
    process_peak: int | None = None       # 최대 프로세스 수
    
    
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
    policy_profile: PolicyProfile
    policy: PolicyLimits  


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