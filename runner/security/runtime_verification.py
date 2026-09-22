"""Collect the trusted post-exit permission failure status."""

import io
import tarfile

import docker

from runner.exceptions import SecurityVerificationError


SECURITY_STATUS_PATH = "/run/codeguard-trace/security.status"
SECURITY_STATUS_LIMIT_BYTES = 64
SECURITY_SUCCESS_TOKEN = b"SECURITY_VERIFICATION_PASSED\n"
SECURITY_FAILURE_TOKEN = b"SECURITY_VERIFICATION_FAILED\n"


def _failure(actual: str) -> SecurityVerificationError:
    return SecurityVerificationError(
        "Execution Container 권한 제한 적용을 검증하지 못했습니다.",
        details={"check": "security_failure_status", "actual": actual},
    )


def collect_runtime_permission_failure(container) -> bool:
    """Return true only for a trusted codeguard-init failure token."""
    try:
        stream, _ = container.get_archive(SECURITY_STATUS_PATH)
    except (docker.errors.NotFound, docker.errors.DockerException) as exc:
        raise _failure("missing or unreadable") from exc

    archive = bytearray()
    try:
        for chunk in stream:
            archive.extend(chunk)
            if len(archive) > SECURITY_STATUS_LIMIT_BYTES + 64 * 1024:
                raise _failure("oversized archive")
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()

    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
            member = next(
                (item for item in tar.getmembers() if item.name == "security.status"),
                None,
            )
            if member is None or not member.isfile():
                raise _failure("missing or non-regular")
            if member.uid != 0 or member.gid != 0 or member.mode & 0o7777 != 0o600:
                raise _failure("not root-owned mode 0600")
            if member.size > SECURITY_STATUS_LIMIT_BYTES:
                raise _failure("oversized")
            file = tar.extractfile(member)
            if file is None:
                raise _failure("unreadable")
            value = file.read(SECURITY_STATUS_LIMIT_BYTES + 1)
    except SecurityVerificationError:
        raise
    except (tarfile.TarError, OSError) as exc:
        raise _failure("invalid archive") from exc

    if value == SECURITY_SUCCESS_TOKEN:
        return False
    if value == SECURITY_FAILURE_TOKEN:
        return True
    raise _failure("invalid token")
