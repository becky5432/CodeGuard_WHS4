import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import docker

from runner.exceptions import ContainerExecutionError, TaskTrackingError
from runner.metrics.cgroup_scope import CgroupMetrics
from runner.metrics.task_tracker import (
    ExecutionCgroupIdentity,
    PidsPeakSnapshot,
)
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
        )

        self.assertIs(result, self.container)
        self.client.containers.create.assert_called_once_with(
            image="codeguard-cpp:dev",
            command=[
                "/usr/local/bin/codeguard-init",
                "--",
                "/workspace/main",
            ],
            volumes={
                self.workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "ro",
                }
            },
            detach=True,
            network_mode="none",
            user="10001:10001",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges=true"],
            mem_limit=128 * 1024 * 1024,
            memswap_limit=128 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            pids_limit=10,
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
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(
            command,
            [
                "/usr/local/bin/codeguard-init",
                "--stdin",
                "/workspace/stdin",
                "--",
                "/workspace/main",
            ],
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
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "Hello\n")
        self.assertEqual(result.stderr, "")
        self.container.start.assert_called_once_with()
        self.container.kill.assert_called_once_with(signal="SIGUSR1")
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
            )

    def test_execute_program_returns_system_error_without_removing_container(self) -> None:
        self.container.start.side_effect = docker.errors.APIError("start failed")

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
        )

        self.assertIsNotNone(result.system_error)
        self.assertIsNone(result.exit_code)
        self.container.remove.assert_not_called()

    @patch("runner.pipeline.execution.PidsLimitMonitor")
    @patch("runner.pipeline.execution.ResourceMonitor")
    def test_execute_program_prefers_parent_cgroup_peak_values(
        self,
        resource_monitor_class,
        pids_monitor_class,
    ) -> None:
        resource_monitor_class.return_value.memory_peak_bytes = 100
        pids_monitor_class.return_value.pids_peak = 2
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

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_execute_program_returns_user_task_peak_snapshot(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {"Memory": 128 * 1024 * 1024},
        }
        resolve_cgroup.return_value = ExecutionCgroupIdentity(
            cgroup_id=999,
            pids_current=1,
        )
        tracker = MagicMock()
        tracker.snapshot.return_value = PidsPeakSnapshot(
            user_task_peak=15,
            process_at_user_task_peak=3,
            thread_at_user_task_peak=12,
        )
        cgroup_scope = MagicMock()
        cgroup_scope.snapshot.return_value = CgroupMetrics(pids_peak=18)

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
            task_tracker=tracker,
        )

        tracker.register_cgroup.assert_called_once_with(self.run_id, 999, 1)
        tracker.register_root.assert_called_once_with(self.run_id, 321)
        tracker.snapshot.assert_called_once_with(self.run_id)
        tracker.remove.assert_called_once_with(self.run_id)
        self.container.kill.assert_called_once_with(signal="SIGUSR1")
        self.assertEqual(result.pids_peak, 18)
        self.assertEqual(result.user_task_peak, 15)
        self.assertEqual(result.process_at_user_task_peak, 3)
        self.assertEqual(result.thread_at_user_task_peak, 12)

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_execute_program_keeps_user_snapshot_when_cgroup_peak_differs(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {},
        }
        resolve_cgroup.return_value = ExecutionCgroupIdentity(999, 1)
        tracker = MagicMock()
        tracker.snapshot.return_value = PidsPeakSnapshot(
            user_task_peak=1,
            process_at_user_task_peak=1,
            thread_at_user_task_peak=0,
        )
        cgroup_scope = MagicMock()
        cgroup_scope.snapshot.return_value = CgroupMetrics(pids_peak=18)

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
            task_tracker=tracker,
        )

        self.assertEqual(result.pids_peak, 18)
        self.assertEqual(result.user_task_peak, 1)
        self.assertEqual(result.process_at_user_task_peak, 1)
        self.assertEqual(result.thread_at_user_task_peak, 0)
        tracker.remove.assert_called_once_with(self.run_id)

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_tracker_registration_failure_does_not_block_execution(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {},
        }
        resolve_cgroup.return_value = ExecutionCgroupIdentity(999, 1)
        tracker = MagicMock()
        tracker.register_cgroup.side_effect = TaskTrackingError("offline")

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            task_tracker=tracker,
        )

        self.assertEqual(result.exit_code, 0)
        self.container.kill.assert_called_once_with(signal="SIGUSR1")
        tracker.register_root.assert_not_called()
        tracker.snapshot.assert_not_called()
        tracker.remove.assert_called_once_with(self.run_id)

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_tracker_pid_reload_failure_does_not_block_execution(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.reload.side_effect = [
            docker.errors.APIError("tracking reload failed"),
            None,
            None,
        ]
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {},
        }
        tracker = MagicMock()

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            task_tracker=tracker,
        )

        self.assertEqual(result.exit_code, 0)
        self.container.kill.assert_called_once_with(signal="SIGUSR1")
        resolve_cgroup.assert_not_called()
        tracker.register_cgroup.assert_not_called()
        tracker.remove.assert_called_once_with(self.run_id)


if __name__ == "__main__":
    unittest.main()
