from datetime import datetime, timezone
from uuid import uuid4

from app.schemas.runner_schema import (
    ResourceUsage,
    RunnerRequest,
    RunnerResponse,
    RunnerStatus,
    RunnerStage,
    StageSummary,
)


# TODO: Runner /execute 엔드포인트 및 요청·응답 규격 확정 후 HttpRunnerClient 구현
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