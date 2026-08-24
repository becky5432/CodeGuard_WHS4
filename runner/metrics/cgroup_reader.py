from dataclasses import dataclass
from pathlib import Path


DEFAULT_PROC_ROOT = Path("/proc")
DEFAULT_CGROUP_ROOT = Path("/sys/fs/cgroup")


@dataclass(frozen=True)
class CgroupSnapshot:
    memory_peak_bytes: int | None = None
    pids_peak: int | None = None
    pids_limit_exceeded: bool = False


def resolve_cgroup_path(
    host_pid: int,
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
    cgroup_root: Path = DEFAULT_CGROUP_ROOT,
) -> Path | None:
    """호스트 PID의 unified cgroup v2 경로를 반환한다."""

    try:
        entries = (proc_root / str(host_pid) / "cgroup").read_text(
            encoding="utf-8",
        )
    except OSError:
        return None

    root = cgroup_root.resolve()
    for line in entries.splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3:
            continue
        hierarchy_id, controllers, relative_path = parts
        if hierarchy_id != "0" or controllers != "":
            continue

        candidate = (root / relative_path.lstrip("/")).resolve()
        if candidate == root or root in candidate.parents:
            return candidate
        return None
    return None


def resolve_container_cgroup_path(
    container,
    *,
    proc_root: Path = DEFAULT_PROC_ROOT,
    cgroup_root: Path = DEFAULT_CGROUP_ROOT,
) -> Path | None:
    """Docker 컨테이너의 호스트 PID를 이용해 cgroup v2 경로를 찾는다."""

    try:
        container.reload()
        host_pid = container.attrs.get("State", {}).get("Pid")
    except Exception:
        return None

    if not isinstance(host_pid, int) or host_pid <= 0:
        return None
    return resolve_cgroup_path(
        host_pid,
        proc_root=proc_root,
        cgroup_root=cgroup_root,
    )


def _read_optional_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_pids_max_events(path: Path) -> int:
    try:
        contents = path.read_text(encoding="utf-8")
    except OSError:
        return 0

    for line in contents.splitlines():
        name, separator, value = line.partition(" ")
        if name != "max" or not separator:
            continue
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


def read_cgroup_snapshot(cgroup_path: Path) -> CgroupSnapshot:
    """컨테이너 cgroup에 커널이 기록한 peak와 제한 이벤트를 읽는다."""

    return CgroupSnapshot(
        memory_peak_bytes=_read_optional_int(cgroup_path / "memory.peak"),
        pids_peak=_read_optional_int(cgroup_path / "pids.peak"),
        pids_limit_exceeded=(
            _read_pids_max_events(cgroup_path / "pids.events.local") > 0
        ),
    )
