import unittest
from unittest.mock import MagicMock

from runner.metrics.resource_monitor import ResourceMonitor


class ResourceMonitorTests(unittest.TestCase):
    def test_collect_tracks_peaks_from_mocked_docker_stats(self) -> None:
        container = MagicMock()
        container.stats.side_effect = [
            {"memory_stats": {"usage": 100}, "pids_stats": {"current": 2}},
            iter(
                [
                    {
                        "memory_stats": {"usage": 200},
                        "pids_stats": {"current": 5},
                    },
                    {
                        "memory_stats": {"usage": 150},
                        "pids_stats": {"current": 3},
                    },
                ]
            ),
        ]
        monitor = ResourceMonitor(container)

        monitor._collect()

        self.assertEqual(monitor.memory_peak_bytes, 200)
        self.assertEqual(monitor.pids_peak, 5)
        self.assertEqual(container.stats.call_count, 2)

    def test_record_tracks_memory_and_pids_peaks(self) -> None:
        monitor = ResourceMonitor(MagicMock())

        for sample in (
            {"memory_stats": {"usage": 100}, "pids_stats": {"current": 2}},
            {"memory_stats": {"usage": 200}, "pids_stats": {"current": 5}},
            {"memory_stats": {"usage": 150}, "pids_stats": {"current": 3}},
        ):
            monitor._record(sample)

        self.assertEqual(monitor.memory_peak_bytes, 200)
        self.assertEqual(monitor.pids_peak, 5)

    def test_record_ignores_missing_or_empty_stats(self) -> None:
        monitor = ResourceMonitor(MagicMock())

        for sample in (
            {},
            {"memory_stats": {}},
            {"pids_stats": {}},
            {"memory_stats": None, "pids_stats": None},
        ):
            monitor._record(sample)

        self.assertIsNone(monitor.memory_peak_bytes)
        self.assertIsNone(monitor.pids_peak)


if __name__ == "__main__":
    unittest.main()
