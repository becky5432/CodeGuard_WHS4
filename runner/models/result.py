from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


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


class RunnerResponse(BaseModel):
    job_id: UUID
    run_id: UUID
    status: RunnerStatus
    reason_code: RunnerReasonCode | None = None
    stage: RunnerStage | None = None
    error_message: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    compile_log: str | None = None
    finished_at: datetime | None = None
