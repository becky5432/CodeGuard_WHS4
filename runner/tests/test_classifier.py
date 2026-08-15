import unittest
from types import SimpleNamespace

from runner.models.result import RunnerReasonCode, RunnerStage, RunnerStatus
from runner.pipeline.classifier import classify_execution


class ClassifierTests(unittest.TestCase):
    def evidence(self, **overrides):
        values = {
            "exit_code": 0,
            "timed_out": False,
            "output_limit_exceeded": False,
            "oom_killed": False,
            "pids_limit_hit": False,
            "system_error": None,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_system_error_has_highest_priority(self) -> None:
        result = classify_execution(
            self.evidence(
                system_error="Docker inspect failed",
                timed_out=True,
                oom_killed=True,
            )
        )

        self.assertEqual(result.status, RunnerStatus.ERROR)
        self.assertEqual(result.reason_code, RunnerReasonCode.INTERNAL_ERROR)
        self.assertEqual(result.stage, RunnerStage.EXECUTE)

    def test_output_limit_precedes_timeout_exit_code(self) -> None:
        result = classify_execution(
            self.evidence(
                exit_code=137,
                output_limit_exceeded=True,
                timed_out=True,
            )
        )

        self.assertEqual(result.status, RunnerStatus.BLOCKED)
        self.assertEqual(result.reason_code, RunnerReasonCode.OUTPUT_LIMIT)

    def test_timeout_precedes_oom_and_exit_code(self) -> None:
        result = classify_execution(
            self.evidence(exit_code=137, timed_out=True, oom_killed=True)
        )

        self.assertEqual(result.reason_code, RunnerReasonCode.TIME_LIMIT)

    def test_oom_requires_evidence_not_exit_137_alone(self) -> None:
        oom_result = classify_execution(
            self.evidence(exit_code=137, oom_killed=True)
        )
        runtime_result = classify_execution(self.evidence(exit_code=137))

        self.assertEqual(oom_result.reason_code, RunnerReasonCode.MEMORY_LIMIT)
        self.assertEqual(runtime_result.reason_code, RunnerReasonCode.RUNTIME_ERROR)

    def test_pid_limit_precedes_runtime_error(self) -> None:
        result = classify_execution(
            self.evidence(exit_code=1, pids_limit_hit=True)
        )

        self.assertEqual(result.reason_code, RunnerReasonCode.PROCESS_LIMIT)

    def test_exit_zero_is_success(self) -> None:
        result = classify_execution(self.evidence(exit_code=0))

        self.assertEqual(result.status, RunnerStatus.SUCCESS)
        self.assertIsNone(result.reason_code)
        self.assertIsNone(result.stage)
        self.assertIsNone(result.error_message)

    def test_nonzero_exit_is_runtime_error(self) -> None:
        result = classify_execution(self.evidence(exit_code=1))

        self.assertEqual(result.status, RunnerStatus.ERROR)
        self.assertEqual(result.reason_code, RunnerReasonCode.RUNTIME_ERROR)
        self.assertEqual(result.stage, RunnerStage.EXECUTE)


if __name__ == "__main__":
    unittest.main()
