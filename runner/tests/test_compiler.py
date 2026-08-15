import unittest
from unittest.mock import MagicMock
from uuid import uuid4

import docker
from requests.exceptions import ReadTimeout

from runner.exceptions import ContainerExecutionError, WorkspaceError
from runner.pipeline.compiler import compile_source
from runner.pipeline.workspace import VolumeWorkspace


class CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.container = MagicMock()
        self.client.containers.create.return_value = self.container
        self.container.put_archive.return_value = True
        self.container.wait.return_value = {"StatusCode": 0}
        self.container.logs.side_effect = [b"", b""]
        archive_stream = MagicMock()
        self.container.get_archive.return_value = (
            archive_stream,
            {"name": "main", "size": 1},
        )
        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )

    def test_compile_source_uses_volume_and_uploads_before_start(self) -> None:
        result = compile_source(
            client=self.client,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
        )

        self.assertTrue(result.success)
        create_kwargs = self.client.containers.create.call_args.kwargs
        self.assertEqual(
            create_kwargs["volumes"],
            {
                self.workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "rw",
                }
            },
        )
        self.assertEqual(
            create_kwargs["command"],
            [
                "g++",
                "-std=c++17",
                "/workspace/main.cpp",
                "-o",
                "/workspace/main",
            ],
        )
        method_names = [call[0] for call in self.container.method_calls]
        self.assertLess(
            method_names.index("put_archive"),
            method_names.index("start"),
        )
        self.container.remove.assert_called_once_with(force=True)

    def test_compile_source_selects_c17(self) -> None:
        compile_source(
            client=self.client,
            workspace=self.workspace,
            language="C",
            code="int main(void) { return 0; }",
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(command[0:2], ["gcc", "-std=c17"])
        self.assertEqual(command[2], "/workspace/main.c")

    def test_compile_source_returns_compile_error(self) -> None:
        self.container.wait.return_value = {"StatusCode": 1}
        self.container.logs.side_effect = [b"", b"syntax error"]

        result = compile_source(
            client=self.client,
            workspace=self.workspace,
            language="CPP",
            code="int main( {",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "syntax error")
        self.assertFalse(result.artifact_ready)
        self.container.get_archive.assert_not_called()

    def test_compile_source_kills_container_on_timeout(self) -> None:
        self.container.wait.side_effect = ReadTimeout("timeout")

        result = compile_source(
            client=self.client,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
        )

        self.assertTrue(result.timed_out)
        self.assertFalse(result.success)
        self.assertIsNone(result.exit_code)
        self.container.kill.assert_called_once_with()
        self.container.remove.assert_called_once_with(force=True)

    def test_compile_source_rejects_failed_archive_upload(self) -> None:
        self.container.put_archive.return_value = False

        with self.assertRaises(WorkspaceError):
            compile_source(
                client=self.client,
                workspace=self.workspace,
                language="CPP",
                code="int main() { return 0; }",
            )

        self.container.start.assert_not_called()
        self.container.remove.assert_called_once_with(force=True)

    def test_compile_source_wraps_missing_image(self) -> None:
        self.client.containers.create.side_effect = docker.errors.ImageNotFound(
            "missing",
        )

        with self.assertRaises(ContainerExecutionError):
            compile_source(
                client=self.client,
                workspace=self.workspace,
                language="CPP",
                code="int main() { return 0; }",
            )

    def test_compile_source_marks_missing_artifact_as_not_ready(self) -> None:
        self.container.get_archive.side_effect = docker.errors.NotFound("missing")

        result = compile_source(
            client=self.client,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
        )

        self.assertFalse(result.success)
        self.assertFalse(result.artifact_ready)


if __name__ == "__main__":
    unittest.main()
