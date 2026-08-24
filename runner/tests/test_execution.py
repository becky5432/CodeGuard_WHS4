import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import docker

from runner.exceptions import ContainerExecutionError
from runner.metrics.cgroup_reader import CgroupSnapshot
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
            ["sh", "-c", "exec /workspace/main < /workspace/stdin"],
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
        self.container.wait.assert_called_once_with()
        self.container.remove.assert_not_called()

    @patch("runner.pipeline.execution.read_cgroup_snapshot")
    @patch("runner.pipeline.execution.resolve_container_cgroup_path")
    def test_execute_program_prefers_exact_cgroup_peaks(
        self,
        resolve_cgroup_path,
        read_cgroup_snapshot,
    ) -> None:
        cgroup_path = Path("/sys/fs/cgroup/system.slice/docker-test.scope")
        resolve_cgroup_path.return_value = cgroup_path
        read_cgroup_snapshot.return_value = CgroupSnapshot(
            memory_peak_bytes=4096,
            pids_peak=32,
            pids_limit_exceeded=True,
        )

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
        )

        resolve_cgroup_path.assert_called_once_with(self.container)
        read_cgroup_snapshot.assert_called_once_with(cgroup_path)
        self.assertEqual(result.memory_peak_bytes, 4096)
        self.assertEqual(result.pids_peak, 32)
        self.assertTrue(result.pids_limit_exceeded)

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


if __name__ == "__main__":
    unittest.main()
