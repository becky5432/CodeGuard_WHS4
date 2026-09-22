from runner.metrics.cgroup_scope import ExecutionCgroupScope
from runner.models.result import CpuUsageSample


class CpuUsageSampler:
    SAMPLE_INTERVAL_MS = 100

    def __init__(
        self,
        cgroup_scope: ExecutionCgroupScope,
        start_cpu_usec: int,
        start_time: float,
    ) -> None:
        self.cgroup_scope = cgroup_scope
        self.start_time = start_time

        self.previous_cpu_usec = start_cpu_usec
        self.previous_elapsed_ms = 0

        self.next_sample_elapsed_ms = self.SAMPLE_INTERVAL_MS
        self.samples: list[CpuUsageSample] = []

    def sample_if_due(self, now: float) -> None:
        elapsed_ms = int((now - self.start_time) * 1000)

        if elapsed_ms < self.next_sample_elapsed_ms:
            return

        current_cpu_usec = self.cgroup_scope.read_cpu_usage_usec()

        # 다음 샘플 기준 시점을 다음 100ms 경계로 이동
        self.next_sample_elapsed_ms = (
            (elapsed_ms // self.SAMPLE_INTERVAL_MS) + 1
        ) * self.SAMPLE_INTERVAL_MS

        if current_cpu_usec is None:
            return

        interval_ms = elapsed_ms - self.previous_elapsed_ms
        if interval_ms <= 0:
            return

        cpu_time_delta_ms = max(
            current_cpu_usec - self.previous_cpu_usec,
            0,
        ) // 1000

        self.samples.append(
            CpuUsageSample(
                elapsed_ms=elapsed_ms,
                interval_ms=interval_ms,
                cpu_time_delta_ms=cpu_time_delta_ms,
            )
        )

        self.previous_cpu_usec = current_cpu_usec
        self.previous_elapsed_ms = elapsed_ms

    def sample_final(
        self,
        finished_at: float,
        final_cpu_usec: int | None,
    ) -> None:
        if final_cpu_usec is None:
            return

        elapsed_ms = int((finished_at - self.start_time) * 1000)
        interval_ms = elapsed_ms - self.previous_elapsed_ms

        if interval_ms <= 0:
            return

        cpu_time_delta_ms = max(
            final_cpu_usec - self.previous_cpu_usec,
            0,
        ) // 1000

        self.samples.append(
            CpuUsageSample(
                elapsed_ms=elapsed_ms,
                interval_ms=interval_ms,
                cpu_time_delta_ms=cpu_time_delta_ms,
            )
        )

        self.previous_cpu_usec = final_cpu_usec
        self.previous_elapsed_ms = elapsed_ms