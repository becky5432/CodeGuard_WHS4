import unittest
from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from runner.config import Settings
from runner.exceptions import CleanupError, ContainerExecutionError
from runner.main import app
from runner.models.job import PolicyLimits, RunnerLanguage, RunnerRequest
from runner.models.result import RunnerReasonCode, RunnerStatus
from runner.pipeline.compiler import CompileResult
from runner.pipeline.execution import ExecutionResult
from runner.pipeline.workspace import VolumeWorkspace
from runner.policies import EXECUTION_OUTPUT_LIMIT_BYTES


class ExecuteApiTests(unittest.TestCase):
    RESPONSE_FIELDS = {
        "job_id",
        "run_id",
        "status",
        "reason_code",
        "error_message",
        "exit_code",
        "stdout",
        "stderr",
        "compile_log",
        "resource_usage",
        "finished_at",
        "stage_summary",
    }

    def setUp(self) -> None:
        self.client = TestClient(app)

        self.docker_client = MagicMock()
        self.compile_container = MagicMock()
        self.execution_container = MagicMock()

        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )

        self.get_client_patcher = patch(
            "runner.pipeline.executor.get_docker_client",
            return_value=self.docker_client,
        )

        self.create_workspace_patcher = patch(
            "runner.pipeline.executor.create_workspace",
            return_value=self.workspace,
        )

        self.remove_workspace_patcher = patch(
            "runner.pipeline.executor.remove_workspace",
        )

        self.compile_source_patcher = patch(
            "runner.pipeline.executor.compile_source",
        )

        self.create_compile_container_patcher = patch(
            "runner.pipeline.executor.create_compile_container",
            return_value=self.compile_container,
        )

        self.create_execution_container_patcher = patch(
            "runner.pipeline.executor.create_execution_container",
            return_value=self.execution_container,
        )

        self.execute_program_patcher = patch(
            "runner.pipeline.executor.execute_program",
        )

        self.get_client_mock = self.get_client_patcher.start()
        self.create_workspace_mock = self.create_workspace_patcher.start()
        self.remove_workspace_mock = self.remove_workspace_patcher.start()
        self.compile_source_mock = self.compile_source_patcher.start()

        self.create_compile_container_mock = (
            self.create_compile_container_patcher.start()
        )

        self.create_execution_container_mock = (
            self.create_execution_container_patcher.start()
        )

        self.execute_program_mock = self.execute_program_patcher.start()

    def tearDown(self) -> None:
        patch.stopall()

    def make_request_body(self) -> dict:
        return {
            "job_id": str(uuid4()),
            "language": "CPP",
            "code": "int main() { return 0; }",
            "stdin": "",
            "policy": {
                "timeout_ms": 2000,
                "memory_limit_mb": 128,
                "pids_limit": 10,
                "cpu_limit": 1.0,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def test_request_model_includes_policy_contract(self) -> None:
        self.assertIn("policy", RunnerRequest.model_fields)

        self.assertEqual(
            set(PolicyLimits.model_fields),
            {
                "timeout_ms",
                "memory_limit_mb",
                "pids_limit",
                "cpu_limit",
                "output_limit_bytes",
            },
        )

        self.assertIn("BLOCKED", RunnerStatus.__members__)

        self.assertEqual(
            set(RunnerReasonCode.__members__),
            {
                "TIME_LIMIT",
                "MEMORY_LIMIT",
                "PIDS_LIMIT",
                "OUTPUT_LIMIT",
                "NETWORK_BLOCKED",
                "COMPILE_ERROR",
                "COMPILE_TIMEOUT",
                "RUNTIME_ERROR",
                "INTERNAL_ERROR",
            },
        )

        self.assertTrue(
            {
                "compile_timeout_seconds",
                "compile_log_limit_bytes",
                "execution_output_limit_bytes",
                "execution_tmpfs_limit_mb",
                "runtime_user",
            }.isdisjoint(Settings.model_fields),
        )

    def test_execute_compiles_cpp_with_job_volume(self) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="compiler note",
            stderr="",
            exit_code=0,
            artifact_ready=True,
        )

        self.execute_program_mock.return_value = ExecutionResult(
            exit_code=0,
            stdout="Hello\n",
            stderr="",
        )

        body = self.make_request_body()

        response = self.client.post(
            "/execute",
            json=body,
        )

        self.assertEqual(response.status_code, 200)

        payload = response.json()

        self.assertEqual(set(payload), self.RESPONSE_FIELDS)
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertIsNone(payload["reason_code"])
        self.assertIsNotNone(payload["finished_at"])
        self.assertEqual(payload["stdout"], "Hello\n")
        self.assertEqual(payload["compile_log"], "compiler note")
        self.assertEqual(
            payload["resource_usage"],
            {
                "wall_time_ms": None,
                "cpu_time_ms": None,
                "memory_peak_bytes": None,
                "process_peak": None,
            },
        )
        self.assertEqual(
            payload["stage_summary"],
            {
                "succeeded": ["WORKSPACE", "COMPILE", "EXECUTE", "CLEANUP"],
                "failed": [],
                "skipped": [],
                "errors": {},
            },
        )

        self.create_workspace_mock.assert_called_once_with(
            self.docker_client,
            UUID(body["job_id"]),
        )

        self.compile_source_mock.assert_called_once_with(
            container=self.compile_container,
            workspace=self.workspace,
            language=RunnerLanguage.CPP,
            code=body["code"],
            stdin="",
        )

        self.create_compile_container_mock.assert_called_once_with(
            client=self.docker_client,
            workspace=self.workspace,
            language=RunnerLanguage.CPP,
        )

        self.create_execution_container_mock.assert_called_once_with(
            client=self.docker_client,
            workspace=self.workspace,
            stdin=body["stdin"],
            job_id=UUID(body["job_id"]),
            run_id=ANY,
            memory_limit_mb=body["policy"]["memory_limit_mb"],
        )

        self.remove_workspace_mock.assert_called_once_with(
            self.docker_client,
            self.workspace,
        )

        self.execute_program_mock.assert_called_once_with(
            container=self.execution_container,
            job_id=UUID(body["job_id"]),
            run_id=ANY,
            timeout_ms=body["policy"]["timeout_ms"],
            output_limit_bytes=EXECUTION_OUTPUT_LIMIT_BYTES,
        )

        self.execution_container.remove.assert_called_once_with(
            force=True,
        )

        self.compile_container.remove.assert_called_once_with(
            force=True,
        )

    def test_execute_compiles_c_with_job_volume(self) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            artifact_ready=True,
        )

        self.execute_program_mock.return_value = ExecutionResult(
            exit_code=0,
            stdout="",
            stderr="",
        )

        body = self.make_request_body()
        body["language"] = "C"
        body["code"] = "int main(void) { return 0; }"

        response = self.client.post(
            "/execute",
            json=body,
        )

        self.assertEqual(response.status_code, 200)

        self.compile_source_mock.assert_called_once_with(
            container=self.compile_container,
            workspace=self.workspace,
            language=RunnerLanguage.C,
            code=body["code"],
            stdin="",
        )

    def test_execute_returns_compile_error(self) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=False,
            stdout="",
            stderr="error: expected ';'",
            exit_code=1,
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(
            payload["reason_code"],
            "COMPILE_ERROR",
        )
        self.assertEqual(payload["stage_summary"]["failed"], ["COMPILE"])
        self.assertEqual(payload["stage_summary"]["skipped"], ["EXECUTE"])
        self.assertEqual(
            payload["stage_summary"]["errors"]["COMPILE"][0],
            {
                "reason_code": "COMPILE_ERROR",
                "message": "소스 코드 컴파일에 실패했습니다.",
            },
        )
        self.assertEqual(
            payload["compile_log"],
            "error: expected ';'",
        )

        self.create_execution_container_mock.assert_not_called()
        self.execute_program_mock.assert_not_called()

        self.compile_container.remove.assert_called_once_with(
            force=True,
        )

        self.execution_container.remove.assert_not_called()

    def test_execute_returns_compile_internal_error(self) -> None:
        self.compile_source_mock.side_effect = ContainerExecutionError(
            "컴파일 컨테이너 실행에 실패했습니다.",
            details={
                "reason": "test docker error",
            },
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(
            payload["reason_code"],
            "INTERNAL_ERROR",
        )
        self.assertEqual(payload["stage_summary"]["failed"], ["COMPILE"])
        self.assertEqual(payload["stage_summary"]["skipped"], ["EXECUTE"])

        self.create_execution_container_mock.assert_not_called()
        self.execute_program_mock.assert_not_called()

    def test_execution_create_failure_still_cleans_compile_and_volume(
        self,
    ) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="compiler note",
            stderr="",
            exit_code=0,
            artifact_ready=True,
        )

        self.create_execution_container_mock.side_effect = (
            ContainerExecutionError(
                "실행 컨테이너 생성에 실패했습니다.",
            )
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "ERROR")

        self.assertEqual(
            payload["reason_code"],
            "INTERNAL_ERROR",
        )

        self.assertEqual(payload["stage_summary"]["failed"], ["EXECUTE"])

        self.assertEqual(
            payload["compile_log"],
            "compiler note",
        )

        self.execute_program_mock.assert_not_called()
        self.execution_container.remove.assert_not_called()

        self.compile_container.remove.assert_called_once_with(
            force=True,
        )

        self.remove_workspace_mock.assert_called_once_with(
            self.docker_client,
            self.workspace,
        )

    def test_execute_returns_cleanup_error(self) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            artifact_ready=True,
        )

        self.execute_program_mock.return_value = ExecutionResult(
            exit_code=0,
            stdout="",
            stderr="",
        )

        self.remove_workspace_mock.side_effect = CleanupError(
            "Job Volume 삭제에 실패했습니다.",
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "SUCCESS")
        self.assertIsNone(payload["reason_code"])
        self.assertEqual(payload["stage_summary"]["failed"], ["CLEANUP"])
        self.assertEqual(
            payload["stage_summary"]["errors"]["CLEANUP"][0],
            {
                "reason_code": "INTERNAL_ERROR",
                "message": "Job Volume 삭제에 실패했습니다.",
            },
        )

    def test_cleanup_continues_in_reverse_creation_order_after_failure(
        self,
    ) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            artifact_ready=True,
        )

        self.execute_program_mock.return_value = ExecutionResult(
            exit_code=0,
            stdout="",
            stderr="",
        )

        cleanup_order = []

        def fail_execution_cleanup(*args, **kwargs):
            cleanup_order.append("execution")
            raise RuntimeError(
                "unexpected execution cleanup failure"
            )

        self.execution_container.remove.side_effect = (
            fail_execution_cleanup
        )

        self.compile_container.remove.side_effect = (
            lambda *args, **kwargs: cleanup_order.append(
                "compile"
            )
        )

        self.remove_workspace_mock.side_effect = (
            lambda *args, **kwargs: cleanup_order.append(
                "volume"
            )
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "SUCCESS")
        self.assertEqual(payload["stage_summary"]["failed"], ["CLEANUP"])

        self.compile_container.remove.assert_called_once_with(
            force=True,
        )

        self.remove_workspace_mock.assert_called_once_with(
            self.docker_client,
            self.workspace,
        )

        self.assertEqual(
            cleanup_order,
            [
                "execution",
                "compile",
                "volume",
            ],
        )

    def test_execute_returns_runtime_error(self) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            artifact_ready=True,
        )

        self.execute_program_mock.return_value = ExecutionResult(
            exit_code=1,
            stdout="before error\n",
            stderr="runtime failure\n",
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(
            payload["status"],
            "ERROR",
        )

        self.assertEqual(
            payload["reason_code"],
            "RUNTIME_ERROR",
        )

        self.assertEqual(payload["stage_summary"]["failed"], ["EXECUTE"])

        self.assertEqual(
            payload["stdout"],
            "before error\n",
        )

        self.assertEqual(
            payload["stderr"],
            "runtime failure\n",
        )

        self.assertEqual(
            payload["exit_code"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
