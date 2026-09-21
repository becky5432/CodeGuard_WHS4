"""Verify trusted runtime permission evidence before releasing user code."""

import io
import logging
import re
import tarfile
import time
from dataclasses import dataclass

import docker

from runner.exceptions import SecurityVerificationError
from runner.security import SECURITY_GID, SECURITY_UID


logger = logging.getLogger("runner")

SECURITY_EVIDENCE_PATH = "/run/codeguard-trace/security.status"
SECURITY_EVIDENCE_LIMIT_BYTES = 4096
SECURITY_EVIDENCE_TIMEOUT_SECONDS = 1.0
_ZERO_CAPABILITY = "0000000000000000"
_EXPECTED_FIELDS = frozenset(
    {
        "status", "ruid", "euid", "suid", "fsuid",
        "rgid", "egid", "sgid", "fsgid",
        "supplementary_group_count", "supplementary_groups_allowed",
        "cap_inh", "cap_prm", "cap_eff", "cap_bnd", "cap_amb",
        "no_new_privileges", "no_new_privileges_prctl",
        "setuid_root_denied", "setgid_root_denied",
    }
)


@dataclass(frozen=True)
class RuntimeSecurityEvidence:
    status: str
    ruid: int
    euid: int
    suid: int
    fsuid: int
    rgid: int
    egid: int
    sgid: int
    fsgid: int
    supplementary_group_count: int
    supplementary_groups_allowed: int
    cap_inh: str
    cap_prm: str
    cap_eff: str
    cap_bnd: str
    cap_amb: str
    no_new_privileges: int
    no_new_privileges_prctl: int
    setuid_root_denied: int
    setgid_root_denied: int


class _EvidenceNotReady(Exception):
    pass


def _failure(check: str, actual: object) -> SecurityVerificationError:
    logger.error(
        "event=runtime_security_verification_failed check=%s actual=%s",
        check,
        actual,
    )
    return SecurityVerificationError(
        "Execution Container 권한 제한 적용을 검증하지 못했습니다.",
        details={"check": check},
    )


def _read_evidence_archive(container) -> bytes:
    try:
        stream, _ = container.get_archive(SECURITY_EVIDENCE_PATH)
    except docker.errors.NotFound as exc:
        raise _EvidenceNotReady from exc
    except docker.errors.DockerException as exc:
        raise _failure("evidence", "unreadable") from exc

    archive = bytearray()
    try:
        for chunk in stream:
            archive.extend(chunk)
            if len(archive) > SECURITY_EVIDENCE_LIMIT_BYTES + 64 * 1024:
                raise _failure("evidence", "oversized archive")
    finally:
        close = getattr(stream, "close", None)
        if callable(close):
            close()
    return bytes(archive)


def _extract_evidence(archive: bytes) -> str:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
            member = next(
                (item for item in tar.getmembers() if item.name == "security.status"),
                None,
            )
            if member is None or not member.isfile() or member.size <= 0:
                raise _EvidenceNotReady
            if member.size >= SECURITY_EVIDENCE_LIMIT_BYTES:
                raise _failure("evidence", "oversized")
            if (
                member.uid != 0 or member.gid != 0
                or member.mode & 0o7777 != 0o600
            ):
                raise _failure("evidence_permissions", "not root-owned mode 0600")
            file = tar.extractfile(member)
            if file is None:
                raise _failure("evidence", "unreadable")
            return file.read(SECURITY_EVIDENCE_LIMIT_BYTES).decode(
                "ascii", errors="strict"
            )
    except SecurityVerificationError:
        raise
    except (tarfile.TarError, UnicodeDecodeError, OSError) as exc:
        raise _failure("evidence", "invalid archive") from exc


