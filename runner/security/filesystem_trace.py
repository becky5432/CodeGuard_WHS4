"""Analyze trusted strace evidence, never user stdout or stderr."""

import ast
import io
import re
import tarfile
from dataclasses import dataclass

TRACE_DIRECTORY = "/run/codeguard-trace"
TRACE_PATH = f"{TRACE_DIRECTORY}/trace.log"
TRACE_LIMIT_BYTES = 1024 * 1024
WRITE_FLAGS = frozenset({"O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_TMPFILE"})
OPEN_FLAG_ARGUMENT = {"open": 1, "openat": 2, "openat2": 2}
RAW_WRITE_SYSCALLS = "write,writev,pwrite64,pwritev,pwritev2"
MUTATION_IOCTLS = frozenset({
    "FICLONE", "FICLONERANGE", "FIDEDUPERANGE", "FS_IOC_SETFLAGS",
    "FS_IOC_SETVERSION", "FS_IOC_FSSETXATTR", "FS_IOC_SET_ENCRYPTION_POLICY",
    "FS_IOC_ENABLE_VERITY",
})
MUTATION_SYSCALLS = frozenset({
    "creat", "unlink", "unlinkat", "rename", "renameat", "renameat2",
    "mkdir", "mkdirat", "rmdir", "link", "linkat", "symlink", "symlinkat",
    "truncate", "ftruncate", "mknod", "mknodat",
    *RAW_WRITE_SYSCALLS.split(","), "fallocate",
    "chmod", "fchmod", "fchmodat", "fchmodat2",
    "chown", "fchown", "lchown", "fchownat",
    "utime", "utimes", "futimesat", "utimensat",
    "setxattr", "lsetxattr", "fsetxattr",
    "removexattr", "lremovexattr", "fremovexattr",
})
# User-installed seccomp can fabricate errno; io_uring submits filesystem work
# without ordinary write syscall stops. Successful use invalidates this detector.
TRACE_SYSCALLS = ",".join([
    "execve", "seccomp", "prctl", "io_uring_setup", "clone", "clone3", "ioctl",
    *OPEN_FLAG_ARGUMENT, *sorted(MUTATION_SYSCALLS),
])
_PREFIX = re.compile(r"^(?:(\d+)\s+|\[pid\s+(\d+)\]\s+)?(.*)$")
_CALL = re.compile(r"^(\w+)\((.*)\)\s+=\s+(-?\d+)(?:\s+(\w+)\b.*)?$")


@dataclass(frozen=True)
class FilesystemViolation:
    detected: bool = False
    syscall: str | None = None
    path: str | None = None
    errno: str | None = None


def _arguments(value: str) -> list[str]:
    result = []
    start = depth = 0
    quoted = escaped = False
    for index, char in enumerate(value):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in "({[":
            depth += 1
        elif char in ")}]":
            depth -= 1
        elif char == "," and depth == 0:
            result.append(value[start:index].strip())
            start = index + 1
    result.append(value[start:].strip())
    return result


def _calls(trace: str):
    pending = {}
    for line in trace.splitlines():
        prefix = _PREFIX.fullmatch(line.strip())
        if not prefix:
            continue
        pid = prefix[1] or prefix[2] or "main"
        body = prefix[3]
        if body.endswith("<unfinished ...>"):
            pending[pid] = body.removesuffix("<unfinished ...>")
            continue
        resumed = re.match(r"^<\.\.\. (\w+) resumed>(.*)$", body)
        if resumed:
            first = pending.pop(pid, None)
            if not first or not first.startswith(resumed[1] + "("):
                continue
            body = first + resumed[2]
        match = _CALL.fullmatch(body)
        if match:
            yield pid, match[1], _arguments(match[2]), int(match[3]), match[4]
        else:
            incomplete = re.fullmatch(r"(seccomp|prctl|io_uring_setup|clone|clone3)\((.*)", body)
            if incomplete:
                yield pid, incomplete[1], _arguments(incomplete[2]), None, None
    # A filter/setup may take effect before its thread dies or the exit stop is
    # collected. An unfinished bypass call cannot establish trustworthy errno.
    for pid, body in pending.items():
        match = re.fullmatch(r"(seccomp|prctl|io_uring_setup|clone|clone3)\((.*)", body)
        if match:
            yield pid, match[1], _arguments(match[2]), None, None


