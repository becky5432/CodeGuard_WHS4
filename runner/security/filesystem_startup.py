"""Upload policy and validate protected native startup evidence, not logs."""

import io
import math
import re
import tarfile
import time
from dataclasses import dataclass

import docker

from runner.exceptions import ContainerExecutionError
from runner.policies.filesystem import FilesystemPolicy, serialize_filesystem_policy


POLICY_PATH = "/run/codeguard-trace/filesystem.policy"
STATUS_PATH = "/run/codeguard-trace/filesystem.status"
STATUS_LIMIT_BYTES = 16384
STATUS_LINE_LIMIT_BYTES = 4096
STATUS_ARCHIVE_LIMIT_BYTES = STATUS_LIMIT_BYTES + 64 * 1024
MIN_LANDLOCK_ABI = 7
_POLICY_ID = re.compile(r"[0-9a-f]{64}")
_RECORD = re.compile(
    r"CGFS_STATUS 1 policy=([0-9a-f]{64}) state=(PREPARED|APPLIED|FAILED) "
    r"abi=(0|[1-9][0-9]*)(?: step=([A-Za-z0-9_-]{1,31}))?(?: errno=(0|[1-9][0-9]*))?"
)


class FilesystemStartupError(ContainerExecutionError):
    """Internal fail-closed startup error; inherits the existing public code."""


class _Incomplete(FilesystemStartupError):
    """Only a missing or still-being-written evidence file is retryable."""


def _failure(actual: str) -> FilesystemStartupError:
    return FilesystemStartupError("Filesystem policy startup verification failed: " + actual,
                                  details={"check": "filesystem_startup", "actual": actual})


def _validate_id(policy_id: str) -> None:
    if not isinstance(policy_id, str) or not _POLICY_ID.fullmatch(policy_id):
        raise _failure("invalid expected policy ID")


@dataclass(frozen=True)
class FilesystemStartupStatus:
    policy_id: str
    abi: int
    prepared: bool
    applied: bool


def build_policy_archive(policy: FilesystemPolicy) -> bytes:
    """One exact root-owned 0400 policy member for the private trace volume."""
    payload = serialize_filesystem_policy(policy)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("filesystem.policy")
        info.size, info.uid, info.gid, info.mode = len(payload), 0, 0, 0o400
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def _number(value: str) -> int:
    if len(value) > 10 or int(value) > 2147483647:
        raise _failure("out-of-range status integer")
    return int(value)


def _valid_partial(tail: str, policy_id: str, next_state: str, abi: int | None) -> bool:
    """Only a prefix of the expected record may be treated as a write in flight."""
    for state in (next_state, "FAILED"):
        prefix = f"CGFS_STATUS 1 policy={policy_id} state={state} abi="
        if prefix.startswith(tail):
            return True
        if not tail.startswith(prefix):
            continue
        rest = tail[len(prefix):]
        if state != "FAILED" and abi is not None:
            if str(abi).startswith(rest):
                return True
        elif re.fullmatch(r"(?:0|[1-9][0-9]{0,9})", rest):
            _number(rest)
            return True
    return False


def parse_filesystem_status(data: bytes, policy_id: str) -> FilesystemStartupStatus:
    """Strict PREPARED/APPLIED sequence; incomplete is retryable only at prepare.

    FAILED is always fatal, including failures before ABI discovery (abi=0).
    The final gate additionally requires both complete records, in order.
    """
    _validate_id(policy_id)
    if not isinstance(data, bytes) or len(data) > STATUS_LIMIT_BYTES:
        raise _failure("oversized or invalid status content")
    segments = data.split(b"\n")
    if any(len(line) + (1 if index < len(segments) - 1 else 0) > STATUS_LINE_LIMIT_BYTES
           for index, line in enumerate(segments)):
        raise _failure("status line exceeds limit")
    try:
        text = data.decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise _failure("status is not ASCII") from exc
    lines = text.split("\n")
    tail = lines.pop()
    prepared = applied = False
    abi = None
    for line in lines:
        record = _RECORD.fullmatch(line)
        if record is None or record[1] != policy_id:
            raise _failure("malformed status record or mismatched policy")
        state, observed_abi = record[2], _number(record[3])
        if record[5] is not None:
            _number(record[5])
        if state == "FAILED":
            raise _failure("FAILED" + (f" at {record[4]}" if record[4] else ""))
        if record[4] is not None or record[5] is not None:
            raise _failure("unexpected fields on successful status record")
        if observed_abi < MIN_LANDLOCK_ABI:
            raise _failure("unsupported Landlock ABI")
        if state == "PREPARED":
            if prepared or applied:
                raise _failure("duplicate or out-of-order PREPARED")
            prepared, abi = True, observed_abi
        elif not prepared or applied or observed_abi != abi:
            raise _failure("duplicate, out-of-order or conflicting APPLIED")
        else:
            applied = True
    if tail:
        partial = _RECORD.fullmatch(tail)
        if partial and partial[1] == policy_id and partial[2] == "FAILED":
            raise _failure("FAILED in incomplete record")
        if applied or not _valid_partial(tail, policy_id, "APPLIED" if prepared else "PREPARED", abi):
            raise _failure("malformed incomplete status record")
        raise _Incomplete("Filesystem startup evidence is incomplete")
    if not prepared:
        raise _Incomplete("Filesystem startup evidence is not yet prepared")
    return FilesystemStartupStatus(policy_id, abi, prepared, applied)


