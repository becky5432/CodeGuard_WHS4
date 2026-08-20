import io
import tarfile
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

import docker

from runner.exceptions import ContainerExecutionError, WorkspaceError
from runner.pipeline.compiler import (
    compile_source,
    create_compile_container,
)
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

    def test_create_compile_container_returns_registered_container(self) -> None:
        result = create_compile_container(
            client=self.client,
            workspace=self.workspace,
            language="CPP",
        )

        self.assertIs(result, self.container)
        self.client.containers.create.assert_called_once_with(
            image="codeguard-cpp:dev",
            command=[
                "g++",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-O0",
                "/workspace/main.cpp",
                "-o",
                "/workspace/main",
            ],
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
                "codeguard.stage": "compile",
            },
        )

    def test_create_compile_container_selects_c17(self) -> None:
        create_compile_container(
            client=self.client,
            workspace=self.workspace,
            language="C",
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(command[0:2], ["gcc", "-std=c17"])
        self.assertEqual(command[2:5], ["-Wall", "-Wextra", "-O0"])
        self.assertEqual(command[5], "/workspace/main.c")

    def test_compile_source_uploads_and_runs_without_removing_container(self) -> None:
        result = compile_source(
            container=self.container,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
            stdin="21\n",
        )

        self.assertTrue(result.success)
        archive_bytes = self.container.put_archive.call_args.kwargs["data"]
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            self.assertEqual(archive.getnames(), ["main.cpp", "stdin"])
        method_names = [call[0] for call in self.container.method_calls]
        self.assertLess(method_names.index("put_archive"), method_names.index("start"))
        self.container.remove.assert_not_called()

    def test_compile_source_returns_compile_error(self) -> None:
        self.container.wait.return_value = {"StatusCode": 1}
        self.container.logs.side_effect = [b"", b"syntax error"]

        result = compile_source(
            container=self.container,
            workspace=self.workspace,
            language="CPP",
            code="int main( {",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "syntax error")
        self.container.remove.assert_not_called()

    def test_compile_source_marks_missing_artifact_as_not_ready(self) -> None:
        self.container.get_archive.side_effect = docker.errors.NotFound("missing")

        result = compile_source(
            container=self.container,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
        )

        self.assertFalse(result.success)
        self.assertFalse(result.artifact_ready)
        self.container.remove.assert_not_called()

    def test_create_compile_container_wraps_missing_image(self) -> None:
        self.client.containers.create.side_effect = docker.errors.ImageNotFound(
            "missing",
        )

        with self.assertRaises(ContainerExecutionError):
            create_compile_container(
                client=self.client,
                workspace=self.workspace,
                language="CPP",
            )

    def test_compile_source_rejects_failed_archive_upload(self) -> None:
        self.container.put_archive.return_value = False

        with self.assertRaises(WorkspaceError):
            compile_source(
                container=self.container,
                workspace=self.workspace,
                language="CPP",
                code="int main() { return 0; }",
            )

        self.container.remove.assert_not_called()


if __name__ == "__main__":
    unittest.main()
