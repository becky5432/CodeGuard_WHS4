"""Fail-closed policy and trusted image manifest contract (no Docker required)."""

import importlib
import importlib.util
import json
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from types import SimpleNamespace

import pytest


IMAGE = "sha256:" + "a" * 64
LOADER = "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
LIBRARIES = {
    "libc.so.6": "/usr/lib/x86_64-linux-gnu/libc.so.6",
    "libstdc++.so.6": "/usr/local/lib64/libstdc++.so.6.0.34",
    "libgcc_s.so.1": "/usr/local/lib64/libgcc_s.so.1",
    "libm.so.6": "/usr/lib/x86_64-linux-gnu/libm.so.6",
}


def manifest(**changes):
    value = dict(version=1, profile="cpp-amd64-v1", architecture="amd64",
                 loader=LOADER, libraries=sorted(LIBRARIES.values()), cache="/etc/ld.so.cache")
    value.update(changes)
    return json.dumps(value).encode()


@pytest.fixture
def fs():
    try:
        return importlib.import_module("runner.policies.filesystem")
    except ModuleNotFoundError as exc:
        message = f"Filesystem policy feature missing: {exc}"
        class MissingFeature:
            def __getattr__(self, name):
                pytest.fail(message)
        return MissingFeature()


def build(fs, **changes):
    options = dict(image_id=IMAGE, language="CPP",
                   runtime_profile=fs.parse_runtime_manifest(manifest(), IMAGE))
    options.update(changes)
    return fs.build_filesystem_policy(**options)


def test_models_and_wire_policy_are_immutable_deterministic(fs):
    policy = build(fs)
    assert policy.version == 2 and policy.image_id == IMAGE
    assert isinstance(policy.rules, tuple)
    assert len(policy.policy_id) == 64
    assert policy.rules == tuple(sorted(policy.rules, key=lambda r: (r.profile.value, r.path)))
    assert policy == build(fs)
    wire = policy.to_bytes()
    assert wire.startswith(f"CGFS\t2\t{policy.policy_id}\n".encode())
    assert wire == (f"CGFS\t2\t{policy.policy_id}\n" + "".join(
        f"{r.profile.value}\t{r.path}\n" for r in policy.rules)).encode()
    for obj, field, value in [(policy, "version", 2), (policy.rules[0], "path", "/"),
                              (fs.parse_runtime_manifest(manifest(), IMAGE), "image_id", "bad")]:
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field, value)
    assert replace(policy, rules=tuple(reversed(policy.rules))) == policy
    with pytest.raises(ValueError):
        replace(policy, policy_id="b" * 64)


def test_policy_is_exact_file_allowlist_without_ambient_access(fs):
    rules = {(r.profile.value, r.path) for r in build(fs).rules}
    assert rules == {
        ("FILE_EXEC", LOADER), ("FILE_READ", "/etc/ld.so.cache"), ("WORK", "/workspace"),
        *(("FILE_READ", path) for path in LIBRARIES.values()),
    }
    assert not any(path.startswith(("/dev", "/proc")) for _, path in rules)


def test_optional_cache_and_devices_are_explicit(fs):
    profile = fs.parse_runtime_manifest(manifest(cache=None), IMAGE)
    policy = build(fs, language="C", runtime_profile=profile)
    assert not any(r.path == "/etc/ld.so.cache" for r in policy.rules)
    assert build(fs, language="C") == build(fs)
    selected = build(fs, device_paths=("/dev/null", "/dev/urandom", "/dev/zero", "/dev/random"))
    assert {(r.profile.value, r.path) for r in selected.rules if r.path.startswith("/dev/")} == {
        ("DEVICE_RW", "/dev/null"), ("DEVICE_READ", "/dev/urandom"),
        ("DEVICE_READ", "/dev/zero"), ("DEVICE_READ", "/dev/random")}
    assert selected.policy_id != build(fs).policy_id
    other = "sha256:" + "b" * 64
    assert build(fs, image_id=other, runtime_profile=fs.parse_runtime_manifest(manifest(), other)).policy_id != build(fs).policy_id


@pytest.mark.parametrize("devices", [("/dev/null", "/dev/null"), ("/dev",), ("/dev/tty",), ("/dev/../null",), " /dev/null", None])
def test_invalid_devices_fail_closed(fs, devices):
    with pytest.raises(ValueError):
        build(fs, device_paths=devices)


