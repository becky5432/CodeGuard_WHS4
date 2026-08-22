from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


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
    PIDS_LIMIT = "PIDS_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    COMPILE_ERROR = "COMPILE_ERROR"
    COMPILE_TIMEOUT = "COMPILE_TIMEOUT"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class StageError(BaseModel):
    reason_code: RunnerReasonCode
    message: str


class StageSummary(BaseModel):
    succeeded: list[RunnerStage] = Field(default_factory=list)
    failed: list[RunnerStage] = Field(default_factory=list)
    skipped: list[RunnerStage] = Field(default_factory=list)
    errors: dict[RunnerStage, list[StageError]] = Field(default_factory=dict)


class ResourceUsage(BaseModel):
    wall_time_ms: int | None = None
    cpu_time_ms: int | None = None
    memory_peak_bytes: int | None = None
    pids_peak: int | None = None


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
    finished_at: datetime | None = None
    stage_summary: StageSummary = Field(default_factory=StageSummary)
