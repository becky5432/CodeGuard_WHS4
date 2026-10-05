"""Versioned, exact-file Landlock policy from a trusted image manifest.

Manifest parsing is a trust boundary: only pass evidence obtained from the
server-selected image's trusted prepare container, never request/user content.
Path validation does not claim that host paths or installed candidates were
verified. The image builder verifies actual ELF files and resolves aliases.
"""

import hashlib
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import PurePosixPath


POLICY_VERSION = 1
MAX_RULES = 128
MAX_POLICY_BYTES = 65536
MAX_PATH_BYTES = 4095
RUNTIME_MANIFEST_PATH = "/usr/local/share/codeguard/filesystem-runtime.json"
RUNTIME_PROFILE_NAME = "cpp-amd64-v1"
RUNTIME_ARCHITECTURE = "amd64"
RUNTIME_LOADER_PATH = "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
LIBRARY_ROOTS = frozenset({"/usr/lib/x86_64-linux-gnu", "/usr/local/lib64"})
LIBRARY_NAMES = frozenset({"libc.so.6", "libstdc++.so.6", "libgcc_s.so.1", "libm.so.6"})
_LIBRARY_PATTERNS = {
    "libc.so.6": re.compile(r"(?:libc\.so\.6|libc-[0-9]+(?:\.[0-9]+)*\.so)"),
    "libstdc++.so.6": re.compile(r"libstdc\+\+\.so\.6(?:\.[0-9]+)*"),
    "libgcc_s.so.1": re.compile(r"libgcc_s\.so\.1(?:\.[0-9]+)*"),
    "libm.so.6": re.compile(r"(?:libm\.so\.6|libm-[0-9]+(?:\.[0-9]+)*\.so)"),
}
_IMAGE_ID = re.compile(r"sha256:[0-9a-f]{64}")
_POLICY_ID = re.compile(r"[0-9a-f]{64}")
_WORKSPACE_FILES = frozenset({
    "/workspace/app/main", "/workspace/app/main.c", "/workspace/app/main.cpp",
    "/workspace/input/stdin",
})


def _validate_image_id(image_id: str) -> None:
    if not isinstance(image_id, str) or not _IMAGE_ID.fullmatch(image_id):
        raise ValueError("runtime image must be an exact verified sha256 image ID")


def _validate_path(path: str) -> None:
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("filesystem path must be absolute")
    if len(path.encode("utf-8")) > MAX_PATH_BYTES:
        raise ValueError("filesystem path exceeds byte limit")
    if any(char in path for char in "\0\t\r\n"):
        raise ValueError("filesystem path contains a forbidden character")
    if any(part in {"", ".", ".."} for part in path[1:].split("/")):
        raise ValueError("filesystem path must be canonical without traversal")


def _library_name(path: str) -> str | None:
    pure = PurePosixPath(path)
    if str(pure.parent) not in LIBRARY_ROOTS:
        return None
    return next((name for name, pattern in _LIBRARY_PATTERNS.items()
                 if pattern.fullmatch(pure.name)), None)


class FsProfile(str, Enum):
    FILE_READ = "FILE_READ"
    FILE_EXEC = "FILE_EXEC"
    DIR_LIST = "DIR_LIST"
    WORK = "WORK"


@dataclass(frozen=True)
class FsRule:
    profile: FsProfile
    path: str

    def __post_init__(self) -> None:
        try:
            profile = FsProfile(self.profile)
        except (ValueError, TypeError) as exc:
            raise ValueError("unknown filesystem profile") from exc
        _validate_path(self.path)
        if profile == FsProfile.WORK:
            allowed = self.path == "/workspace/work"
        elif profile == FsProfile.DIR_LIST:
            allowed = self.path in {"/workspace/app", "/workspace/input"}
        elif profile == FsProfile.FILE_EXEC:
            allowed = self.path in {"/workspace/app/main", RUNTIME_LOADER_PATH}
        else:
            allowed = (self.path in _WORKSPACE_FILES
                       or self.path in {RUNTIME_LOADER_PATH, "/etc/ld.so.cache"}
                       or _library_name(self.path) is not None)
        if not allowed:
            raise ValueError("filesystem rule is outside the exact-file runtime allowlist")
        object.__setattr__(self, "profile", profile)


