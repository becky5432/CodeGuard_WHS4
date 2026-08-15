import tempfile 
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from runner.config import settings
from runner.main import app
from runner.models.job import RunnerLanguage
from runner.pipeline.compiler import CompileResult
from runner.exceptions import ContainerExecutionError


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
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_workspace_root = settings.workspace_root
        settings.workspace_root = (
            Path(self.temporary_directory.name) / "codeguard-runner"
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        settings.workspace_root = self.original_workspace_root
        self.temporary_directory.cleanup()

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

    @patch("runner.pipeline.executor.compile_source")
    def test_execute_compiles_cpp_and_removes_workspace(
        self,
        compile_source_mock,
    ) -> None:
        compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
        )

        body = self.make_request_body()

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(set(payload), self.RESPONSE_FIELDS)
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertIsNone(payload["reason_code"])
        self.assertIsNone(payload["stage"])
        self.assertIsNone(payload["error_message"])
        self.assertEqual(payload["compile_log"], "")
        self.assertIsNotNone(payload["finished_at"])

        workspace = settings.workspace_root / body["job_id"]

        compile_source_mock.assert_called_once_with(
            workspace=workspace,
            language=RunnerLanguage.CPP,
        )

        self.assertFalse(workspace.exists())

    @patch("runner.pipeline.executor.compile_source")
    def test_execute_compiles_c_and_removes_workspace(
        self,
        compile_source_mock,
    ) -> None:
        compile_source_mock.return_value = CompileResult(
            success=True,
            stdout="",
            stderr="",
            exit_code=0,
        )

        body = self.make_request_body()
        body["language"] = "C"
        body["code"] = "int main(void) { return 0; }"

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "SUCCESS")
        self.assertIsNone(response.json()["reason_code"])

        workspace = settings.workspace_root / body["job_id"]

        compile_source_mock.assert_called_once_with(
            workspace=workspace,
            language=RunnerLanguage.C,
        )

        self.assertFalse(workspace.exists())

    @patch("runner.pipeline.executor.compile_source")
    def test_execute_returns_compile_error(
        self,
        compile_source_mock,
    ) -> None:
        compile_source_mock.return_value = CompileResult(
            success=False,
            stdout="",
            stderr="error: expected ';'",
            exit_code=1,
        )

        response = self.client.post(
            "/execute",
            json=self.make_request_body(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(
            payload["reason_code"],
            "COMPILE_ERROR",
        )
        self.assertEqual(payload["stage"], "COMPILE")
        self.assertEqual(
            payload["error_message"],
            "소스 코드 컴파일에 실패했습니다.",
        )
        self.assertEqual(payload["compile_log"], "error: expected ';'")
        self.assertEqual(payload["stdout"], "")
        self.assertEqual(payload["stderr"], "")
        self.assertEqual(payload["exit_code"], 1)
        self.assertIsNotNone(payload["finished_at"])

    @patch("runner.pipeline.executor.compile_source")
    def test_execute_returns_c_compile_error(
        self,
        compile_source_mock,
    ) -> None:
        compile_source_mock.return_value = CompileResult(
            success=False,
            stdout="",
            stderr="error: expected ';'",
            exit_code=1,
        )

        body = self.make_request_body()
        body["language"] = "C"
        body["code"] = "int main(void) { return 0 }"

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ERROR")
        self.assertEqual(
            response.json()["reason_code"],
            "COMPILE_ERROR",
        )
        self.assertEqual(response.json()["exit_code"], 1)

        workspace = settings.workspace_root / body["job_id"]

        compile_source_mock.assert_called_once_with(
            workspace=workspace,
            language=RunnerLanguage.C,
        )

        self.assertFalse(workspace.exists())

    @patch("runner.pipeline.executor.compile_source")
    def test_execute_returns_compile_timeout(
        self,
        compile_source_mock,
    ) -> None:
        compile_source_mock.return_value = CompileResult(
            success=False,
            stdout="",
            stderr="Compilation timed out.",
            exit_code=None,
            timed_out=True,
        )

        response = self.client.post(
            "/execute",
            json=self.make_request_body(),
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "BLOCKED")
        self.assertEqual(
            payload["reason_code"],
            "COMPILE_TIMEOUT",
        )
        self.assertEqual(payload["stage"], "COMPILE")
        self.assertEqual(
            payload["error_message"],
            "컴파일 제한 시간을 초과했습니다.",
        )
        self.assertEqual(payload["compile_log"], "Compilation timed out.")
        self.assertIsNotNone(payload["finished_at"])

    @patch("runner.pipeline.executor.compile_source")
    def test_execute_returns_internal_error(
        self,
        compile_source_mock,
    ) -> None:
        compile_source_mock.side_effect = ContainerExecutionError(
            "컴파일 컨테이너 실행에 실패했습니다.",
            details={"reason": "test docker error"},
        )

        body = self.make_request_body()

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["status"], "ERROR")
        self.assertEqual(
            payload["reason_code"],
            "INTERNAL_ERROR",
        )
        self.assertEqual(payload["stage"], "COMPILE")
        self.assertEqual(
            payload["error_message"],
            "컴파일 컨테이너 실행에 실패했습니다.",
        )
        self.assertIsNone(payload["exit_code"])
        self.assertIsNotNone(payload["finished_at"])

        workspace = settings.workspace_root / body["job_id"]
        self.assertFalse(workspace.exists())

    def test_execute_rejects_invalid_policy_without_starting_job(
        self,
    ) -> None:
        body = self.make_request_body()
        body["policy"]["memory_limit_mb"] = 0

        response = self.client.post("/execute", json=body)

        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
