from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CgroupSnapshot:
    memory_peak_bytes: int | None
    pids_peak: int | None
    cpu_usage_usec: int | None
    oom_kill_count: int = 0
    pids_max_events: int = 0


def resolve_cgroup_path(
    pid: int,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> Path | None:
    """PID의 unified cgroup v2 경로를 안전하게 해석한다."""

    try:
        lines = (proc_root / str(pid) / "cgroup").read_text(
            encoding="utf-8",
        ).splitlines()
    except OSError:
        return None

    unified_path = None
    for line in lines:
        if line.startswith("0::"):
            unified_path = line[3:]
            break

    if unified_path is None:
        return None

    root = cgroup_root.resolve()
    candidate = (root / unified_path.lstrip("/")).resolve()
    if not candidate.is_relative_to(root):
        return None
    if not candidate.exists():
        return None
    return candidate


def _read_int(path: Path) -> int | None:
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _read_key_values(path: Path) -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values

    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            values[parts[0]] = int(parts[1])
        except ValueError:
            continue
    return values


def read_snapshot(path: Path) -> CgroupSnapshot:
    """Container 삭제 전에 필요한 cgroup v2 증거를 읽는다."""

    memory_events = _read_key_values(path / "memory.events")
    pids_events = _read_key_values(path / "pids.events")
    cpu_stat = _read_key_values(path / "cpu.stat")

    return CgroupSnapshot(
        memory_peak_bytes=_read_int(path / "memory.peak"),
        pids_peak=_read_int(path / "pids.peak"),
        cpu_usage_usec=cpu_stat.get("usage_usec"),
        oom_kill_count=memory_events.get("oom_kill", 0),
        pids_max_events=pids_events.get("max", 0),
    )
