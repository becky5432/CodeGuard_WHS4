import ctypes
import os
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID

from runner.config import Settings
from runner.exceptions import TaskTrackingError


CG_TRACKER_MAGIC = 0x43475452
CG_TRACKER_VERSION = 1

CG_OP_HEALTH = 1
CG_OP_REGISTER_CGROUP = 2
CG_OP_REGISTER_ROOT = 3
CG_OP_SNAPSHOT = 4
CG_OP_REMOVE = 5

CG_ERR_NONE = 0
CG_ERR_TASK_MAP = 1 << 0
CG_ERR_PROCESS_MAP = 1 << 1
CG_ERR_COUNTER = 1 << 2
CG_ERR_CAPACITY = 1 << 3

REQUEST_STRUCT = struct.Struct("<IHH16sQII")
RESPONSE_STRUCT = struct.Struct("<IHHiIIII")


class TaskTrackerTransport(Protocol):
    def request(self, payload: bytes, response_size: int) -> bytes: ...


class UnixSeqpacketTransport:
    def __init__(self, socket_path: Path, timeout_seconds: float) -> None:
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def request(self, payload: bytes, response_size: int) -> bytes:
        try:
            with socket.socket(
                socket.AF_UNIX,
                socket.SOCK_SEQPACKET,
            ) as connection:
                connection.settimeout(self.timeout_seconds)
                connection.connect(str(self.socket_path))
                connection.sendall(payload)
                response = connection.recv(response_size + 1)
        except (OSError, TimeoutError) as exc:
            raise TaskTrackingError(
                f"Task tracker communication failed: {exc}"
            ) from exc

        if len(response) != response_size:
            raise TaskTrackingError("Invalid task tracker response length.")
        return response


@dataclass(frozen=True)
class PidsPeakSnapshot:
    container_task_peak: int
    process_at_pids_peak: int
    thread_at_pids_peak: int


@dataclass(frozen=True)
class ExecutionCgroupIdentity:
    cgroup_id: int
    pids_current: int


class _FileHandle(ctypes.Structure):
    _fields_ = [
        ("handle_bytes", ctypes.c_uint),
        ("handle_type", ctypes.c_int),
        ("f_handle", ctypes.c_ubyte * 8),
    ]


def _cgroup_id_from_handle(cgroup_path: Path) -> int:
    if os.name != "posix":
        raise OSError("name_to_handle_at is only available on Linux")

    libc = ctypes.CDLL(None, use_errno=True)
    try:
        name_to_handle_at = libc.name_to_handle_at
    except AttributeError as exc:
        raise OSError("libc does not provide name_to_handle_at") from exc
    name_to_handle_at.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.POINTER(_FileHandle),
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_int,
    ]
    name_to_handle_at.restype = ctypes.c_int

    handle = _FileHandle(handle_bytes=8)
    mount_id = ctypes.c_int()
    result = name_to_handle_at(
        -100,  # AT_FDCWD
        os.fsencode(cgroup_path),
        ctypes.byref(handle),
        ctypes.byref(mount_id),
        0,
    )
    if result != 0:
        error_number = ctypes.get_errno()
        raise OSError(error_number, os.strerror(error_number), cgroup_path)
    if handle.handle_bytes != 8:
        raise OSError("unexpected cgroup file handle size")
    return int.from_bytes(bytes(handle.f_handle), byteorder=sys.byteorder)


