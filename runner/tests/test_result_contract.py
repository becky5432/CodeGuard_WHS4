import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.schemas.runner_schema import (  # noqa: E402
    RunnerResponse as BackendRunnerResponse,
)
from runner.models.result import (  # noqa: E402
    RunnerReasonCode,
    RunnerResponse,
    RunnerStage,
    RunnerStatus,
    StageError,
    StageSummary,
)


class ResultContractTests(unittest.TestCase):
    def test_status_and_reason_codes_match_backend_contract(self) -> None:
        self.assertEqual(
            {status.value for status in RunnerStatus},
            {"SUCCESS", "BLOCKED", "ERROR"},
        )
        self.assertEqual(
            {reason.value for reason in RunnerReasonCode},
            {
                "TIME_LIMIT",
                "MEMORY_LIMIT",
                "PROCESS_LIMIT",
                "OUTPUT_LIMIT",
                "NETWORK_BLOCKED",
                "COMPILE_ERROR",
                "COMPILE_TIMEOUT",
                "RUNTIME_ERROR",
                "INTERNAL_ERROR",
            },
        )

    def test_stage_summary_uses_backend_shape(self) -> None:
        summary = StageSummary(
            succeeded=[RunnerStage.WORKSPACE],
            failed=[RunnerStage.COMPILE],
            skipped=[RunnerStage.EXECUTE],
            errors={
                RunnerStage.COMPILE: [
                    StageError(
                        reason_code=RunnerReasonCode.COMPILE_ERROR,
                        message="소스 코드 컴파일에 실패했습니다.",
                    )
                ]
            },
        )

        self.assertEqual(
            summary.model_dump(mode="json"),
            {
                "succeeded": ["WORKSPACE"],
                "failed": ["COMPILE"],
                "skipped": ["EXECUTE"],
                "errors": {
                    "COMPILE": [
                        {
                            "reason_code": "COMPILE_ERROR",
                            "message": "소스 코드 컴파일에 실패했습니다.",
                        }
                    ]
                },
            },
        )

    def test_runner_response_validates_with_backend_schema(self) -> None:
        response = RunnerResponse(
            job_id=uuid4(),
            run_id=uuid4(),
            status=RunnerStatus.SUCCESS,
            stage_summary=StageSummary(
                succeeded=[
                    RunnerStage.WORKSPACE,
                    RunnerStage.COMPILE,
                    RunnerStage.EXECUTE,
                    RunnerStage.CLEANUP,
                ]
            ),
            finished_at=datetime.now(timezone.utc),
        )

        payload = response.model_dump(mode="json")

        self.assertNotIn("stage", payload)
        BackendRunnerResponse.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
