import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from runner.metrics.cgroup_reader import (
    read_cgroup_snapshot,
    resolve_cgroup_path,
    resolve_container_cgroup_path,
)


class CgroupReaderTests(unittest.TestCase):
    def test_resolve_cgroup_path_uses_unified_v2_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proc_root = root / "proc"
            cgroup_root = root / "sys" / "fs" / "cgroup"
            pid_dir = proc_root / "4321"
            pid_dir.mkdir(parents=True)
            cgroup_root.mkdir(parents=True)
            (pid_dir / "cgroup").write_text(
                "0::/system.slice/docker-test.scope\n",
                encoding="utf-8",
            )

            result = resolve_cgroup_path(
                4321,
                proc_root=proc_root,
                cgroup_root=cgroup_root,
            )

            self.assertEqual(
                result,
                cgroup_root / "system.slice" / "docker-test.scope",
            )

    def test_read_cgroup_snapshot_reads_exact_peaks_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cgroup_path = Path(temp_dir)
            (cgroup_path / "memory.peak").write_text("4096\n", encoding="utf-8")
            (cgroup_path / "pids.peak").write_text("32\n", encoding="utf-8")
            (cgroup_path / "pids.events.local").write_text(
                "max 4\n",
                encoding="utf-8",
            )

            snapshot = read_cgroup_snapshot(cgroup_path)

            self.assertEqual(snapshot.memory_peak_bytes, 4096)
            self.assertEqual(snapshot.pids_peak, 32)
            self.assertTrue(snapshot.pids_limit_exceeded)

    def test_resolve_container_cgroup_path_uses_inspected_host_pid(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            proc_root = root / "proc"
            cgroup_root = root / "sys" / "fs" / "cgroup"
            pid_dir = proc_root / "9876"
            pid_dir.mkdir(parents=True)
            cgroup_root.mkdir(parents=True)
            (pid_dir / "cgroup").write_text(
                "0::/docker/test-container\n",
                encoding="utf-8",
            )
            container = MagicMock()
            container.attrs = {"State": {"Pid": 9876}}

            result = resolve_container_cgroup_path(
                container,
                proc_root=proc_root,
                cgroup_root=cgroup_root,
            )

            container.reload.assert_called_once_with()
            self.assertEqual(result, cgroup_root / "docker" / "test-container")

    def test_read_cgroup_snapshot_allows_missing_optional_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = read_cgroup_snapshot(Path(temp_dir))

            self.assertIsNone(snapshot.memory_peak_bytes)
            self.assertIsNone(snapshot.pids_peak)
            self.assertFalse(snapshot.pids_limit_exceeded)


if __name__ == "__main__":
    unittest.main()