def resolve_execution_cgroup(
    root_tid: int,
    *,
    proc_root: Path = Path("/proc"),
    cgroup_mount: Path = Path("/sys/fs/cgroup"),
    cgroup_id_resolver: Callable[[Path], int] = _cgroup_id_from_handle,
) -> ExecutionCgroupIdentity:
    if root_tid <= 0:
        raise TaskTrackingError("Invalid execution container root TID.")

    try:
        lines = (proc_root / str(root_tid) / "cgroup").read_text(
            encoding="utf-8"
        ).splitlines()
        relative_path = next(
            line.split(":", 2)[2]
            for line in lines
            if line.startswith("0::")
        )
        mount = cgroup_mount.resolve()
        cgroup_path = (mount / relative_path.lstrip("/")).resolve()
        cgroup_path.relative_to(mount)
        cgroup_id = cgroup_id_resolver(cgroup_path)
        pids_current = int(
            (cgroup_path / "pids.current")
            .read_text(encoding="utf-8")
            .strip()
        )
    except (FileNotFoundError, OSError, StopIteration, ValueError) as exc:
        raise TaskTrackingError(
            f"Failed to resolve the execution cgroup: {exc}"
        ) from exc

    if cgroup_id <= 0 or pids_current <= 0:
        raise TaskTrackingError("Invalid execution cgroup values.")
    return ExecutionCgroupIdentity(
        cgroup_id=cgroup_id,
        pids_current=pids_current,
    )


@dataclass(frozen=True)
class _TrackerResponse:
    container_task_peak: int
    process_at_pids_peak: int
    thread_at_pids_peak: int
    error_flags: int


class TaskTrackerClient:
    def __init__(self, transport: TaskTrackerTransport) -> None:
        self._transport = transport

    @classmethod
    def from_settings(cls, settings: Settings) -> "TaskTrackerClient":
        return cls(
            transport=UnixSeqpacketTransport(
                socket_path=settings.task_tracker_socket,
                timeout_seconds=settings.task_tracker_timeout_seconds,
            )
        )

    def health(self) -> None:
        self._request(CG_OP_HEALTH, UUID(int=0))

    def register_cgroup(
        self,
        run_id: UUID,
        cgroup_id: int,
        initial_task_count: int,
    ) -> None:
        if cgroup_id <= 0 or initial_task_count <= 0:
            raise TaskTrackingError("Invalid cgroup registration values.")
        self._request(
            CG_OP_REGISTER_CGROUP,
            run_id,
            cgroup_id=cgroup_id,
            initial_task_count=initial_task_count,
        )

    def register_root(self, run_id: UUID, root_tid: int) -> None:
        if root_tid <= 0:
            raise TaskTrackingError("Invalid root TID.")
        self._request(CG_OP_REGISTER_ROOT, run_id, root_tid=root_tid)

    def snapshot(self, run_id: UUID) -> PidsPeakSnapshot:
        response = self._request(CG_OP_SNAPSHOT, run_id)
        return PidsPeakSnapshot(
            container_task_peak=response.container_task_peak,
            process_at_pids_peak=response.process_at_pids_peak,
            thread_at_pids_peak=response.thread_at_pids_peak,
        )

    def remove(self, run_id: UUID) -> None:
        self._request(CG_OP_REMOVE, run_id)

    def _request(
        self,
        operation: int,
        run_id: UUID,
        *,
        cgroup_id: int = 0,
        root_tid: int = 0,
        initial_task_count: int = 0,
    ) -> _TrackerResponse:
        payload = REQUEST_STRUCT.pack(
            CG_TRACKER_MAGIC,
            CG_TRACKER_VERSION,
            operation,
            run_id.bytes,
            cgroup_id,
            root_tid,
            initial_task_count,
        )
        raw_response = self._transport.request(payload, RESPONSE_STRUCT.size)
        (
            magic,
            version,
            _reserved,
            status,
            container_task_peak,
            process_at_pids_peak,
            thread_at_pids_peak,
            error_flags,
        ) = RESPONSE_STRUCT.unpack(raw_response)

        if magic != CG_TRACKER_MAGIC or version != CG_TRACKER_VERSION:
            raise TaskTrackingError("Task tracker protocol version mismatch.")
        if status != 0:
            raise TaskTrackingError(
                f"Task tracker request failed: status={status}"
            )
        if error_flags != CG_ERR_NONE:
            raise TaskTrackingError(
                "Task tracker measurement failed: "
                f"flags=0x{error_flags:x}"
            )

        return _TrackerResponse(
            container_task_peak=container_task_peak,
            process_at_pids_peak=process_at_pids_peak,
            thread_at_pids_peak=thread_at_pids_peak,
            error_flags=error_flags,
        )
