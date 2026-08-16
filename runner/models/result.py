from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class RunnerStatus(str, Enum):
    SUCCESS = "SUCCESS"
    ERROR = "ERROR"


class RunnerStage(str, Enum):
    WORKSPACE = "WORKSPACE"
    COMPILE = "COMPILE"
    EXECUTE = "EXECUTE"
    CLEANUP = "CLEANUP"


class RunnerReasonCode(str, Enum):
    COMPILE_ERROR = "COMPILE_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ResourceUsage(BaseModel):
    wall_time_ms: int | None = None
    cpu_time_ms: int | None = None
    memory_peak_bytes: int | None = None
    process_peak: int | None = None


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
    resource_usage: ResourceUsage | None = None
    finished_at: datetime | None = None
