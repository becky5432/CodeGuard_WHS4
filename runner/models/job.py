from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class RunnerLanguage(str, Enum):
    C = "C"
    CPP = "CPP"


class RunnerRequest(BaseModel):
    job_id: UUID
    language: RunnerLanguage
    code: str = Field(min_length=1)
    stdin: str = ""
    created_at: datetime
