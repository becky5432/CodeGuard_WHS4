from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from runner.exceptions import CgroupScopeError


DEFAULT_CGROUP_MOUNT = Path("/sys/fs/cgroup")
SUPPORTED_CGROUP_DRIVERS = {"cgroupfs", "systemd"}


def validate_docker_cgroup_driver(client) -> str:
    """지원하는 Docker cgroup 드라이버인지 확인하고 이름을 반환한다."""
    try:
        driver = client.info().get("CgroupDriver")
    except Exception as exc:
        raise CgroupScopeError(
            "Docker cgroup 드라이버를 확인하지 못했습니다.",
            details={"reason": str(exc)},
        ) from exc

    if driver not in SUPPORTED_CGROUP_DRIVERS:
        raise CgroupScopeError(
            "지원하지 않는 Docker cgroup 드라이버입니다.",
            details={"cgroup_driver": driver},
        )

    return driver


@dataclass(frozen=True)
class CgroupMetrics:
    memory_peak_bytes: int
    pids_peak: int
    oom_killed: bool = False
    pids_limit_exceeded: bool = False


@dataclass(frozen=True)
class ExecutionCgroupScope:
    path: Path
    docker_parent: str
    runner_managed: bool = True

    @classmethod
    def create(
        cls,
        root: Path,
        run_id: UUID,
        driver: str = "cgroupfs",
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

        if driver == "systemd":
            base_name = delegated_root.name
            # systemd는 unit 이름의 '-'를 slice 계층 구분자로 사용한다.
            # run_id 앞에는 '_'를 사용해 불필요한 중간 slice 생성을 막는다.
            slice_name = f"{base_name}-execution_{run_id.hex}.slice"
            return cls(
                path=mount / f"{base_name}.slice" / slice_name,
                docker_parent=slice_name,
                runner_managed=False,
            )

        if driver != "cgroupfs":
            raise CgroupScopeError(
                "Unsupported Docker cgroup driver.",
                details={"cgroup_driver": driver},
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
        self._require_event(memory_events, "memory.events", "oom_kill")
        self._require_event(pids_events, "pids.events", "max")
        return CgroupMetrics(
            memory_peak_bytes=self._read_int("memory.peak"),
            pids_peak=self._read_int("pids.peak"),
            oom_killed=memory_events.get("oom_kill", 0) > 0,
            pids_limit_exceeded=pids_events.get("max", 0) > 0,
        )

    def remove(self) -> None:
        if not self.runner_managed:
            return

        try:
            self.path.rmdir()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise CgroupScopeError(
                "Execution 전용 cgroup 정리에 실패했습니다.",
                details={"path": str(self.path), "reason": str(exc)},
            ) from exc

    def _read_int(self, filename: str) -> int:
        path = self.path / filename
        try:
            return int(path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise CgroupScopeError(
                "Execution cgroup 측정값을 읽지 못했습니다.",
                details={"path": str(path), "reason": str(exc)},
            ) from exc

    def _read_events(self, filename: str) -> dict[str, int]:
        path = self.path / filename
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (FileNotFoundError, OSError) as exc:
            raise CgroupScopeError(
                "Execution cgroup 이벤트를 읽지 못했습니다.",
                details={"path": str(path), "reason": str(exc)},
            ) from exc

        events: dict[str, int] = {}
        for line in lines:
            parts = line.split()
            if len(parts) != 2:
                raise CgroupScopeError(
                    "Execution cgroup 이벤트 형식이 올바르지 않습니다.",
                    details={"path": str(path), "line": line},
                )
            try:
                events[parts[0]] = int(parts[1])
            except ValueError as exc:
                raise CgroupScopeError(
                    "Execution cgroup 이벤트 형식이 올바르지 않습니다.",
                    details={"path": str(path), "line": line},
                ) from exc
        return events

    def _require_event(
        self,
        events: dict[str, int],
        filename: str,
        event: str,
    ) -> None:
        if event not in events:
            raise CgroupScopeError(
                "Execution cgroup 필수 이벤트를 찾지 못했습니다.",
                details={
                    "path": str(self.path / filename),
                    "event": event,
                },
            )
