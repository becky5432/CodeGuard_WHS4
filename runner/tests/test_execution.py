import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import docker

from runner.exceptions import CgroupScopeError, ContainerExecutionError
from runner.metrics.cgroup_scope import CgroupMetrics
from runner.pipeline.execution import (
    create_execution_container,
    execute_program,
)
from runner.pipeline.workspace import VolumeWorkspace


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.container = MagicMock()
        self.client.containers.create.return_value = self.container
        self.container.wait.return_value = {"StatusCode": 0}
        self.container.attach.return_value = [(b"Hello\n", None)]
        self.cgroup_scope = MagicMock()
        self.cgroup_scope.docker_parent = "/codeguard/execution-test"
        self.cgroup_scope.snapshot.return_value = CgroupMetrics(
            memory_peak_bytes=1024,
            pids_peak=1,
        )
        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )
        self.run_id = uuid4()

    def test_create_execution_container_returns_registered_container(self) -> None:
        result = create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_limit=1.0,
            pids_limit=10,
            cgroup_scope=self.cgroup_scope,
        )

        self.assertIs(result, self.container)
        self.client.containers.create.assert_called_once_with(
            image="codeguard-cpp:dev",
            command=["/workspace/main"],
            volumes={
                self.workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "ro",
                }
            },
            detach=True,
            mem_limit=128 * 1024 * 1024,
            memswap_limit=128 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            pids_limit=10,
            cgroup_parent="/codeguard/execution-test",
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(self.workspace.job_id),
                "codeguard.run_id": str(self.run_id),
                "codeguard.stage": "execute",
            },
        )

    def test_create_execution_container_uses_stdin_file(self) -> None:
        create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="21\n",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_limit=1.0,
            pids_limit=10,
            cgroup_scope=self.cgroup_scope,
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(
            command,
            ["sh", "-c", "exec /workspace/main < /workspace/stdin"],
        )

    def test_create_execution_container_uses_cgroup_parent(self) -> None:
        cgroup_scope = MagicMock()
        cgroup_scope.docker_parent = "/codeguard/execution-test"

        create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_limit=1.0,
            pids_limit=10,
            cgroup_scope=cgroup_scope,
        )

        self.assertEqual(
            self.client.containers.create.call_args.kwargs["cgroup_parent"],
            "/codeguard/execution-test",
        )

    def test_execute_program_collects_result_without_removing_container(self) -> None:
        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=self.cgroup_scope,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "Hello\n")
        self.assertEqual(result.stderr, "")
        self.container.start.assert_called_once_with()
        self.container.wait.assert_called_once_with()
        self.container.remove.assert_not_called()

    def test_create_execution_container_wraps_docker_failure(self) -> None:
        self.client.containers.create.side_effect = docker.errors.APIError(
            "create failed",
        )

        with self.assertRaises(ContainerExecutionError):
            create_execution_container(
                client=self.client,
                workspace=self.workspace,
                stdin="",
                job_id=self.workspace.job_id,
                run_id=self.run_id,
                memory_limit_mb=128,
                cpu_limit=1.0,
                pids_limit=10,
                cgroup_scope=self.cgroup_scope,
            )

    def test_execute_program_returns_system_error_without_removing_container(self) -> None:
        self.container.start.side_effect = docker.errors.APIError("start failed")

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=self.cgroup_scope,
        )

        self.assertIsNotNone(result.system_error)
        self.assertIsNone(result.exit_code)
        self.container.remove.assert_not_called()

    @patch("runner.pipeline.execution.PidsLimitMonitor")
    def test_execute_program_uses_parent_cgroup_metrics_only(
        self,
        pids_monitor_class,
    ) -> None:
        pids_monitor_class.return_value.exceeded.return_value = False
        cgroup_scope = MagicMock()
        cgroup_scope.snapshot.return_value = CgroupMetrics(
            memory_peak_bytes=16 * 1024 * 1024,
            pids_peak=18,
        )

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
        )

        self.assertEqual(result.memory_peak_bytes, 16 * 1024 * 1024)
        self.assertEqual(result.pids_peak, 18)
        cgroup_scope.snapshot.assert_called_once_with()
        self.container.stats.assert_not_called()

    @patch("runner.pipeline.execution.PidsLimitMonitor")
    def test_execute_program_propagates_cgroup_snapshot_error(
        self,
        pids_monitor_class,
    ) -> None:
        pids_monitor_class.return_value.exceeded.return_value = False
        self.cgroup_scope.snapshot.side_effect = CgroupScopeError(
            "Execution cgroup 측정값을 읽지 못했습니다.",
        )

        with self.assertRaises(CgroupScopeError):
            execute_program(
                container=self.container,
                job_id=self.workspace.job_id,
                run_id=self.run_id,
                timeout_ms=2000,
                cgroup_scope=self.cgroup_scope,
            )


if __name__ == "__main__":
    unittest.main()
