from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel

from app.schemas.execution_schema import PolicyLimits
# execution_schema.py에 있는 PolicyLimits를 그대로 전달


class RunnerLanguage(str, Enum):
    C = "C"
    CPP = "CPP"


class RunnerStatus(str, Enum):
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
    
    
class RunnerReasonCode(str, Enum):
    TIME_LIMIT = "TIME_LIMIT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    PROCESS_LIMIT = "PROCESS_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    COMPILE_TIMEOUT = "COMPILE_TIMEOUT"


class RunnerRequest(BaseModel):
    job_id: UUID
    language: RunnerLanguage
    code: str
    stdin: str = ""
    policy: PolicyLimits
    created_at: datetime   # 백엔드가 실행 요청을 접수해서 생성한 시각


class RunnerResponse(BaseModel):
    job_id: UUID
    run_id: UUID
    status: RunnerStatus
    reason_code: RunnerReasonCode | None  # 이 실행을 최종적으로 왜 BLOCKED/ERROR 처리했는가
    # violations: list[RunnerReasonCode]  # 여러 위반 사유 제시가 필요해지면 추가

    stdout: str
    stderr: str
    exit_code: int | None