def _parse_evidence(value: str) -> RuntimeSecurityEvidence:
    lines = value.splitlines()
    if len(lines) != 1 or not lines[0]:
        raise _failure("evidence_format", "expected one line")
    fields: dict[str, str] = {}
    for item in lines[0].split():
        if item.count("=") != 1:
            raise _failure("evidence_format", item)
        key, field_value = item.split("=", 1)
        if key in fields:
            raise _failure("evidence_format", f"duplicate {key}")
        fields[key] = field_value
    if fields.keys() != _EXPECTED_FIELDS:
        raise _failure("evidence_fields", sorted(fields))
    for name in ("cap_inh", "cap_prm", "cap_eff", "cap_bnd", "cap_amb"):
        if re.fullmatch(r"[0-9a-fA-F]{16}", fields[name]) is None:
            raise _failure(name, fields[name])
    try:
        return RuntimeSecurityEvidence(
            status=fields["status"], ruid=int(fields["ruid"]),
            euid=int(fields["euid"]), suid=int(fields["suid"]),
            fsuid=int(fields["fsuid"]),
            rgid=int(fields["rgid"]), egid=int(fields["egid"]),
            sgid=int(fields["sgid"]), fsgid=int(fields["fsgid"]),
            supplementary_group_count=int(
                fields["supplementary_group_count"]
            ),
            supplementary_groups_allowed=int(
                fields["supplementary_groups_allowed"]
            ),
            cap_inh=fields["cap_inh"].lower(),
            cap_prm=fields["cap_prm"].lower(),
            cap_eff=fields["cap_eff"].lower(),
            cap_bnd=fields["cap_bnd"].lower(),
            cap_amb=fields["cap_amb"].lower(),
            no_new_privileges=int(fields["no_new_privileges"]),
            no_new_privileges_prctl=int(fields["no_new_privileges_prctl"]),
            setuid_root_denied=int(fields["setuid_root_denied"]),
            setgid_root_denied=int(fields["setgid_root_denied"]),
        )
    except ValueError as exc:
        raise _failure("evidence_format", "non-integer field") from exc


def verify_runtime_permission_restrictions(container) -> None:
    """Fail closed unless the pre-exec codeguard process proves restrictions."""
    deadline = time.monotonic() + SECURITY_EVIDENCE_TIMEOUT_SECONDS
    while True:
        try:
            value = _extract_evidence(_read_evidence_archive(container))
            if not value.endswith("\n"):
                raise _EvidenceNotReady
            evidence = _parse_evidence(value)
            break
        except _EvidenceNotReady as exc:
            if time.monotonic() >= deadline:
                raise _failure("evidence", "missing or incomplete") from exc
            time.sleep(0.01)
    checks = {
        "status": evidence.status == "verified",
        "uid": all(value == SECURITY_UID for value in (
            evidence.ruid, evidence.euid, evidence.suid, evidence.fsuid,
        )),
        "gid": all(value == SECURITY_GID for value in (
            evidence.rgid, evidence.egid, evidence.sgid, evidence.fsgid,
        )),
        "supplementary_groups": (
            evidence.supplementary_group_count in (0, 1)
            and evidence.supplementary_groups_allowed == 1
        ),
        "cap_inh": evidence.cap_inh == _ZERO_CAPABILITY,
        "cap_prm": evidence.cap_prm == _ZERO_CAPABILITY,
        "cap_eff": evidence.cap_eff == _ZERO_CAPABILITY,
        "cap_amb": evidence.cap_amb == _ZERO_CAPABILITY,
        "no_new_privileges": evidence.no_new_privileges == 1,
        "no_new_privileges_prctl": evidence.no_new_privileges_prctl == 1,
        "setuid_root_denied": evidence.setuid_root_denied == 1,
        "setgid_root_denied": evidence.setgid_root_denied == 1,
    }
    failed = [name for name, verified in checks.items() if not verified]
    if failed:
        raise _failure("runtime", failed)

    logger.info(
        "event=runtime_permission_restriction_verified "
        "ruid=%s euid=%s suid=%s fsuid=%s "
        "rgid=%s egid=%s sgid=%s fsgid=%s "
        "supplementary_group_count=%s supplementary_groups_allowed=%s "
        "cap_inh=%s cap_prm=%s "
        "cap_eff=%s cap_bnd=%s cap_amb=%s no_new_privileges=%s "
        "no_new_privileges_prctl=%s "
        "setuid_root_denied=%s setgid_root_denied=%s",
        evidence.ruid, evidence.euid, evidence.suid, evidence.fsuid,
        evidence.rgid, evidence.egid, evidence.sgid, evidence.fsgid,
        evidence.supplementary_group_count,
        evidence.supplementary_groups_allowed,
        evidence.cap_inh, evidence.cap_prm, evidence.cap_eff,
        evidence.cap_bnd, evidence.cap_amb, evidence.no_new_privileges,
        evidence.no_new_privileges_prctl,
        evidence.setuid_root_denied, evidence.setgid_root_denied,
    )
