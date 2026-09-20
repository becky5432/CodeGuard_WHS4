from datetime import datetime, timezone
from uuid import uuid4

import httpx

from app.schemas.runner_schema import (
    ResourceUsage,
    RunnerRequest,
    RunnerResponse,
    RunnerStatus,
    RunnerStage,
    StageSummary,
)


# Runner 서버 연결 실패 및 timeout은
# ExecutionService에서 ERROR/INTERNAL_ERROR로 처리        
class HttpRunnerClient:
    def __init__(self, base_url: str, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def execute(self, request: RunnerRequest) -> RunnerResponse:
        response = httpx.post(
            f"{self.base_url}/execute",
            json=request.model_dump(mode="json"),
            timeout=self.timeout,
        )

        response.raise_for_status()

        return RunnerResponse.model_validate(response.json())