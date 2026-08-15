import tempfile
import unittest
from pathlib import Path

from runner.metrics.cgroup_reader import (
    CgroupSnapshot,
    read_snapshot,
    resolve_cgroup_path,
)


class CgroupReaderTests(unittest.TestCase):
    def test_resolve_unified_cgroup_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc_root = root / "proc"
            cgroup_root = root / "cgroup"
            process_dir = proc_root / "123"
            expected = cgroup_root / "docker" / "container-id"
            process_dir.mkdir(parents=True)
            expected.mkdir(parents=True)
            (process_dir / "cgroup").write_text(
                "0::/docker/container-id\n",
                encoding="utf-8",
            )

            result = resolve_cgroup_path(
                pid=123,
                proc_root=proc_root,
                cgroup_root=cgroup_root,
            )

            self.assertEqual(result, expected.resolve())

    def test_resolve_returns_none_for_missing_or_legacy_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc_root = root / "proc"
            process_dir = proc_root / "123"
            process_dir.mkdir(parents=True)
            (process_dir / "cgroup").write_text(
                "2:memory:/docker/legacy\n",
                encoding="utf-8",
            )

            result = resolve_cgroup_path(
                pid=123,
                proc_root=proc_root,
                cgroup_root=root / "cgroup",
            )

            self.assertIsNone(result)

    def test_read_snapshot_parses_cgroup_v2_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "memory.peak").write_text("4096\n", encoding="utf-8")
            (path / "memory.events").write_text(
                "low 0\nhigh 1\nmax 2\noom 1\noom_kill 3\n",
                encoding="utf-8",
            )
            (path / "pids.peak").write_text("7\n", encoding="utf-8")
            (path / "pids.events").write_text("max 4\n", encoding="utf-8")
            (path / "cpu.stat").write_text(
                "usage_usec 1250\nuser_usec 1000\nsystem_usec 250\n",
                encoding="utf-8",
            )

            snapshot = read_snapshot(path)

            self.assertEqual(
                snapshot,
                CgroupSnapshot(
                    memory_peak_bytes=4096,
                    pids_peak=7,
                    cpu_usage_usec=1250,
                    oom_kill_count=3,
                    pids_max_events=4,
                ),
            )

    def test_read_snapshot_tolerates_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            snapshot = read_snapshot(Path(directory))

            self.assertEqual(
                snapshot,
                CgroupSnapshot(
                    memory_peak_bytes=None,
                    pids_peak=None,
                    cpu_usage_usec=None,
                    oom_kill_count=0,
                    pids_max_events=0,
                ),
            )


if __name__ == "__main__":
    unittest.main()
