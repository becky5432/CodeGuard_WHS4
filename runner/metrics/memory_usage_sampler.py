import logging

from runner.metrics.cgroup_scope import ExecutionCgroupScope
from runner.models.result import MemoryUsageSample


logger = logging.getLogger("runner")


class MemoryUsageSampler:
    SAMPLE_INTERVAL_MS = 100

    def __init__(
        self,
        cgroup_scope: ExecutionCgroupScope,
        start_time: float,
    ) -> None:
        self.cgroup_scope = cgroup_scope
        self.start_time = start_time

        self.previous_elapsed_ms = 0
        self.next_sample_elapsed_ms = self.SAMPLE_INTERVAL_MS
        self.samples: list[MemoryUsageSample] = []

    def sample_if_due(self, now: float) -> None:
        elapsed_ms = int((now - self.start_time) * 1000)

        if elapsed_ms < self.next_sample_elapsed_ms:
            return

        try:
            memory_bytes = self.cgroup_scope.read_memory_current_bytes()
        except Exception as exc:
            logger.warning(
                "event=execution_memory_sampling_error error=%s",
                exc,
            )
            memory_bytes = None

        # 다음 샘플 기준 시점을 다음 100ms 경계로 이동
        self.next_sample_elapsed_ms = (
            (elapsed_ms // self.SAMPLE_INTERVAL_MS) + 1
        ) * self.SAMPLE_INTERVAL_MS

        if (
            not isinstance(memory_bytes, int)
            or isinstance(memory_bytes, bool)
            or elapsed_ms <= self.previous_elapsed_ms
        ):
            return

        self.samples.append(
            MemoryUsageSample(
                elapsed_ms=elapsed_ms,
                memory_bytes=max(memory_bytes, 0),
            )
        )
        self.previous_elapsed_ms = elapsed_ms

    def sample_final(
        self,
        finished_at: float,
        final_memory_bytes: int | None,
    ) -> None:
        if not isinstance(final_memory_bytes, int) or isinstance(
            final_memory_bytes,
            bool,
        ):
            return

        elapsed_ms = int((finished_at - self.start_time) * 1000)
        if elapsed_ms <= self.previous_elapsed_ms:
            return

        self.samples.append(
            MemoryUsageSample(
                elapsed_ms=elapsed_ms,
                memory_bytes=max(final_memory_bytes, 0),
            )
        )
        self.previous_elapsed_ms = elapsed_ms
