import io
import tarfile
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

import docker

from runner.exceptions import CleanupError, WorkspaceError
from runner.pipeline.workspace import (
    VolumeWorkspace,
    build_source_archive,
    create_workspace,
    remove_workspace,
)


class WorkspaceTests(unittest.TestCase):
    def test_create_workspace_creates_labeled_job_volume(self) -> None:
        client = MagicMock()
        volume = MagicMock()
        job_id = uuid4()
        volume.name = f"codeguard-job-{job_id}"
        client.volumes.create.return_value = volume

        workspace = create_workspace(client, job_id)

        self.assertEqual(
            workspace,
            VolumeWorkspace(job_id=job_id, volume_name=volume.name),
        )
        client.volumes.create.assert_called_once_with(
            name=f"codeguard-job-{job_id}",
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(job_id),
                "codeguard.purpose": "workspace",
            },
        )

    def test_create_workspace_wraps_docker_error(self) -> None:
        client = MagicMock()
        client.volumes.create.side_effect = docker.errors.APIError(
            "volume create failed",
        )

        with self.assertRaises(WorkspaceError):
            create_workspace(client, uuid4())

    def test_build_cpp_source_archive(self) -> None:
        code = "// 한글 주석\nint main() { return 0; }"

        archive_bytes = build_source_archive("CPP", code)

        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            self.assertEqual(archive.getnames(), ["main.cpp"])
            member = archive.getmember("main.cpp")
            self.assertEqual(member.mode, 0o600)
            extracted = archive.extractfile(member)
            self.assertIsNotNone(extracted)
            self.assertEqual(extracted.read().decode("utf-8"), code)

    def test_build_c_source_archive(self) -> None:
        archive_bytes = build_source_archive(
            "C",
            "int main(void) { return 0; }",
        )

        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            self.assertEqual(archive.getnames(), ["main.c"])

    def test_build_source_archive_rejects_unsupported_language(self) -> None:
        with self.assertRaises(WorkspaceError):
            build_source_archive("PYTHON", "print('hello')")

    def test_remove_workspace_removes_volume(self) -> None:
        client = MagicMock()
        volume = MagicMock()
        workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )
        client.volumes.get.return_value = volume

        remove_workspace(client, workspace)

        client.volumes.get.assert_called_once_with(workspace.volume_name)
        volume.remove.assert_called_once_with(force=True)

    def test_remove_workspace_is_idempotent_when_volume_is_missing(self) -> None:
        client = MagicMock()
        workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-missing",
        )
        client.volumes.get.side_effect = docker.errors.NotFound("missing")

        remove_workspace(client, workspace)

    def test_remove_workspace_wraps_docker_error(self) -> None:
        client = MagicMock()
        volume = MagicMock()
        workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )
        client.volumes.get.return_value = volume
        volume.remove.side_effect = docker.errors.APIError("remove failed")

        with self.assertRaises(CleanupError):
            remove_workspace(client, workspace)


if __name__ == "__main__":
    unittest.main()