def test_device_settings_are_immutable_validated_and_environment_selected(monkeypatch):
    from runner.config import Settings
    monkeypatch.delenv("FILESYSTEM_DEVICE_PATHS", raising=False)
    assert Settings().filesystem_device_paths == ()
    monkeypatch.setenv("FILESYSTEM_DEVICE_PATHS", '["/dev/null", "/dev/random"]')
    assert Settings().filesystem_device_paths == ("/dev/null", "/dev/random")
    for devices in [("/dev/tty",), ("/dev/null", "/dev/null")]:
        with pytest.raises(ValueError):
            Settings(filesystem_device_paths=devices)


@pytest.mark.parametrize("path", ["", "relative", "/workspace/app/../main", "/x/..",
    "/workspace/app/main\x00", "/workspace/app/main\t", "/workspace/app/main\n",
    "/workspace/app/main\r", "//workspace/app/main", "/workspace//app/main",
    "/workspace/./app/main", "/workspace/app/main/", "/" + "챕" * 2048])
def test_invalid_paths_are_rejected_without_normalizing_traversal(fs, path):
    with pytest.raises(ValueError):
        fs.FsRule(fs.FsProfile.FILE_READ, path)


@pytest.mark.parametrize("profile,path", [
    ("UNKNOWN", "/workspace/work"), ("WORK", "/workspace/work"), ("FILE_EXEC", "/workspace/main"), ("DIR_LIST", "/workspace"), ("DEVICE_RW", "/dev/random"), ("DEVICE_READ", "/dev/null"), ("WORK", "/tmp"), ("DIR_LIST", "/usr/lib"),
    ("FILE_EXEC", "/bin/sh"), ("FILE_READ", "/usr/lib/x86_64-linux-gnu"),
    ("FILE_READ", "/dev/null"), ("FILE_READ", "/proc/self/maps"),
    ("FILE_READ", "/etc/hosts"), ("FILE_READ", "/etc/resolv.conf"),
    ("FILE_READ", "/etc/nsswitch.conf"), ("FILE_READ", "/usr/lib/x86_64-linux-gnu/libssl.so.3"),
])
def test_unknown_profiles_and_ambient_authority_are_rejected(fs, profile, path):
    with pytest.raises(ValueError):
        fs.FsRule(profile, path)


def test_policy_rejects_wrong_versions_duplicate_conflicting_and_too_many_rules(fs):
    policy = build(fs)
    for changes in [dict(version=1), dict(version=True), dict(rules=policy.rules + policy.rules[:1]),
                    dict(rules=policy.rules * 20),
                    dict(rules=policy.rules + (fs.FsRule("FILE_READ", LOADER),))]:
        with pytest.raises(ValueError):
            replace(policy, **changes)


def test_policy_limits_use_utf8_bytes_and_complete_wire_size(fs):
    prefix = "/usr/local/lib64/libstdc++.so.6."
    exact = prefix + "1" * (4095 - len(prefix))
    assert fs.FsRule("FILE_READ", exact).path == exact
    with pytest.raises(ValueError):
        fs.FsRule("FILE_READ", exact + "1")
    rules = tuple(fs.FsRule("FILE_READ", prefix + str(i) + "1" * 3990) for i in range(17))
    with pytest.raises(ValueError, match="size|bytes|limit"):
        replace(build(fs), rules=rules)


@pytest.mark.parametrize("image", ["gcc:15.2.0", "a" * 64, "sha256:" + "A" * 64,
                                     "sha256:" + "a" * 63, "sha256:" + "a" * 65, None])
def test_runtime_image_id_must_be_exact_verified_digest(fs, image):
    with pytest.raises(ValueError):
        fs.parse_runtime_manifest(manifest(), image)


def test_builder_requires_manifest_verified_for_exact_server_image(fs):
    profile = fs.parse_runtime_manifest(manifest(), IMAGE)
    with pytest.raises(ValueError):
        fs.build_filesystem_policy(IMAGE, "C", runtime_profile=None)
    for bad in [None, {}, replace(profile), profile.__class__(
        image_id=IMAGE, version=1, profile="cpp-amd64-v1", architecture="amd64",
        loader=LOADER, libraries=tuple(sorted(LIBRARIES.values())), cache=None)]:
        with pytest.raises(ValueError):
            build(fs, runtime_profile=bad)
    with pytest.raises(ValueError):
        build(fs, image_id="sha256:" + "c" * 64)
    for changes in [dict(language="PYTHON")]:
        with pytest.raises(ValueError):
            build(fs, **changes)


