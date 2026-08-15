import unittest

from runner.pipeline.execution import ExecutionResult
from runner.models.result import RunnerReasonCode, RunnerStatus
from runner.pipeline.classifier import classify_execution


class ClassifierTests(unittest.TestCase):
    def test_exit_zero_is_success(self) -> None:
        result = classify_execution(ExecutionResult(0, "ok", ""))

        self.assertEqual(result.status, RunnerStatus.SUCCESS)
        self.assertIsNone(result.reason_code)
        self.assertIsNone(result.stage)

    def test_nonzero_exit_is_runtime_error(self) -> None:
        result = classify_execution(ExecutionResult(3, "", "failure"))

        self.assertEqual(result.status, RunnerStatus.ERROR)
        self.assertEqual(result.reason_code, RunnerReasonCode.RUNTIME_ERROR)
        self.assertEqual(result.stage.value, "EXECUTE")

    def test_system_error_has_priority(self) -> None:
        result = classify_execution(
            ExecutionResult(None, "", "", system_error="docker failed"),
        )

        self.assertEqual(result.status, RunnerStatus.ERROR)
        self.assertEqual(result.reason_code, RunnerReasonCode.INTERNAL_ERROR)


if __name__ == "__main__":
    unittest.main()
