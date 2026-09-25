import unittest
from unittest.mock import MagicMock

from runner.metrics.cgroup_scope import ExecutionCgroupScope
from runner.metrics.memory_usage_sampler import MemoryUsageSampler


class MemoryUsageSamplerTests(unittest.TestCase):
    def test_does_not_sample_before_100ms(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_if_due(10.099)

        self.assertEqual(sampler.samples, [])
        cgroup_scope.read_memory_current_bytes.assert_not_called()

    def test_creates_sample_after_100ms(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_memory_current_bytes.return_value = 12_582_912
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_if_due(10.102)

        self.assertEqual(len(sampler.samples), 1)
        self.assertEqual(sampler.samples[0].elapsed_ms, 102)
        self.assertEqual(sampler.samples[0].memory_bytes, 12_582_912)

    def test_uses_100ms_sampling_boundaries(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_memory_current_bytes.side_effect = [10, 20]
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_if_due(10.101)
        sampler.sample_if_due(10.150)
        sampler.sample_if_due(10.201)

        self.assertEqual(
            [sample.elapsed_ms for sample in sampler.samples],
            [101, 201],
        )
        self.assertEqual(
            [sample.memory_bytes for sample in sampler.samples],
            [10, 20],
        )

    def test_elapsed_ms_increases_between_samples(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_memory_current_bytes.side_effect = [10, 20, 30]
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_if_due(10.101)
        sampler.sample_if_due(10.203)
        sampler.sample_final(10.250, 40)

        elapsed_values = [sample.elapsed_ms for sample in sampler.samples]
        self.assertEqual(elapsed_values, sorted(set(elapsed_values)))

    def test_memory_bytes_is_never_negative(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_memory_current_bytes.return_value = -1
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_if_due(10.101)

        self.assertEqual(sampler.samples[0].memory_bytes, 0)

    def test_final_sample_handles_execution_shorter_than_100ms(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_final(10.062, 8_388_608)

        self.assertEqual(len(sampler.samples), 1)
        self.assertEqual(sampler.samples[0].elapsed_ms, 61)
        self.assertEqual(sampler.samples[0].memory_bytes, 8_388_608)

    def test_failed_read_is_skipped_and_later_sampling_continues(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_memory_current_bytes.side_effect = [None, 100]
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_if_due(10.101)
        sampler.sample_if_due(10.201)

        self.assertEqual(len(sampler.samples), 1)
        self.assertEqual(sampler.samples[0].elapsed_ms, 201)
        self.assertEqual(sampler.samples[0].memory_bytes, 100)

    def test_read_exception_is_nonfatal(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        cgroup_scope.read_memory_current_bytes.side_effect = OSError(
            "read failed"
        )
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_if_due(10.101)

        self.assertEqual(sampler.samples, [])

    def test_final_sample_is_skipped_when_value_is_missing(self) -> None:
        cgroup_scope = MagicMock(spec=ExecutionCgroupScope)
        sampler = MemoryUsageSampler(cgroup_scope, start_time=10.0)

        sampler.sample_final(10.050, None)

        self.assertEqual(sampler.samples, [])


if __name__ == "__main__":
    unittest.main()
