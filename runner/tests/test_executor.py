import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from uuid import uuid4

from runner.models.job import PolicyLimits, RunnerLanguage, RunnerRequest
from runner.models.result import RunnerReasonCode, RunnerStage, RunnerStatus
from runner.pipeline.compiler import CompileResult
from runner.pipeline.executor import execute_job
from runner.pipeline.workspace import VolumeWorkspace


class ExecutorTests(unittest.TestCase):
    @patch("runner.pipeline.executor.remove_workspace")
    @patch("runner.pipeline.executor.create_execution_container")
    @patch("runner.pipeline.executor.compile_source")
    @patch("runner.pipeline.executor.create_compile_container")
    @patch("runner.pipeline.executor.create_workspace")
    @patch("runner.pipeline.executor.get_docker_client")
    def test_compile_timeout_skips_execution_and_cleans_resources(
        self,
        get_client,
        create_workspace,
        create_compile_container,
        compile_source,
        create_execution_container,
        remove_workspace,
    ) -> None:
        job_id = uuid4()
        client = MagicMock()
        compile_container = MagicMock()
        workspace = VolumeWorkspace(job_id, "codeguard-job-test")
        get_client.return_value = client
        create_workspace.return_value = workspace
        create_compile_container.return_value = compile_container
        compile_source.return_value = CompileResult(
            success=False,
            stdout="",
            stderr="",
            exit_code=None,
            timed_out=True,
        )
        job = RunnerRequest(
            job_id=job_id,
            language=RunnerLanguage.CPP,
            code="int main() { return 0; }",
            policy=PolicyLimits(
                timeout_ms=1000,
                memory_limit_mb=64,
                pids_limit=8,
                cpu_limit=1.0,
            ),
            created_at=datetime.now(timezone.utc),
        )

        response = execute_job(job)

        self.assertEqual(response.status, RunnerStatus.ERROR)
        self.assertEqual(
            response.reason_code,
            RunnerReasonCode.COMPILE_TIMEOUT,
        )
        self.assertEqual(response.stage_summary.failed, [RunnerStage.COMPILE])
        self.assertEqual(response.stage_summary.skipped, [RunnerStage.EXECUTE])
        create_execution_container.assert_not_called()
        compile_container.remove.assert_called_once_with(force=True)
        remove_workspace.assert_called_once_with(client, workspace)


if __name__ == "__main__":
    unittest.main()
