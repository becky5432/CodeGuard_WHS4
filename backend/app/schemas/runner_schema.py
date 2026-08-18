from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.execution_schema import PolicyLimits
# execution_schema.py에 있는 PolicyLimits를 그대로 전달


class RunnerLanguage(str, Enum):
    C = "C"
    CPP = "CPP"


class RunnerStatus(str, Enum):
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    

class RunnerStage(str, Enum):
    WORKSPACE = "WORKSPACE"
    COMPILE = "COMPILE"
    EXECUTE = "EXECUTE"
    CLEANUP = "CLEANUP"
    
    
class RunnerReasonCode(str, Enum):
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
    cpu_time_ms: int | None = None        # CPU 사용 시간 (누적)
    memory_peak_bytes: int | None = None  # 최대 메모리 (bytes 단위 주의)
    process_peak: int | None = None       # 최대 프로세스 수


class StageError(BaseModel):
    reason_code: RunnerReasonCode
    message: str


class StageSummary(BaseModel):
    succeeded: list[RunnerStage] = Field(default_factory=list)
    failed: list[RunnerStage] = Field(default_factory=list)
    skipped: list[RunnerStage] = Field(default_factory=list)
    errors: dict[
        RunnerStage,
        list[StageError],
    ] = Field(default_factory=dict)


class RunnerRequest(BaseModel):
    job_id: UUID
    language: RunnerLanguage
    code: str
    stdin: str = ""
    policy: PolicyLimits
    created_at: datetime   # 백엔드가 실행 요청을 접수해서 생성한 시각


class StageError(BaseModel):
    reason_code: RunnerReasonCode
    message: str


class StageSummary(BaseModel):
    succeeded: list[RunnerStage] = []
    failed: list[RunnerStage] = []
    skipped: list[RunnerStage] = []
    errors: list[StageError] = []

class RunnerResponse(BaseModel):
    job_id: UUID
    run_id: UUID
    status: RunnerStatus
    reason_code: RunnerReasonCode | None = None
    error_message: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    compile_log: str | None = None
    resource_usage: ResourceUsage | None = None
    stage_summary: StageSummary          # 필수 (단일 stage는 제거)
    finished_at: datetime                # 필수로 변경
