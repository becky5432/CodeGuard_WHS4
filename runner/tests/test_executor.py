import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

from runner.config import settings
from runner.models.job import PolicyLimits, RunnerLanguage, RunnerRequest
from runner.models.result import RunnerReasonCode, RunnerStage, RunnerStatus
from runner.pipeline.compiler import CompileResult
from runner.pipeline.execution import ExecutionResult
from runner.pipeline.executor import execute_job
from runner.pipeline.workspace import VolumeWorkspace


class ExecutorTests(unittest.TestCase):
    def test_enabled_cgroup_scope_is_passed_and_removed(self) -> None:
        job_id = uuid4()
        client = MagicMock()
        client.info.return_value = {"CgroupDriver": "cgroupfs"}
        compile_container = MagicMock()
        execution_container = MagicMock()
        task_tracker = MagicMock()
        cgroup_scope = MagicMock()
        workspace = VolumeWorkspace(job_id, "codeguard-job-test")
        delegated_root = Path("/sys/fs/cgroup/codeguard")
        job = RunnerRequest(
            job_id=job_id,
            language=RunnerLanguage.CPP,
            code="int main() { return 0; }",
            policy=PolicyLimits(
                timeout_ms=1000,
                memory_limit_mb=64,
                pids_limit=8,
                cpu_bandwidth=1.0,
                cpu_time_limit_ms=1000,
            ),
            created_at=datetime.now(timezone.utc),
        )

        with (
            patch.object(settings, "execution_cgroup_enabled", True),
            patch.object(settings, "task_tracker_enabled", True),
            patch.object(settings, "execution_cgroup_root", delegated_root),
            patch(
                "runner.pipeline.executor.TaskTrackerClient.from_settings",
                return_value=task_tracker,
            ),
            patch(
                "runner.metrics.cgroup_scope.ExecutionCgroupScope.create",
                return_value=cgroup_scope,
            ) as create_scope,
            patch(
                "runner.pipeline.executor.get_docker_client",
                return_value=client,
            ),
            patch(
                "runner.pipeline.executor.create_workspace",
                return_value=workspace,
            ),
            patch(
                "runner.pipeline.executor.create_compile_container",
                return_value=compile_container,
            ),
            patch(
                "runner.pipeline.executor.compile_source",
                return_value=CompileResult(
                    success=True,
                    stdout="",
                    stderr="",
                    exit_code=0,
                    artifact_ready=True,
                ),
            ),
            patch(
                "runner.pipeline.executor.create_execution_container",
                return_value=execution_container,
            ) as create_execution,
            patch(
                "runner.pipeline.executor.execute_program",
                return_value=ExecutionResult(
                    exit_code=0,
                    stdout="",
                    stderr="",
                    cpu_time_ms=75,
                    pids_peak=18,
                    user_task_peak=15,
                    process_at_user_task_peak=3,
                    thread_at_user_task_peak=12,
                ),
            ) as execute_program,
            patch("runner.pipeline.executor.remove_workspace"),
        ):
            response = execute_job(job)

        create_scope.assert_called_once_with(
            root=delegated_root,
            run_id=response.run_id,
            driver="cgroupfs",
        )
        self.assertIs(
            create_execution.call_args.kwargs["cgroup_scope"],
            cgroup_scope,
        )
        self.assertEqual(
            create_execution.call_args.kwargs["cpu_bandwidth"],
            1.0,
        )
        self.assertIs(
            execute_program.call_args.kwargs["cgroup_scope"],
            cgroup_scope,
        )
        self.assertEqual(
            execute_program.call_args.kwargs["cpu_time_limit_ms"],
            1000,
        )
        self.assertIs(
            execute_program.call_args.kwargs["task_tracker"],
            task_tracker,
        )
        self.assertEqual(response.resource_usage.pids_peak, 18)
        self.assertEqual(response.resource_usage.user_task_peak, 15)
        self.assertEqual(response.resource_usage.cpu_time_ms, 75)
        self.assertEqual(
            response.resource_usage.process_at_user_task_peak,
            3,
        )
        self.assertEqual(
            response.resource_usage.thread_at_user_task_peak,
            12,
        )
        cgroup_scope.remove.assert_called_once_with()

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
                cpu_bandwidth=1.0,
                cpu_time_limit_ms=1000,
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
