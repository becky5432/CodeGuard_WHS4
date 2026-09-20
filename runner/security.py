"""Container security policy verification helpers used only inside Runner."""

import logging
from dataclasses import dataclass

import docker

from runner.exceptions import ContainerExecutionError, SecurityVerificationError


logger = logging.getLogger("runner")

SECURITY_UID = 10001
SECURITY_GID = 10001
SECURITY_CAP_DROP = ("ALL",)
SECURITY_NO_NEW_PRIVILEGES = True
SECURITY_OPT = "no-new-privileges=true"
SECURITY_PREFLIGHT_PATH = "/usr/local/bin/codeguard-security-preflight"
SECURITY_MARKER = "CODEGUARD_SECURITY_CHECK"


@dataclass(frozen=True)
class SecurityCheckResult:
    uid: int
    gid: int
    cap_inh: str
    cap_prm: str
    cap_eff: str
    cap_bnd: str
    cap_amb: str
    no_new_privileges: int


def security_environment() -> dict[str, str]:
    return {
        "CODEGUARD_EXPECTED_UID": str(SECURITY_UID),
        "CODEGUARD_EXPECTED_GID": str(SECURITY_GID),
    }


def _security_error(stage: str, check: str, actual: object) -> None:
    logger.error(
        "event=container_security_verification_failed "
        "stage=%s check=%s actual=%s",
        stage,
        check,
        actual,
    )
    raise SecurityVerificationError(
        f"{stage.capitalize()} Container 보안 설정 검증에 실패했습니다."
    )


def verify_container_security_config(
    container,
    *,
    stage: str,
    workspace_mode: str,
) -> None:
    """Validate Docker inspect configuration before container startup."""

    try:
        container.reload()
    except docker.errors.DockerException as exc:
        raise ContainerExecutionError(
            f"{stage.capitalize()} Container 설정 확인에 실패했습니다.",
            details={"reason": str(exc)},
        ) from exc

    attrs = container.attrs
    config = attrs.get("Config", {})
    host_config = attrs.get("HostConfig", {})
    expected_user = f"{SECURITY_UID}:{SECURITY_GID}"

    actual_user = config.get("User")
    if actual_user != expected_user:
        _security_error(stage, "user", actual_user)

    actual_cap_drop = host_config.get("CapDrop") or []
    if "ALL" not in {str(value).upper() for value in actual_cap_drop}:
        _security_error(stage, "cap_drop", actual_cap_drop)

    actual_security_opt = host_config.get("SecurityOpt") or []
    if SECURITY_OPT not in actual_security_opt:
        _security_error(stage, "no_new_privileges", actual_security_opt)

    workspace_mount = next(
        (
            mount
            for mount in attrs.get("Mounts", [])
            if mount.get("Destination") == "/workspace"
        ),
        None,
    )
    expected_rw = workspace_mode == "rw"
    if workspace_mount is None or workspace_mount.get("RW") is not expected_rw:
        _security_error(stage, "workspace_mode", workspace_mount)

    logger.info(
        "event=container_security_config_verified "
        "stage=%s user=%s cap_drop=%s security_opt=%s workspace_mode=%s",
        stage,
        actual_user,
        actual_cap_drop,
        actual_security_opt,
        workspace_mode,
    )


def parse_security_preflight(stderr: str, *, stage: str) -> str:
    """Remove the internal marker, validate it, and log measured values."""

    marker_values = None
    user_lines: list[str] = []
    for line in stderr.splitlines(keepends=True):
        stripped = line.rstrip("\r\n")
        if stripped.startswith(f"{SECURITY_MARKER} "):
            fields = stripped.split()[1:]
            try:
                marker_values = dict(field.split("=", 1) for field in fields)
            except ValueError:
                _security_error(stage, "runtime_marker", stripped)
        else:
            user_lines.append(line)

    if marker_values is None:
        _security_error(stage, "runtime_marker", "missing")

    try:
        result = SecurityCheckResult(
            uid=int(marker_values["uid"]),
            gid=int(marker_values["gid"]),
            cap_inh=marker_values["cap_inh"],
            cap_prm=marker_values["cap_prm"],
            cap_eff=marker_values["cap_eff"],
            cap_bnd=marker_values["cap_bnd"],
            cap_amb=marker_values["cap_amb"],
            no_new_privileges=int(marker_values["no_new_privileges"]),
        )
    except (KeyError, TypeError, ValueError):
        _security_error(stage, "runtime_marker", marker_values)

    zero_capability = "0000000000000000"
    verified = (
        marker_values.get("status") == "verified"
        and result.uid == SECURITY_UID
        and result.gid == SECURITY_GID
        and result.cap_inh == zero_capability
        and result.cap_prm == zero_capability
        and result.cap_eff == zero_capability
        and result.cap_bnd == zero_capability
        and result.cap_amb == zero_capability
        and result.no_new_privileges == 1
    )
    if not verified:
        _security_error(stage, "runtime", result)

    logger.info(
        "event=container_security_verified "
        "stage=%s uid=%s gid=%s cap_inh=%s cap_prm=%s cap_eff=%s "
        "cap_bnd=%s cap_amb=%s no_new_privileges=%s",
        stage,
        result.uid,
        result.gid,
        result.cap_inh,
        result.cap_prm,
        result.cap_eff,
        result.cap_bnd,
        result.cap_amb,
        result.no_new_privileges,
    )
    return "".join(user_lines)
