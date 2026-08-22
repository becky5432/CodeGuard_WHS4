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


class MockRunnerClient:
    def execute(self, request: RunnerRequest) -> RunnerResponse:
        return RunnerResponse(
            job_id=request.job_id,
            run_id=uuid4(),
            status=RunnerStatus.SUCCESS,
            reason_code=None,
            stage_summary=StageSummary(
                succeeded=[
                    RunnerStage.WORKSPACE,
                    RunnerStage.COMPILE,
                    RunnerStage.EXECUTE,
                    RunnerStage.CLEANUP,
                ]
            ),
            error_message=None,
            exit_code=0,
            stdout="Hello from MockRunner!\n",
            stderr="",
            compile_log="",
            finished_at=datetime.now(timezone.utc),
            resource_usage=ResourceUsage(
                wall_time_ms=850,              # 테스트용 가짜 값
                cpu_time_ms=120,
                memory_peak_bytes=44040192,
                process_peak=2,
            ),
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