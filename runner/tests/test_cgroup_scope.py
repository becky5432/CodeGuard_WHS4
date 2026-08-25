import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

from runner.exceptions import CgroupScopeError
from runner.metrics.cgroup_scope import (
    ExecutionCgroupScope,
    validate_docker_cgroup_driver,
)


class ExecutionCgroupScopeTests(unittest.TestCase):
    def test_rejects_non_cgroupfs_docker_driver(self) -> None:
        client = MagicMock()
        client.info.return_value = {"CgroupDriver": "systemd"}

        with self.assertRaises(CgroupScopeError) as context:
            validate_docker_cgroup_driver(client)

        self.assertIn("cgroupfs", context.exception.message)

    def test_accepts_cgroupfs_docker_driver(self) -> None:
        client = MagicMock()
        client.info.return_value = {"CgroupDriver": "cgroupfs"}

        validate_docker_cgroup_driver(client)

    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.cgroup_mount = Path(self.temp_directory.name)
        self.delegated_root = self.cgroup_mount / "codeguard"
        self.delegated_root.mkdir()
        (self.cgroup_mount / "cgroup.controllers").write_text(
            "cpu memory pids\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_create_builds_job_scope_and_docker_parent(self) -> None:
        run_id = uuid4()

        scope = ExecutionCgroupScope.create(
            root=self.delegated_root,
            run_id=run_id,
            cgroup_mount=self.cgroup_mount,
        )

        self.assertTrue(scope.path.is_dir())
        self.assertEqual(
            scope.docker_parent,
            f"/codeguard/execution-{run_id.hex}",
        )

        scope.remove()
        self.assertFalse(scope.path.exists())

    def test_snapshot_reads_peak_and_limit_events(self) -> None:
        scope = ExecutionCgroupScope.create(
            root=self.delegated_root,
            run_id=uuid4(),
            cgroup_mount=self.cgroup_mount,
        )
        (scope.path / "memory.peak").write_text("20971520\n", encoding="utf-8")
        (scope.path / "pids.peak").write_text("64\n", encoding="utf-8")
        (scope.path / "memory.events").write_text(
            "low 0\nhigh 0\nmax 1\noom 1\noom_kill 1\n",
            encoding="utf-8",
        )
        (scope.path / "pids.events").write_text(
            "max 2\n",
            encoding="utf-8",
        )

        metrics = scope.snapshot()

        self.assertEqual(metrics.memory_peak_bytes, 20971520)
        self.assertEqual(metrics.pids_peak, 64)
        self.assertTrue(metrics.oom_killed)
        self.assertTrue(metrics.pids_limit_exceeded)

    def test_snapshot_returns_none_when_peak_files_are_missing(self) -> None:
        scope = ExecutionCgroupScope.create(
            root=self.delegated_root,
            run_id=uuid4(),
            cgroup_mount=self.cgroup_mount,
        )

        metrics = scope.snapshot()

        self.assertIsNone(metrics.memory_peak_bytes)
        self.assertIsNone(metrics.pids_peak)
        self.assertFalse(metrics.oom_killed)
        self.assertFalse(metrics.pids_limit_exceeded)


if __name__ == "__main__":
    unittest.main()
