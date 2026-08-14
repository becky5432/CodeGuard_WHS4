from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.runner_schema import RunnerRequest, RunnerResponse, RunnerStatus


# TODO: Runner /execute 엔드포인트 및 요청·응답 규격 확정 후 HttpRunnerClient 구현
class MockRunnerClient:
    def execute(self, request: RunnerRequest) -> RunnerResponse:
        return RunnerResponse(
            job_id=request.job_id,
            run_id=uuid4(),
            status=RunnerStatus.SUCCESS,
            reason_code=None,
            stage=None,
            error_message=None,
            exit_code=0,
            stdout="Hello from MockRunner!\n",
            stderr="",
            compile_log="",
            finished_at=datetime.now(timezone.utc),
        )