def _collect_status(container) -> bytes:
    try:
        stream, _ = container.get_archive(STATUS_PATH)
    except docker.errors.NotFound as exc:
        raise _Incomplete("Filesystem startup evidence is missing") from exc
    except (docker.errors.DockerException, OSError) as exc:
        raise _failure("status archive is unreadable") from exc
    archive = bytearray()
    try:
        for chunk in stream:
            if not isinstance(chunk, bytes) or len(archive) + len(chunk) > STATUS_ARCHIVE_LIMIT_BYTES:
                raise _failure("oversized or invalid status archive")
            archive.extend(chunk)
    except FilesystemStartupError:
        raise
    except (docker.errors.DockerException, OSError, ValueError, TypeError) as exc:
        raise _failure("status archive stream failed") from exc
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except (docker.errors.DockerException, OSError, ValueError) as exc:
                raise _failure("status archive stream close failed") from exc
    try:
        # Docker's get_archive returns an uncompressed TAR. Do not decompress
        # attacker-controlled evidence or admit compression expansion bombs.
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            members = tar.getmembers()
            if len(members) != 1:
                raise _failure("status archive must contain exactly one file")
            member = members[0]
            if (member.name != "filesystem.status" or member.type not in {tarfile.REGTYPE, tarfile.AREGTYPE}
                    or member.sparse is not None
                    or any(key.startswith("GNU.sparse.") for key in member.pax_headers)):
                raise _failure("status archive has a nonexact or nonregular file")
            if member.uid != 0 or member.gid != 0 or member.mode != 0o600:
                raise _failure("status file is not root-owned mode 0600")
            if not 0 <= member.size <= STATUS_LIMIT_BYTES:
                raise _failure("oversized status file")
            end = member.offset_data + ((member.size + 511) // 512) * 512
            if (len(archive) < end + 1024 or len(archive) % 512
                    or any(archive[member.offset_data + member.size:])):
                raise _failure("status archive has missing end markers or hidden trailing data")
            file = tar.extractfile(member)
            if file is None:
                raise _failure("status file is unreadable")
            with file:
                data = file.read(STATUS_LIMIT_BYTES + 1)
            if len(data) != member.size:
                raise _failure("truncated status file")
            return data
    except FilesystemStartupError:
        raise
    except (tarfile.TarError, OSError, ValueError) as exc:
        raise _failure("invalid status archive") from exc


def wait_for_filesystem_prepared(container, policy_id: str,
                                 timeout_seconds: float = 5) -> FilesystemStartupStatus:
    """Retry only missing/valid incomplete evidence; malformed/FAILED is fatal."""
    _validate_id(policy_id)
    if (type(timeout_seconds) not in {int, float} or not math.isfinite(timeout_seconds)
            or timeout_seconds < 0):
        raise _failure("invalid prepare timeout")
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            return parse_filesystem_status(_collect_status(container), policy_id)
        except _Incomplete as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise _failure("prepare timeout: missing or incomplete evidence") from exc
            time.sleep(min(0.01, remaining))


def verify_filesystem_applied(container, policy_id: str) -> FilesystemStartupStatus:
    """Final verification never retries and requires exactly PREPARED/APPLIED."""
    _validate_id(policy_id)
    status = parse_filesystem_status(_collect_status(container), policy_id)
    if not status.prepared or not status.applied:
        raise _failure("final status is missing APPLIED")
    return status
