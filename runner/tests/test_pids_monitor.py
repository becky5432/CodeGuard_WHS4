import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from runner.metrics.pids_monitor import PidsLimitMonitor


class PidsLimitMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.cgroup_path = Path(self.temp_directory.name)
        self.monitor = PidsLimitMonitor(MagicMock())
        self.monitor.cgroup_path = str(self.cgroup_path)

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_sample_uses_kernel_pids_peak_when_available(self) -> None:
        (self.cgroup_path / "pids.peak").write_text("7\n", encoding="utf-8")

        self.monitor.sample()

        self.assertEqual(self.monitor.pids_peak, 7)

    def test_sample_tracks_pids_current_peak_when_pids_peak_is_unavailable(self) -> None:
        current_path = self.cgroup_path / "pids.current"
        current_path.write_text("2\n", encoding="utf-8")
        self.monitor.sample()
        current_path.write_text("5\n", encoding="utf-8")
        self.monitor.sample()
        current_path.write_text("3\n", encoding="utf-8")
        self.monitor.sample()

        self.assertEqual(self.monitor.pids_peak, 5)

    def test_exceeded_updates_peak_and_reads_pids_events(self) -> None:
        (self.cgroup_path / "pids.peak").write_text("10\n", encoding="utf-8")
        (self.cgroup_path / "pids.events").write_text(
            "max 1\n",
            encoding="utf-8",
        )

        self.assertTrue(self.monitor.exceeded())
        self.assertEqual(self.monitor.pids_peak, 10)


if __name__ == "__main__":
    unittest.main()