@pytest.mark.parametrize("changes", [
    {"version": 2}, {"version": True}, {"profile": "custom"}, {"architecture": "aarch64"},
    {"loader": "/lib64/ld-linux-x86-64.so.2"}, {"loader": "/usr/lib/x86_64-linux-gnu/evil.so"},
    {"cache": "/etc/hosts"}, {"cache": False}, {"extra": "unexpected"},
    {"libraries": {}}, {"libraries": LIBRARIES},
    {"libraries": list(LIBRARIES.values()) + ["/usr/lib/x86_64-linux-gnu/libssl.so.3"]},
    {"libraries": ["/lib/x86_64-linux-gnu/libc.so.6", *list(LIBRARIES.values())[1:]]},
    {"libraries": ["/usr/local/lib64/libevil.so.6", *list(LIBRARIES.values())[1:]]},
    {"libraries": [*list(LIBRARIES.values())[1:], LIBRARIES["libstdc++.so.6"]]},
    {"libraries": ["/usr/local/lib64/../libc.so.6", *list(LIBRARIES.values())[1:]]},
])
def test_manifest_schema_and_dependency_paths_fail_closed(fs, changes):
    with pytest.raises(ValueError):
        fs.parse_runtime_manifest(manifest(**changes), IMAGE)


@pytest.mark.parametrize("data", [b"", b"[]", b"null", b"{", b"\xff", b"x" * 65537,
    manifest().replace(b'"version": 1', b'"version": 1, "version": 1'),
    manifest().replace(b'"version": 1', b'"version": NaN')], ids=lambda value: repr(value)[:55])
def test_manifest_rejects_malformed_duplicate_or_oversized_json(fs, data):
    with pytest.raises(ValueError):
        fs.parse_runtime_manifest(data, IMAGE)


def test_manifest_input_mutations_cannot_change_verified_profile(fs):
    raw = bytearray(manifest())
    profile = fs.parse_runtime_manifest(bytes(raw), IMAGE)
    raw[:] = b"invalid"
    assert profile.libraries == tuple(sorted(LIBRARIES.values()))
    assert build(fs, runtime_profile=profile) == build(fs)


def test_public_validation_and_serialization_revalidate_policy(fs):
    policy = build(fs)
    assert fs.validate_filesystem_policy(policy) == policy
    assert fs.serialize_filesystem_policy(policy) == policy.to_bytes()
    with pytest.raises(ValueError):
        fs.serialize_filesystem_policy({"policy_id": policy.policy_id})


