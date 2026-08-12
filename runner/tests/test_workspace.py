import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from runner.config import settings
from runner.exceptions import CleanupError, WorkspaceError
from runner.pipeline.workspace import (
    create_workspace,
    remove_workspace,
    write_source,
)


class WorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.original_workspace_root = settings.workspace_root
        settings.workspace_root = Path(self.temporary_directory.name) / "codeguard-runner"

    def tearDown(self) -> None:
        settings.workspace_root = self.original_workspace_root
        self.temporary_directory.cleanup()

    def test_create_write_and_remove_cpp_workspace(self) -> None:
        job_id = uuid4()

        workspace = create_workspace(job_id)
        source = write_source(workspace, "CPP", "int main() { return 0; }")

        self.assertEqual(workspace, settings.workspace_root / str(job_id))
        self.assertEqual(source.name, "main.cpp")
        self.assertEqual(source.read_text(encoding="utf-8"), "int main() { return 0; }")

        remove_workspace(workspace)

        self.assertFalse(workspace.exists())

    def test_write_source_uses_c_extension(self) -> None:
        workspace = create_workspace(uuid4())

        source = write_source(workspace, "C", "int main(void) { return 0; }")

        self.assertEqual(source.name, "main.c")

    def test_create_workspace_rejects_duplicate_job_id(self) -> None:
        job_id = uuid4()
        create_workspace(job_id)

        with self.assertRaises(WorkspaceError):
            create_workspace(job_id)

    def test_write_source_rejects_unsupported_language(self) -> None:
        workspace = create_workspace(uuid4())

        with self.assertRaises(WorkspaceError):
            write_source(workspace, "PYTHON", "print('hello')")

    def test_remove_workspace_rejects_path_outside_workspace_root(self) -> None:
        outside_path = Path(self.temporary_directory.name) / "outside"
        outside_path.mkdir()

        with self.assertRaises(CleanupError):
            remove_workspace(outside_path)

        self.assertTrue(outside_path.exists())


if __name__ == "__main__":
    unittest.main()
