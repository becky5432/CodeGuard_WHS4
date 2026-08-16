from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class RunnerLanguage(str, Enum):
    C = "C"
    CPP = "CPP"


class PolicyLimits(BaseModel):
    timeout_ms: int = Field(gt=0)
    memory_limit_mb: int = Field(gt=0)
    process_limit: int = Field(gt=0)
    cpu_limit: float = Field(gt=0)


class RunnerRequest(BaseModel):
    job_id: UUID
    language: RunnerLanguage
    code: str = Field(min_length=1)
    stdin: str = ""
    policy: PolicyLimits
    created_at: datetime