@pytest.fixture
def builder():
    path = Path(__file__).parents[1] / "container/cpp/build-filesystem-manifest.py"
    if not path.is_file():
        class MissingFeature:
            def __getattr__(self, name):
                pytest.fail("Trusted image manifest builder feature missing")
        return MissingFeature()
    spec = importlib.util.spec_from_file_location("filesystem_manifest_builder", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LDD_C = """linux-vdso.so.1 (0x00007fff0000)
libm.so.6 => /lib/x86_64-linux-gnu/libm.so.6 (0x00007fff0001)
libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007fff0002)
/lib64/ld-linux-x86-64.so.2 (0x00007fff0003)
"""
LDD_CPP = """linux-vdso.so.1 (0x00007fff0000)
libstdc++.so.6 => /usr/local/lib64/libstdc++.so.6 (0x00007fff0001)
libm.so.6 => /lib/x86_64-linux-gnu/libm.so.6 (0x00007fff0002)
libgcc_s.so.1 => /usr/local/lib64/libgcc_s.so.1 (0x00007fff0003)
libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x00007fff0004)
/lib64/ld-linux-x86-64.so.2 (0x00007fff0005)
"""


def test_builder_ldd_parser_accepts_only_fixed_candidate_dependencies(builder):
    deps, loaders = builder.parse_ldd_output(LDD_CPP)
    assert set(deps) == set(LIBRARIES)
    assert deps["libc.so.6"] == "/lib/x86_64-linux-gnu/libc.so.6"
    assert loaders == ("/lib64/ld-linux-x86-64.so.2",)


@pytest.mark.parametrize("output", ["", "not a dynamic executable", "libc.so.6 => not found",
    LDD_CPP + "libssl.so.3 => /usr/lib/x86_64-linux-gnu/libssl.so.3 (0x1)\n",
    LDD_CPP + "libc.so.6 => /tmp/libc.so.6 (0x1)\n", "evil loader path (0x1)",
    LDD_CPP.replace("/lib/x86_64-linux-gnu/libc.so.6", "relative")])
def test_builder_rejects_missing_arbitrary_or_duplicate_ldd_dependencies(builder, output):
    with pytest.raises(ValueError):
        builder.parse_ldd_output(output)


def elf_header(*, machine=62, elfclass=2, endian=1, kind=3):
    result = bytearray(64)
    result[:4] = b"\x7fELF"
    result[4:7] = bytes([elfclass, endian, 1])
    result[16:18] = kind.to_bytes(2, "little")
    result[18:20] = machine.to_bytes(2, "little")
    return bytes(result)


def test_builder_verifies_actual_regular_x86_64_elf_files(builder, tmp_path):
    path = tmp_path / "lib.so"
    path.write_bytes(elf_header())
    assert builder.verify_elf(path) == str(path.resolve())
    for content in [b"bad", elf_header(machine=183), elf_header(elfclass=1),
                    elf_header(endian=2), elf_header(kind=1)]:
        path.write_bytes(content)
        with pytest.raises(ValueError):
            builder.verify_elf(path)
    with pytest.raises(ValueError):
        builder.verify_elf(tmp_path)


def test_builder_measures_union_of_only_its_two_fixed_compiler_probes(builder, fs, monkeypatch):
    calls, seen_sources = [], []
    def run(command, **options):
        calls.append(command)
        assert options["check"] is True and options["capture_output"] is True
        assert options["timeout"] == 30 and options.get("shell", False) is False
        if command[0] in {"gcc", "g++"}:
            assert "-O0" in command
            assert ("-std=c17" if command[0] == "gcc" else "-std=c++17") in command
            source = next(Path(arg) for arg in command if arg.endswith((".c", ".cpp")))
            seen_sources.append(source.read_text())
            return SimpleNamespace(stdout="")
        assert command[0] in {"readelf", "ldd"}
        assert Path(command[-1]).name in {"probe-c", "probe-cpp"}
        if command[0] == "readelf":
            return SimpleNamespace(stdout="[Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]\n")
        return SimpleNamespace(stdout=LDD_C if Path(command[-1]).name == "probe-c" else LDD_CPP)
    def resolve(path):
        if path == "/lib64/ld-linux-x86-64.so.2":
            return LOADER
        for soname, actual in LIBRARIES.items():
            if path.endswith("/" + soname):
                return actual
        pytest.fail(f"Unexpected resolved dependency: {path}")
    monkeypatch.setattr(builder.platform, "system", lambda: "Linux")
    monkeypatch.setattr(builder.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(builder.subprocess, "run", run)
    monkeypatch.setattr(builder, "verify_elf", resolve)
    monkeypatch.setattr(builder, "existing_cache", lambda: None)
    value = builder.build_manifest()
    assert value == json.loads(manifest(cache=None))
    assert len(calls) == 6 and len(seen_sources) == 2
    assert "printf" in seen_sources[0] and "std::" in seen_sources[1]
    assert fs.parse_runtime_manifest(json.dumps(value), IMAGE).image_id == IMAGE


def test_builder_rejects_other_architecture_without_running_probes(builder, monkeypatch):
    monkeypatch.setattr(builder.platform, "machine", lambda: "aarch64")
    with pytest.raises(ValueError):
        builder.build_manifest()


def test_builder_destination_is_fixed_by_default_and_manifest_is_readonly(builder, tmp_path):
    assert builder.DEFAULT_DESTINATION == "/usr/local/share/codeguard/filesystem-runtime.json"
    destination = tmp_path / "manifest.json"
    builder.write_manifest(manifest_value=json.loads(manifest()), destination=destination)
    assert json.loads(destination.read_text()) == json.loads(manifest())
    assert destination.stat().st_mode & 0o222 == 0


def test_builder_checks_regular_type_before_opening_potential_fifo(builder, monkeypatch):
    class Fifo:
        def resolve(self, **options):
            return self
        def lstat(self):
            return SimpleNamespace(st_mode=0o010600)
        def open(self, *args):
            pytest.fail("FIFO opened before its file type was checked")
    monkeypatch.setattr(builder, "Path", lambda _: Fifo())
    with pytest.raises(ValueError, match="regular"):
        builder.verify_elf("/usr/local/lib64/libc.so.6")


@pytest.mark.parametrize("language", [None, 7, [], {}])
def test_unknown_language_types_fail_closed_as_validation_errors(fs, language):
    with pytest.raises(ValueError):
        build(fs, language=language)
