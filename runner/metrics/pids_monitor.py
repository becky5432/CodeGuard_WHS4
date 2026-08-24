import docker


class PidsLimitMonitor:
    def __init__(self, container) -> None:
        self.container = container
        self.cgroup_path: str | None = None
        self.events_max: int | None = None
        self.pids_peak: int | None = None

    def start(self) -> None:
        self.cgroup_path = self._get_cgroup_path()
        self.sample()

    def sample(self) -> None:
        """cgroup이 제공하는 PID peak를 읽고, 미지원 시 current의 최댓값을 기록한다."""
        peak = self._read_int_file("pids.peak")
        if peak is None:
            peak = self._read_int_file("pids.current")

        if peak is not None and (
            self.pids_peak is None or peak > self.pids_peak
        ):
            self.pids_peak = peak

    def exceeded(self) -> bool:
        self.sample()
        self.events_max = self._read_pids_events_max()

        return (
            self.events_max is not None
            and self.events_max > 0
        )

    def _get_cgroup_path(self) -> str | None:
        try:
            self.container.reload()

            pid = self.container.attrs.get(
                "State",
                {},
            ).get("Pid")

            if not isinstance(pid, int) or pid <= 0:
                return None

            with open(f"/proc/{pid}/cgroup", "r") as file:
                for line in file:
                    parts = line.strip().split(":", 2)

                    if len(parts) == 3 and parts[0] == "0":
                        return (
                            f"/sys/fs/cgroup/"
                            f"{parts[2].lstrip('/')}"
                        )

        except (
            OSError,
            docker.errors.DockerException,
        ):
            return None

        return None

    def _read_int_file(self, filename: str) -> int | None:
        if self.cgroup_path is None:
            return None

        try:
            with open(f"{self.cgroup_path}/{filename}", "r") as file:
                return int(file.read().strip())
        except (OSError, ValueError):
            return None

    def _read_pids_events_max(self) -> int | None:
        if self.cgroup_path is None:
            return None

        events_path = f"{self.cgroup_path}/pids.events"

        try:
            with open(events_path, "r") as file:
                for line in file:
                    parts = line.split()

                    if (
                        len(parts) == 2
                        and parts[0] == "max"
                    ):
                        return int(parts[1])

        except (OSError, ValueError):
            return None

        return None
