from enum import Enum

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
    process_limit: int = Field(gt=0)
    cpu_limit: float = Field(gt=0)


class ExecutionStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"
    BLOCKED = "BLOCKED"


class ExecutionCreateRequest(BaseModel):
    language: Language
    code: str = Field(min_length=1)
    stdin: str = ""
    policy_profile: PolicyProfile
    policy: PolicyLimits


class ExecutionCreateResponse(BaseModel): # 실행 요청 직후 응답
    job_id: str
    status: ExecutionStatus


class ExecutionResultResponse(BaseModel): # 상태/결과 조회
    job_id: str
    status: ExecutionStatus
    stdout: str | None = None
    stderr: str | None = None
    exit_code: int | None = None
    reason_code: str | None = None