def _wire_rules(rules: tuple[FsRule, ...]) -> str:
    return "".join(f"{rule.profile.value}\t{rule.path}\n" for rule in rules)


def _policy_hash(version: int, image_id: str, rules: tuple[FsRule, ...]) -> str:
    canonical = json.dumps([version, image_id, [[r.profile.value, r.path] for r in rules]],
                           ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@dataclass(frozen=True)
class FilesystemPolicy:
    version: int
    policy_id: str
    image_id: str
    rules: tuple[FsRule, ...]

    def __post_init__(self) -> None:
        if type(self.version) is not int or self.version != POLICY_VERSION:
            raise ValueError("unsupported filesystem policy version")
        _validate_image_id(self.image_id)
        if not isinstance(self.rules, tuple) or not 1 <= len(self.rules) <= MAX_RULES:
            raise ValueError("filesystem policy rule count exceeds limit or is empty")
        if any(type(rule) is not FsRule for rule in self.rules):
            raise ValueError("filesystem policy requires FsRule records")
        rules = tuple(sorted(self.rules, key=lambda rule: (rule.profile.value, rule.path)))
        if len({rule.path for rule in rules}) != len(rules):
            raise ValueError("duplicate or conflicting filesystem rules")
        if len(("CGFS\t1\t" + "0" * 64 + "\n" + _wire_rules(rules)).encode()) > MAX_POLICY_BYTES:
            raise ValueError("filesystem policy exceeds byte size limit")
        if (not isinstance(self.policy_id, str) or not _POLICY_ID.fullmatch(self.policy_id)
                or self.policy_id != _policy_hash(self.version, self.image_id, rules)):
            raise ValueError("filesystem policy ID does not match canonical policy")
        object.__setattr__(self, "rules", rules)

    def to_bytes(self) -> bytes:
        return (f"CGFS\t{self.version}\t{self.policy_id}\n" + _wire_rules(self.rules)).encode("utf-8")


@dataclass(frozen=True)
class RuntimeFilesystemProfile:
    image_id: str
    version: int
    profile: str
    architecture: str
    loader: str
    libraries: tuple[str, ...]
    cache: str | None
    _verified: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        _validate_image_id(self.image_id)
        if (type(self.version) is not int or self.version != 1
                or self.profile != RUNTIME_PROFILE_NAME or self.architecture != RUNTIME_ARCHITECTURE):
            raise ValueError("unsupported runtime profile/version/architecture")
        if self.loader != RUNTIME_LOADER_PATH:
            raise ValueError("unverified x86_64 runtime loader path")
        if not isinstance(self.libraries, tuple) or len(self.libraries) != len(LIBRARY_NAMES):
            raise ValueError("runtime libraries must be exactly four verified files")
        for path in self.libraries:
            _validate_path(path)
        if {_library_name(path) for path in self.libraries} != LIBRARY_NAMES:
            raise ValueError("runtime contains missing, duplicate or arbitrary library dependencies")
        if self.cache is not None and self.cache != "/etc/ld.so.cache":
            raise ValueError("runtime cache must be absent or the exact loader cache file")
        object.__setattr__(self, "libraries", tuple(sorted(self.libraries)))


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate runtime manifest JSON key")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"invalid runtime manifest JSON constant: {value}")


def parse_runtime_manifest(data: bytes | str, image_id: str) -> RuntimeFilesystemProfile:
    """Validate trusted prepare evidence and bind it to the selected image ID.

    This marks a server-verified profile, not an attestation of arbitrary JSON.
    Callers must establish the trusted container/image provenance first.
    """
    _validate_image_id(image_id)
    if not isinstance(data, (bytes, str)):
        raise ValueError("runtime manifest must be JSON bytes or text")
    raw = data.encode("utf-8") if isinstance(data, str) else data
    if len(raw) > MAX_POLICY_BYTES:
        raise ValueError("runtime manifest exceeds byte limit")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (UnicodeError, RecursionError, ValueError) as exc:
        raise ValueError("invalid runtime manifest JSON") from exc
    if not isinstance(value, dict) or set(value) != {
        "version", "profile", "architecture", "loader", "libraries", "cache",
    }:
        raise ValueError("runtime manifest must contain the exact versioned schema")
    if not isinstance(value["libraries"], list):
        raise ValueError("runtime manifest libraries must be a list of real paths")
    profile = RuntimeFilesystemProfile(image_id=image_id, version=value["version"],
        profile=value["profile"], architecture=value["architecture"], loader=value["loader"],
        libraries=tuple(value["libraries"]), cache=value["cache"])
    object.__setattr__(profile, "_verified", True)
    return profile


def build_filesystem_policy(image_id: str, language, include_source: bool, *,
                            runtime_profile: RuntimeFilesystemProfile | None = None,
                            include_stdin: bool = True) -> FilesystemPolicy:
    """Build only exact file rules from a verified server-selected runtime."""
    _validate_image_id(image_id)
    if (type(runtime_profile) is not RuntimeFilesystemProfile or not runtime_profile._verified
            or runtime_profile.image_id != image_id):
        raise ValueError("filesystem policy requires a verified runtime profile for this image")
    language = getattr(language, "value", language)
    if not isinstance(language, str) or language not in {"C", "CPP"}:
        raise ValueError("unsupported filesystem policy language")
    if type(include_source) is not bool or type(include_stdin) is not bool:
        raise ValueError("provided source/stdin indicators must be booleans")
    rules = [FsRule(FsProfile.FILE_EXEC, "/workspace/app/main"),
             FsRule(FsProfile.FILE_EXEC, runtime_profile.loader),
             FsRule(FsProfile.DIR_LIST, "/workspace/app"),
             FsRule(FsProfile.DIR_LIST, "/workspace/input"),
             FsRule(FsProfile.WORK, "/workspace/work")]
    rules.extend(FsRule(FsProfile.FILE_READ, path) for path in runtime_profile.libraries)
    if runtime_profile.cache is not None:
        rules.append(FsRule(FsProfile.FILE_READ, runtime_profile.cache))
    if include_source:
        rules.append(FsRule(FsProfile.FILE_READ,
                            "/workspace/app/main.c" if language == "C" else "/workspace/app/main.cpp"))
    if include_stdin:
        rules.append(FsRule(FsProfile.FILE_READ, "/workspace/input/stdin"))
    normalized = tuple(sorted(rules, key=lambda rule: (rule.profile.value, rule.path)))
    return FilesystemPolicy(POLICY_VERSION, _policy_hash(POLICY_VERSION, image_id, normalized),
                            image_id, normalized)


def validate_filesystem_policy(policy: FilesystemPolicy) -> FilesystemPolicy:
    """Revalidate an immutable policy at the trusted upload boundary."""
    if type(policy) is not FilesystemPolicy:
        raise ValueError("expected an immutable FilesystemPolicy")
    if not isinstance(policy.rules, tuple):
        raise ValueError("filesystem policy rules must be immutable")
    if any(type(rule) is not FsRule for rule in policy.rules):
        raise ValueError("filesystem policy requires FsRule records")
    rules = tuple(FsRule(rule.profile, rule.path) for rule in policy.rules)
    return FilesystemPolicy(policy.version, policy.policy_id, policy.image_id, rules)


def serialize_filesystem_policy(policy: FilesystemPolicy) -> bytes:
    """Return bounded, canonical version-one TSV after full validation."""
    return validate_filesystem_policy(policy).to_bytes()
