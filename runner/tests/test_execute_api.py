import unittest
from datetime import datetime, timezone
from unittest.mock import ANY, MagicMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from runner.exceptions import CleanupError, ContainerExecutionError
from runner.container.container_runner import ExecutionResult
from runner.main import app
from runner.models.job import RunnerLanguage
from runner.pipeline.compiler import CompileResult
from runner.pipeline.workspace import VolumeWorkspace


class ExecuteApiTests(unittest.TestCase):
    RESPONSE_FIELDS = {
        "job_id",
        "run_id",
        "status",
        "reason_code",
        "stage",
        "error_message",
        "exit_code",
        "stdout",
        "stderr",
        "compile_log",
        "finished_at",
    }

    def setUp(self) -> None:
        self.client = TestClient(app)
        self.docker_client = MagicMock()
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
        self.execute_program_patcher = patch(
            "runner.pipeline.executor.execute_program",
        )

        self.get_client_mock = self.get_client_patcher.start()
        self.create_workspace_mock = self.create_workspace_patcher.start()
        self.remove_workspace_mock = self.remove_workspace_patcher.start()
        self.compile_source_mock = self.compile_source_patcher.start()
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
                "process_limit": 10,
                "cpu_limit": 1.0,
            },
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

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

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), self.RESPONSE_FIELDS)
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertIsNone(payload["reason_code"])
        self.assertIsNone(payload["stage"])
        self.assertIsNotNone(payload["finished_at"])
        self.assertEqual(payload["stdout"], "Hello\n")
        self.assertEqual(payload["compile_log"], "compiler note")
        self.create_workspace_mock.assert_called_once_with(
            self.docker_client,
            UUID(body["job_id"]),
        )
        self.compile_source_mock.assert_called_once_with(
            client=self.docker_client,
            workspace=self.workspace,
            language=RunnerLanguage.CPP,
            code=body["code"],
            stdin="",
        )
        self.remove_workspace_mock.assert_called_once_with(
            self.docker_client,
            self.workspace,
        )
        self.execute_program_mock.assert_called_once_with(
            client=self.docker_client,
            workspace=self.workspace,
            stdin=body["stdin"],
            policy=ANY,
            job_id=UUID(body["job_id"]),
            run_id=ANY,
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

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 200)
        self.compile_source_mock.assert_called_once_with(
            client=self.docker_client,
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
        self.assertEqual(payload["reason_code"], "COMPILE_ERROR")
        self.assertEqual(payload["stage"], "COMPILE")
        self.assertEqual(payload["compile_log"], "error: expected ';'")
        self.execute_program_mock.assert_not_called()

    def test_execute_returns_compile_timeout(self) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=False,
            stdout="",
            stderr="Compilation timed out.",
            exit_code=None,
            timed_out=True,
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "COMPILE_TIMEOUT")
        self.assertEqual(payload["stage"], "COMPILE")
        self.execute_program_mock.assert_not_called()

    def test_execute_returns_compile_internal_error(self) -> None:
        self.compile_source_mock.side_effect = ContainerExecutionError(
            "컴파일 컨테이너 실행에 실패했습니다.",
            details={"reason": "test docker error"},
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(payload["reason_code"], "INTERNAL_ERROR")
        self.assertEqual(payload["stage"], "COMPILE")
        self.execute_program_mock.assert_not_called()

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

        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(payload["reason_code"], "INTERNAL_ERROR")
        self.assertEqual(payload["stage"], "CLEANUP")

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

        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(payload["reason_code"], "RUNTIME_ERROR")
        self.assertEqual(payload["stage"], "EXECUTE")
        self.assertEqual(payload["stdout"], "before error\n")
        self.assertEqual(payload["stderr"], "runtime failure\n")
        self.assertEqual(payload["exit_code"], 1)

    def test_execute_returns_time_limit(self) -> None:
        self.compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
            artifact_ready=True,
        )
        self.execute_program_mock.return_value = ExecutionResult(
            exit_code=137,
            stdout="",
            stderr="",
            timed_out=True,
        )

        payload = self.client.post(
            "/execute",
            json=self.make_request_body(),
        ).json()

        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(payload["reason_code"], "TIME_LIMIT")
        self.assertEqual(payload["stage"], "EXECUTE")

    def test_execute_rejects_invalid_policy_without_starting_job(self) -> None:
        body = self.make_request_body()
        body["policy"]["memory_limit_mb"] = 0

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 422)
        self.get_client_mock.assert_not_called()
if __name__ == "__main__":
    unittest.main()
