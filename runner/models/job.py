from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class RunnerLanguage(str, Enum):
    C = "C"
    CPP = "CPP"


class PolicyLimits(BaseModel):
    """이번 단계에서는 형식만 검증하고 컨테이너에는 적용하지 않는다."""

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