def _path(syscall: str, args: list[str]) -> str | None:
    # For *at calls the first argument is a directory fd. ftruncate has only an fd.
    index = 1 if syscall in {
        "openat", "openat2", "unlinkat", "renameat", "renameat2", "mkdirat",
        "linkat", "mknodat", "fchmodat", "fchmodat2", "fchownat",
        "futimesat", "utimensat",
    } else 0
    if syscall == "symlinkat":
        index = 2
    if syscall == "symlink":
        index = 1
    if index >= len(args):
        return None
    value = args[index]
    if value.startswith('"') and value.endswith('"'):
        try:
            return ast.literal_eval(value)
        except (ValueError, SyntaxError):
            return None
    fd_path = re.fullmatch(r"\d+<(.+)>", value)
    return fd_path[1] if fd_path else None


def analyze_filesystem_trace(trace: str) -> FilesystemViolation:
    calls = list(_calls(trace))
    for _, syscall, args, result, _ in calls:
        seccomp_install = (
            syscall == "seccomp" and args
            and args[0] in {"SECCOMP_SET_MODE_STRICT", "SECCOMP_SET_MODE_FILTER", "0", "1", "0x0", "0x1"}
        )
        prctl_install = (
            syscall == "prctl" and args
            and args[0] in {"PR_SET_SECCOMP", "22", "0x16"}
        )
        untraced_clone = False
        if syscall in {"clone", "clone3"}:
            flags = re.search(r"\bflags=([^,}]+)", ",".join(args))
            if flags:
                parts = [part.strip() for part in flags[1].split("|")]
                untraced_clone = "CLONE_UNTRACED" in parts
                for part in parts:
                    try:
                        untraced_clone |= bool(int(part, 0) & 0x00800000)  # CLONE_UNTRACED
                    except ValueError:
                        pass
            else:
                # Unknown clone argument decoding cannot prove child tracing.
                untraced_clone = True
        if (
            (seccomp_install or prctl_install or syscall == "io_uring_setup" or untraced_clone)
            and (result is None or result >= 0)
        ):
            raise ValueError("user syscall filtering, asynchronous IO or untraced cloning invalidates filesystem evidence")
    for _, syscall, args, result, errno in calls:
        if result != -1 or errno != "EROFS":
            continue
        if syscall in OPEN_FLAG_ARGUMENT:
            index = OPEN_FLAG_ARGUMENT[syscall]
            if index >= len(args):
                continue
            flags = args[index]
            if syscall == "openat2":
                field = re.search(r"(?:^|[{,]\s*)flags=([^,}]+)", flags)
                flags = field[1] if field else ""
            if not WRITE_FLAGS.intersection(re.findall(r"\bO_\w+\b", flags)):
                continue
        elif syscall == "ioctl":
            if len(args) < 2 or args[1] not in MUTATION_IOCTLS:
                continue
        elif syscall not in MUTATION_SYSCALLS:
            continue
        return FilesystemViolation(True, syscall, _path(syscall, args), errno)
    return FilesystemViolation()


def collect_filesystem_trace(container, *, interrupted: bool = False) -> FilesystemViolation:
    """Read a stopped container's dedicated volume with a bounded archive size.

    Missing/truncated evidence or a tracer startup failure raises an internal error.
    A policy kill can legitimately leave a partial trace without an exit marker.
    """
    stream, _ = container.get_archive(TRACE_PATH)
    archive = bytearray()
    try:
        for chunk in stream:
            archive.extend(chunk)
            if len(archive) > TRACE_LIMIT_BYTES + 64 * 1024:
                raise ValueError("filesystem trace archive exceeds limit")
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        member = next((m for m in tar.getmembers() if m.name == "trace.log"), None)
        if member is None or not member.isfile() or member.size >= TRACE_LIMIT_BYTES:
            raise ValueError("missing or oversized filesystem trace")
        if member.uid != 0 or member.gid != 0 or member.mode & 0o7777 != 0o600:
            raise ValueError("filesystem trace must be root-owned and private")
        file = tar.extractfile(member)
        if file is None:
            raise ValueError("filesystem trace is not readable")
        trace = file.read(TRACE_LIMIT_BYTES).decode("utf-8", errors="strict")
    calls = list(_calls(trace))
    executed = any(
        syscall == "execve" and args and args[0] == '"/workspace/main"' and result == 0
        for _, syscall, args, result, _ in calls
    )
    if not executed:
        raise ValueError("strace did not record a successful program exec")
    if not interrupted:
        terminated = set()
        for line in trace.splitlines():
            prefix = _PREFIX.fullmatch(line.strip())
            if prefix and re.fullmatch(r"\+\+\+ (?:exited with \d+|killed by SIG\w+.*?) \+\+\+", prefix[3]):
                terminated.add(prefix[1] or prefix[2] or "main")
        if not {pid for pid, *_ in calls}.issubset(terminated):
            raise ValueError("filesystem trace is incomplete")
    return analyze_filesystem_trace(trace)
