import unittest
from unittest.mock import MagicMock

from runner.metrics.cgroup_scope import ExecutionCgroupScope
from runner.metrics.cpu_usage_sampler import CpuUsageSampler


class CpuUsageSamplerTests(unittest.TestCase):
    def test_does_not_sample_before_100ms(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        sampler = CpuUsageSampler(
            cgroup_scope=cgroup_scope,
            start_cpu_usec=1_000_000,
            start_time=10.0,
        )

        sampler.sample_if_due(10.099)

        self.assertEqual(sampler.samples, [])
        cgroup_scope.read_cpu_usage_usec.assert_not_called()

    def test_creates_sample_after_100ms(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_cpu_usage_usec.return_value = 1_038_000

        sampler = CpuUsageSampler(
            cgroup_scope=cgroup_scope,
            start_cpu_usec=1_000_000,
            start_time=10.0,
        )

        sampler.sample_if_due(10.102)

        self.assertEqual(len(sampler.samples), 1)

        sample = sampler.samples[0]
        self.assertEqual(sample.elapsed_ms, 102)
        self.assertEqual(sample.interval_ms, 102)
        self.assertEqual(sample.cpu_time_delta_ms, 38)

    def test_uses_actual_interval_between_samples(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_cpu_usage_usec.side_effect = [
            1_038_000,
            1_110_000,
        ]

        sampler = CpuUsageSampler(
            cgroup_scope=cgroup_scope,
            start_cpu_usec=1_000_000,
            start_time=10.0,
        )

        sampler.sample_if_due(10.102)
        sampler.sample_if_due(10.204)

        self.assertEqual(len(sampler.samples), 2)

        second = sampler.samples[1]
        self.assertEqual(second.elapsed_ms, 204)
        self.assertEqual(second.interval_ms, 102)
        self.assertEqual(second.cpu_time_delta_ms, 72)

    def test_skips_failed_read_and_next_sample_covers_full_interval(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_cpu_usage_usec.side_effect = [
            1_038_000,
            None,
            1_110_000,
        ]

        sampler = CpuUsageSampler(
            cgroup_scope=cgroup_scope,
            start_cpu_usec=1_000_000,
            start_time=10.0,
        )

        sampler.sample_if_due(10.101)
        sampler.sample_if_due(10.201)
        sampler.sample_if_due(10.301)

        self.assertEqual(len(sampler.samples), 2)

        second = sampler.samples[1]
        self.assertEqual(second.elapsed_ms, 301)
        self.assertEqual(second.interval_ms, 200)
        self.assertEqual(second.cpu_time_delta_ms, 72)

    def test_final_sample_handles_execution_shorter_than_100ms(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)

        sampler = CpuUsageSampler(
            cgroup_scope=cgroup_scope,
            start_cpu_usec=1_000_000,
            start_time=10.0,
        )

        sampler.sample_final(
            finished_at=10.062,
            final_cpu_usec=1_025_000,
        )

        self.assertEqual(len(sampler.samples), 1)

        sample = sampler.samples[0]
        self.assertEqual(sample.elapsed_ms, 61)
        self.assertEqual(sample.interval_ms, 61)
        self.assertEqual(sample.cpu_time_delta_ms, 25)

    def test_final_sample_adds_partial_interval_after_periodic_sample(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_cpu_usage_usec.return_value = 1_038_000

        sampler = CpuUsageSampler(
            cgroup_scope=cgroup_scope,
            start_cpu_usec=1_000_000,
            start_time=10.0,
        )

        sampler.sample_if_due(10.102)
        sampler.sample_final(
            finished_at=10.147,
            final_cpu_usec=1_057_000,
        )

        self.assertEqual(len(sampler.samples), 2)

        final = sampler.samples[1]
        self.assertEqual(final.elapsed_ms, 147)
        self.assertEqual(final.interval_ms, 45)
        self.assertEqual(final.cpu_time_delta_ms, 19)

    def test_final_sample_is_skipped_when_cpu_value_is_missing(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)

        sampler = CpuUsageSampler(
            cgroup_scope=cgroup_scope,
            start_cpu_usec=1_000_000,
            start_time=10.0,
        )

        sampler.sample_final(
            finished_at=10.050,
            final_cpu_usec=None,
        )

        self.assertEqual(sampler.samples, [])


if __name__ == "__main__":
    unittest.main()