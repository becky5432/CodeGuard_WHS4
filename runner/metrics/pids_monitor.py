import docker


class PidsLimitMonitor:
    def __init__(self, container) -> None:
        self.container = container
        self.cgroup_path: str | None = None
        self.events_max: int | None = None

    def start(self) -> None:
        self.cgroup_path = self._get_cgroup_path()

    def exceeded(self) -> bool:
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