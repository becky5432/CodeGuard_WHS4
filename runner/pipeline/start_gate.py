"""Synchronize the trusted execution entrypoint without signaling PID 1."""

import io
import os
import tarfile
import time
from pathlib import Path

import docker

from runner.exceptions import ContainerExecutionError, SecurityVerificationError, TaskTrackingError
from runner.security.filesystem_trace import TRACE_DIRECTORY
from runner.security.runtime_verification import collect_runtime_permission_failure


START_READY_PATH = f"{TRACE_DIRECTORY}/start.ready"
_INIT_EXE = "/usr/local/bin/codeguard-init"


def release_start_gate(container) -> None:
    """Create the empty marker inside this container's private evidence volume."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo(Path(START_READY_PATH).name)
        member.size = 0
        member.uid = 0
        member.gid = 0
        member.mode = 0o444
        archive.addfile(member)
    try:
        created = container.put_archive(TRACE_DIRECTORY, buffer.getvalue())
    except docker.errors.DockerException as exc:
        raise ContainerExecutionError("사용자 코드 시작 파일을 생성하지 못했습니다.") from exc
    if created is not True:
        raise ContainerExecutionError("사용자 코드 시작 파일을 생성하지 못했습니다.")


def _cgroup_path(proc_root: Path, tid: int) -> str:
    lines = (proc_root / str(tid) / "cgroup").read_text(encoding="utf-8").splitlines()
    return next(line.split(":", 2)[2] for line in lines if line.startswith("0::"))


def _is_codeguard_init(proc_root: Path, tid: int) -> bool:
    proc_dir = proc_root / str(tid)
    try:
        return os.readlink(proc_dir / "exe") == _INIT_EXE
    except PermissionError:
        # The unprivileged Runner cannot always inspect a different UID's exe.
        # The start gate guarantees that only the trusted init can run here.
        argv0 = (proc_dir / "cmdline").read_bytes().split(b"\0", 1)[0]
        return argv0 == os.fsencode(_INIT_EXE)


def find_codeguard_init_tid(tracer_tid: int, *, proc_root: Path = Path("/proc")) -> int:
    """Find strace's direct codeguard-init child, never the tracer itself."""
    if tracer_tid <= 0:
        raise TaskTrackingError("Invalid strace TID.")
    try:
        expected_cgroup = _cgroup_path(proc_root, tracer_tid)
        children = (
            proc_root / str(tracer_tid) / "task" / str(tracer_tid) / "children"
        ).read_text(encoding="utf-8").split()
    except (OSError, StopIteration) as exc:
        raise TaskTrackingError("Cannot inspect strace children.") from exc

    for value in children:
        try:
            child_tid = int(value)
            if child_tid <= 0 or _cgroup_path(proc_root, child_tid) != expected_cgroup:
                continue
            if _is_codeguard_init(proc_root, child_tid):
                return child_tid
        except (OSError, StopIteration, ValueError):
            continue
    raise TaskTrackingError("Waiting codeguard-init child was not found.")


def wait_for_security_evidence(container, *, timeout_seconds: float = 5.0) -> None:
    """Wait for codeguard-init's trusted PASS/FAIL evidence before release."""
    deadline = time.monotonic() + timeout_seconds
    last_error = None
    while True:
        try:
            failed = collect_runtime_permission_failure(container)
        except SecurityVerificationError as exc:
            last_error = exc
        else:
            if failed:
                raise SecurityVerificationError(
                    "Execution Container 권한 제한 적용을 검증하지 못했습니다."
                )
            return
        if time.monotonic() >= deadline:
            raise ContainerExecutionError("권한 검증 증거를 확인하지 못했습니다.") from last_error
        time.sleep(0.01)
