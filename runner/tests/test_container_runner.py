import unittest
from unittest.mock import MagicMock
from uuid import uuid4

import docker

from runner.container.container_runner import execute_program
from runner.pipeline.workspace import VolumeWorkspace


class ContainerRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.container = MagicMock()
        self.client.containers.create.return_value = self.container
        self.container.wait.return_value = {"StatusCode": 0}
        self.container.logs.side_effect = [b"Hello\n", b""]
        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )

    def test_execute_program_uses_only_basic_container_options(self) -> None:
        run_id = uuid4()

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=run_id,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "Hello\n")
        self.assertEqual(result.stderr, "")
        self.client.containers.create.assert_called_once_with(
            image="codeguard-cpp:dev",
            command=["/workspace/main"],
            volumes={
                self.workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "rw",
                }
            },
            detach=True,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(self.workspace.job_id),
                "codeguard.run_id": str(run_id),
                "codeguard.stage": "execute",
            },
        )
        self.container.start.assert_called_once_with()
        self.container.wait.assert_called_once_with()
        self.container.remove.assert_called_once_with(force=True)

    def test_execute_program_reads_stdin_from_volume(self) -> None:
        execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="21\n",
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(
            command,
            ["sh", "-c", "exec /workspace/main < /workspace/stdin"],
        )

    def test_execute_program_returns_runtime_exit_and_full_output(self) -> None:
        self.container.wait.return_value = {"StatusCode": 3}
        self.container.logs.side_effect = [b"before error", b"runtime failure"]

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        self.assertEqual(result.exit_code, 3)
        self.assertEqual(result.stdout, "before error")
        self.assertEqual(result.stderr, "runtime failure")

    def test_execute_program_returns_system_error_for_docker_failure(self) -> None:
        self.client.containers.create.side_effect = docker.errors.APIError(
            "create failed",
        )

        result = execute_program(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=uuid4(),
        )

        self.assertIsNotNone(result.system_error)
        self.assertIsNone(result.exit_code)


if __name__ == "__main__":
    unittest.main()
