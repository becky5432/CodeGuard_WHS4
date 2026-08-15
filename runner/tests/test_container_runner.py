import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import docker
from requests.exceptions import ReadTimeout

from runner.metrics.cgroup_reader import CgroupSnapshot
from runner.models.job import PolicyLimits
from runner.container.container_runner import execute_program
from runner.pipeline.workspace import VolumeWorkspace


class ContainerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.container = MagicMock()
        self.container.id = "container-id"
        self.client.containers.create.return_value = self.container
        self.container.attach.return_value = iter([(b"Hello\n", None)])
        self.container.wait.return_value = {"StatusCode": 0}
        self.container.attrs = {
            "State": {
                "Pid": 123,
                "OOMKilled": False,
            }
        }
        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )
        self.policy = PolicyLimits(
            timeout_ms=2000,
            memory_limit_mb=128,
            process_limit=10,
            cpu_limit=1.0,
        )

    @patch("runner.container.container_runner.read_snapshot")
    @patch("runner.container.container_runner.resolve_cgroup_path")
    def test_execute_program_applies_hardening_and_policy(
        self,
        resolve_path_mock,
        read_snapshot_mock,
    ) -> None:
        resolve_path_mock.return_value = None

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            policy=self.policy,
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "Hello\n")
        create_kwargs = self.client.containers.create.call_args.kwargs
        self.assertEqual(create_kwargs["command"], ["/workspace/main"])
        self.assertEqual(
            create_kwargs["volumes"],
            {
                self.workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "ro",
                }
            },
        )
        self.assertTrue(create_kwargs["read_only"])
        self.assertTrue(create_kwargs["network_disabled"])
        self.assertEqual(create_kwargs["cap_drop"], ["ALL"])
        self.assertEqual(
            create_kwargs["security_opt"],
            ["no-new-privileges:true"],
        )
        self.assertEqual(create_kwargs["user"], "65534:65534")
        self.assertEqual(create_kwargs["mem_limit"], "128m")
        self.assertEqual(create_kwargs["memswap_limit"], "128m")
        self.assertEqual(create_kwargs["nano_cpus"], 1_000_000_000)
        self.assertEqual(create_kwargs["pids_limit"], 10)
        self.assertIn("/tmp", create_kwargs["tmpfs"])
        self.container.remove.assert_called_once_with(force=True)

    @patch("runner.container.container_runner.resolve_cgroup_path")
    def test_execute_program_kills_container_on_timeout(
        self,
        resolve_path_mock,
    ) -> None:
        resolve_path_mock.return_value = None
        self.container.attach.return_value = iter([])
        self.container.wait.side_effect = ReadTimeout("timeout")

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            policy=self.policy,
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        self.assertTrue(result.timed_out)
        self.container.kill.assert_called()

    @patch("runner.container.container_runner.settings")
    @patch("runner.container.container_runner.resolve_cgroup_path")
    def test_execute_program_caps_output_and_records_limit(
        self,
        resolve_path_mock,
        settings_mock,
    ) -> None:
        resolve_path_mock.return_value = None
        settings_mock.execution_output_limit_bytes = 5
        settings_mock.execution_tmpfs_limit_mb = 64
        settings_mock.runtime_user = "65534:65534"
        settings_mock.cpp_image = "codeguard-cpp:dev"
        self.container.attach.return_value = iter([(b"1234", None), (b"56", None)])

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            policy=self.policy,
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        self.assertTrue(result.output_limit_exceeded)
        self.assertEqual(result.stdout, "12345")
        self.container.kill.assert_called()

    @patch("runner.container.container_runner.read_snapshot")
    @patch("runner.container.container_runner.resolve_cgroup_path")
    def test_execute_program_collects_oom_and_pid_evidence(
        self,
        resolve_path_mock,
        read_snapshot_mock,
    ) -> None:
        resolve_path_mock.return_value = MagicMock()
        read_snapshot_mock.side_effect = [
            CgroupSnapshot(None, None, None, 0, 0),
            CgroupSnapshot(1024, 10, 500, 1, 2),
        ]
        self.container.attrs = {
            "State": {
                "Pid": 123,
                "OOMKilled": True,
            }
        }

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            policy=self.policy,
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        self.assertTrue(result.oom_killed)
        self.assertTrue(result.pids_limit_hit)
        self.assertEqual(result.metrics.memory_peak_bytes, 1024)

    def test_execute_program_returns_system_error_for_docker_failure(self) -> None:
        self.client.containers.create.side_effect = docker.errors.APIError(
            "create failed",
        )

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            policy=self.policy,
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        self.assertIsNotNone(result.system_error)
        self.assertIsNone(result.exit_code)


if __name__ == "__main__":
    unittest.main()
