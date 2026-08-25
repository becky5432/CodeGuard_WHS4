import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from runner.exceptions import CgroupScopeError


logger = logging.getLogger("runner")
DEFAULT_CGROUP_MOUNT = Path("/sys/fs/cgroup")


def validate_docker_cgroup_driver(client) -> None:
    """현재 직접 경로 방식이 지원하는 Docker cgroup 드라이버인지 확인한다."""
    try:
        driver = client.info().get("CgroupDriver")
    except Exception as exc:
        raise CgroupScopeError(
            "Docker cgroup 드라이버를 확인하지 못했습니다.",
            details={"reason": str(exc)},
        ) from exc

    if driver != "cgroupfs":
        raise CgroupScopeError(
            "Execution 전용 cgroup은 현재 Docker cgroupfs 드라이버만 지원합니다.",
            details={"cgroup_driver": driver},
        )


@dataclass(frozen=True)
class CgroupMetrics:
    memory_peak_bytes: int | None = None
    pids_peak: int | None = None
    oom_killed: bool = False
    pids_limit_exceeded: bool = False


@dataclass(frozen=True)
class ExecutionCgroupScope:
    path: Path
    docker_parent: str

    @classmethod
    def create(
        cls,
        root: Path,
        run_id: UUID,
        cgroup_mount: Path = DEFAULT_CGROUP_MOUNT,
    ) -> "ExecutionCgroupScope":
        mount = cgroup_mount.resolve()
        delegated_root = root.resolve()

        try:
            delegated_root.relative_to(mount)
        except ValueError as exc:
            raise CgroupScopeError(
                "Execution cgroup 루트가 cgroup v2 마운트 밖에 있습니다.",
                details={"root": str(delegated_root), "mount": str(mount)},
            ) from exc

        if not (mount / "cgroup.controllers").is_file():
            raise CgroupScopeError(
                "cgroup v2 실행환경을 확인하지 못했습니다.",
                details={"mount": str(mount)},
            )

        if not delegated_root.is_dir():
            raise CgroupScopeError(
                "위임된 Execution cgroup 루트를 찾지 못했습니다.",
                details={"root": str(delegated_root)},
            )

        scope_path = delegated_root / f"execution-{run_id.hex}"
        try:
            scope_path.mkdir()
        except OSError as exc:
            raise CgroupScopeError(
                "Execution 전용 cgroup 생성에 실패했습니다.",
                details={"path": str(scope_path), "reason": str(exc)},
            ) from exc

        relative_path = scope_path.relative_to(mount).as_posix()
        return cls(
            path=scope_path,
            docker_parent=f"/{relative_path}",
        )

    def snapshot(self) -> CgroupMetrics:
        memory_events = self._read_events("memory.events")
        pids_events = self._read_events("pids.events")
        return CgroupMetrics(
            memory_peak_bytes=self._read_int("memory.peak"),
            pids_peak=self._read_int("pids.peak"),
            oom_killed=memory_events.get("oom_kill", 0) > 0,
            pids_limit_exceeded=pids_events.get("max", 0) > 0,
        )

    def remove(self) -> None:
        try:
            self.path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CgroupScopeError(
                "Execution 전용 cgroup 정리에 실패했습니다.",
                details={"path": str(self.path), "reason": str(exc)},
            ) from exc

    def _read_int(self, filename: str) -> int | None:
        try:
            return int((self.path / filename).read_text(encoding="utf-8").strip())
        except (FileNotFoundError, OSError, ValueError) as exc:
            logger.warning(
                "event=cgroup_metric_read_error path=%s error=%s",
                self.path / filename,
                exc,
            )
            return None

    def _read_events(self, filename: str) -> dict[str, int]:
        try:
            lines = (self.path / filename).read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError) as exc:
            logger.warning(
                "event=cgroup_event_read_error path=%s error=%s",
                self.path / filename,
                exc,
            )
            return {}

        events: dict[str, int] = {}
        for line in lines:
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                events[parts[0]] = int(parts[1])
            except ValueError:
                continue
        return events
