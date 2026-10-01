from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field

from runner.policies import EXECUTION_OUTPUT_LIMIT_BYTES


class RunnerLanguage(str, Enum):
    C = "C"
    CPP = "CPP"


class NetworkPreset(str, Enum):
    """실행 컨테이너의 네트워크 차단 프리셋."""

    NONE = "none"    # P0: 완전 차단
    HTTPS = "https"  # P1: 공인 443만 허용
    WEB = "web"      # P2: 공인 80/443/53 허용


class PolicyLimits(BaseModel):
    timeout_ms: int = Field(gt=0)
    memory_limit_mb: int = Field(gt=0)
    pids_limit: int = Field(gt=0)
    cpu_bandwidth: float = Field(gt=0)
    cpu_time_limit_ms: int = Field(gt=0)
    output_limit_bytes: int = Field(
        default=EXECUTION_OUTPUT_LIMIT_BYTES,
        gt=0,
    )
    # 미지정(None)이면 러너 기본 네트워크(settings.execution_network)를 사용 → 기존 동작 유지
    network_preset: NetworkPreset | None = None


class RunnerRequest(BaseModel):
    job_id: UUID
    language: RunnerLanguage
    code: str = Field(min_length=1)
    stdin: str = ""
    policy: PolicyLimits
    created_at: datetime
