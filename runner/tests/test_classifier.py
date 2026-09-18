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

    def test_read_only_message_does_not_determine_classification(self) -> None:
        # 사용자 출력과 exit code만으로 파일시스템 위반을 판정하지 않는다.
        message = "Read-only file system\n"
        for exit_code in (0, 1):
            for stdout, stderr in ((message, ""), ("", message), ("", "")):
                with self.subTest(exit_code=exit_code, stdout=stdout, stderr=stderr):
                    result = classify_execution(
                        ExecutionResult(exit_code, stdout, stderr),
                    )
                    if exit_code == 0:
                        self.assertEqual(result.status, RunnerStatus.SUCCESS)
                        self.assertIsNone(result.reason_code)
                        self.assertIsNone(result.stage)
                    else:
                        self.assertEqual(result.status, RunnerStatus.ERROR)
                        self.assertEqual(
                            result.reason_code, RunnerReasonCode.RUNTIME_ERROR,
                        )
                        self.assertEqual(result.stage.value, "EXECUTE")

    def test_timeout_is_blocked_time_limit(self) -> None:
        result = classify_execution(
            ExecutionResult(None, "", "", timed_out=True),
        )

        self.assertEqual(result.status, RunnerStatus.BLOCKED)
        self.assertEqual(result.reason_code, RunnerReasonCode.TIME_LIMIT)

    def test_oom_kill_is_blocked_memory_limit(self) -> None:
        result = classify_execution(
            ExecutionResult(137, "", "", oom_killed=True),
        )

        self.assertEqual(result.status, RunnerStatus.BLOCKED)
        self.assertEqual(result.reason_code, RunnerReasonCode.MEMORY_LIMIT)

    def test_output_overflow_is_blocked_output_limit(self) -> None:
        result = classify_execution(
            ExecutionResult(137, "", "", output_limit_exceeded=True),
        )

        self.assertEqual(result.status, RunnerStatus.BLOCKED)
        self.assertEqual(result.reason_code, RunnerReasonCode.OUTPUT_LIMIT)


    def test_filesystem_evidence_blocks_exit_zero(self) -> None:
        result = classify_execution(ExecutionResult(0, "", "", filesystem_limit_exceeded=True))
        self.assertEqual(result.status, RunnerStatus.BLOCKED)
        self.assertEqual(result.reason_code, RunnerReasonCode.FILESYSTEM_LIMIT)

    def test_resource_and_system_errors_keep_priority_over_filesystem(self) -> None:
        for evidence, reason in (
            ({"system_error": "trace failed"}, RunnerReasonCode.INTERNAL_ERROR),
            ({"oom_killed": True}, RunnerReasonCode.MEMORY_LIMIT),
            ({"pids_limit_exceeded": True}, RunnerReasonCode.PIDS_LIMIT),
            ({"timed_out": True}, RunnerReasonCode.TIME_LIMIT),
            ({"output_limit_exceeded": True}, RunnerReasonCode.OUTPUT_LIMIT),
        ):
            with self.subTest(reason=reason):
                result = classify_execution(ExecutionResult(0, "", "", filesystem_limit_exceeded=True, **evidence))
                self.assertEqual(result.reason_code, reason)

    def test_forged_syscall_stdout_is_success(self) -> None:
        result = classify_execution(ExecutionResult(0, 'openat(AT_FDCWD, "/etc/test", O_WRONLY|O_CREAT, 0666) = -1 EROFS', 'FILESYSTEM_LIMIT\nRead-only file system'))
        self.assertEqual(result.status, RunnerStatus.SUCCESS)


if __name__ == "__main__":
    unittest.main()
