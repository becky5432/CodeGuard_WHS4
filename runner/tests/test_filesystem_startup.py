"""Protected startup evidence tests use real TAR payloads and fake Docker IO."""

import importlib
import io
import tarfile
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

import docker
import pytest

from runner.exceptions import ContainerExecutionError, RunnerError
from runner.tests.test_filesystem_policy import IMAGE, manifest


ID = "a" * 64
PREPARED = f"CGFS_STATUS 1 policy={ID} state=PREPARED abi=7\n".encode()
APPLIED = f"CGFS_STATUS 1 policy={ID} state=APPLIED abi=7\n".encode()
FAILED = f"CGFS_STATUS 1 policy={ID} state=FAILED abi=7 step=enforce errno=1\n".encode()


@pytest.fixture
def startup():
    try:
        return importlib.import_module("runner.security.filesystem_startup")
    except ModuleNotFoundError as exc:
        message = f"Filesystem startup feature missing: {exc}"
        class MissingFeature:
            def __getattr__(self, name):
                pytest.fail(message)
        return MissingFeature()


def archive(data, *, name="filesystem.status", uid=0, gid=0, mode=0o600,
            kind=tarfile.REGTYPE, extra=False, declared_size=None, compressed=False):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz" if compressed else "w", format=tarfile.USTAR_FORMAT) as tar:
        info = tarfile.TarInfo(name)
        info.uid, info.gid, info.mode, info.type = uid, gid, mode, kind
        info.size = len(data) if declared_size is None else declared_size
        info.linkname = "filesystem.status" if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE} else ""
        tar.addfile(info, io.BytesIO(data + b" " * max(0, info.size - len(data))))
        if extra:
            info = tarfile.TarInfo("extra")
            tar.addfile(info)
    return buffer.getvalue()


class Container:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = 0

    def get_archive(self, path):
        assert path == "/run/codeguard-trace/filesystem.status"
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response, {}


def container_for(data, **metadata):
    return Container([archive(data, **metadata)])


def test_policy_archive_is_single_exact_root_private_readonly_file(startup):
    fs = importlib.import_module("runner.policies.filesystem")
    policy = fs.build_filesystem_policy(IMAGE, "C",
        runtime_profile=fs.parse_runtime_manifest(manifest(), IMAGE))
    assert startup.POLICY_PATH == "/run/codeguard-trace/filesystem.policy"
    assert startup.STATUS_PATH == "/run/codeguard-trace/filesystem.status"
    with tarfile.open(fileobj=io.BytesIO(startup.build_policy_archive(policy)), mode="r:") as tar:
        members = tar.getmembers()
        assert len(members) == 1
        member = members[0]
        assert member.name == "filesystem.policy" and member.isfile()
        assert member.uid == member.gid == 0 and member.mode == 0o400
        assert tar.extractfile(member).read() == policy.to_bytes()


def test_prepared_and_applied_require_matching_ordered_evidence(startup):
    status = startup.wait_for_filesystem_prepared(container_for(PREPARED), ID)
    assert status.policy_id == ID and status.abi == 7
    assert status.prepared and not status.applied
    final = startup.verify_filesystem_applied(container_for(PREPARED + APPLIED), ID)
    assert final.prepared and final.applied and final.abi == 7
    with pytest.raises(FrozenInstanceError):
        final.abi = 9


@pytest.mark.parametrize("value", [
    APPLIED, PREPARED + PREPARED, PREPARED + APPLIED + APPLIED,
    PREPARED + APPLIED + PREPARED, PREPARED + b"unknown\n", b"\n" + PREPARED,
    PREPARED.replace(b"abi=7", b"abi=6"), PREPARED.replace(b"abi=7", b"abi=-7"),
    PREPARED.replace(b"abi=7", b"abi=07"), PREPARED.replace(b"abi=7", b"abi=abc"),
    PREPARED.replace(b"abi=7", b"abi=2147483648"),
    PREPARED + APPLIED.replace(b"abi=7", b"abi=8"),
    PREPARED.replace(b"CGFS_STATUS 1", b"CGFS_STATUS 2"),
    PREPARED.replace(b"CGFS_STATUS 1", b"CGFS_STATUS\t1"),
    PREPARED.replace(ID.encode(), b"b" * 64), PREPARED.replace(ID.encode(), b"A" * 64),
    PREPARED.replace(ID.encode(), b"a" * 63), PREPARED.replace(ID.encode(), b"a" * 65),
    PREPARED.replace(b"PREPARED", b"UNKNOWN"),
    PREPARED.replace(b"\n", b" step=x\n"), PREPARED.replace(b"\n", b" abi=7\n"),
    PREPARED.replace(b"\n", b"\r\n"), PREPARED.replace(b"\n", b"\0\n"),
    b"\xff\n", b"x" * 4097 + b"\n", b"x" * 16385,
], ids=lambda value: repr(value)[:80])
def test_malformed_or_contradictory_evidence_is_fatal_without_retry(startup, value):
    container = container_for(value)
    with pytest.raises(startup.FilesystemStartupError):
        startup.wait_for_filesystem_prepared(container, ID)
    assert container.calls == 1


@pytest.mark.parametrize("value", [FAILED, PREPARED + FAILED, PREPARED + APPLIED + FAILED,
    FAILED.replace(b"abi=7", b"abi=0"), FAILED.replace(b" step=enforce errno=1", b"")])
def test_failed_record_is_internal_container_failure_never_retried(startup, value):
    container = container_for(value)
    with pytest.raises(startup.FilesystemStartupError, match="FAILED") as failure:
        startup.wait_for_filesystem_prepared(container, ID)
    assert isinstance(failure.value, (ContainerExecutionError, RunnerError))
    assert failure.value.error_code == ContainerExecutionError.error_code
    assert container.calls == 1


@pytest.mark.parametrize("value", [b"", PREPARED, PREPARED + APPLIED[:-1], PREPARED[:-1]])
def test_final_verification_never_accepts_missing_or_incomplete_evidence(startup, value):
    with pytest.raises(startup.FilesystemStartupError):
        startup.verify_filesystem_applied(container_for(value), ID)


def test_prepare_retries_only_not_found_empty_or_valid_partial_record(startup, monkeypatch):
    monkeypatch.setattr(startup.time, "sleep", lambda _: None)
    container = Container(docker.errors.NotFound("missing"), [archive(b"")],
                          [archive(PREPARED[:-1])], [archive(PREPARED)])
    assert startup.wait_for_filesystem_prepared(container, ID).prepared
    assert container.calls == 4


@pytest.mark.parametrize("value", [b"garbage", PREPARED[:-1] + b" bad", PREPARED[:30] + b"z"])
def test_invalid_incomplete_record_is_fatal_while_preparing(startup, value):
    container = container_for(value)
    with pytest.raises(startup.FilesystemStartupError):
        startup.wait_for_filesystem_prepared(container, ID)
    assert container.calls == 1


def test_missing_incomplete_timeout_and_api_failures_are_internal_errors(startup):
    for response in [docker.errors.NotFound("missing"), [archive(b"")]]:
        with pytest.raises(startup.FilesystemStartupError, match="timeout"):
            startup.wait_for_filesystem_prepared(Container(response), ID, timeout_seconds=0)
    for function in [startup.wait_for_filesystem_prepared, startup.verify_filesystem_applied]:
        with pytest.raises(startup.FilesystemStartupError):
            function(Container(docker.errors.APIError("server error")), ID)
    for timeout in [-1, float("inf"), float("nan"), "5"]:
        with pytest.raises(startup.FilesystemStartupError):
            startup.wait_for_filesystem_prepared(container_for(PREPARED), ID, timeout_seconds=timeout)


@pytest.mark.parametrize("metadata", [
    {"uid": 10001}, {"gid": 10001}, {"mode": 0o644}, {"mode": 0o4600},
    {"name": "./filesystem.status"}, {"name": "/run/codeguard-trace/filesystem.status"},
    {"name": "elsewhere/filesystem.status"}, {"kind": tarfile.SYMTYPE},
    {"kind": tarfile.LNKTYPE}, {"kind": tarfile.DIRTYPE}, {"kind": tarfile.FIFOTYPE},
    {"extra": True}, {"declared_size": 16385}, {"compressed": True},
])
def test_unprotected_nonexact_extra_or_oversized_archive_is_fatal(startup, metadata):
    with pytest.raises(startup.FilesystemStartupError):
        startup.wait_for_filesystem_prepared(container_for(PREPARED, **metadata), ID)


def test_duplicate_exact_status_members_are_rejected(startup):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tar:
        for _ in range(2):
            info = tarfile.TarInfo("filesystem.status")
            info.uid = info.gid = 0
            info.mode, info.size = 0o600, len(PREPARED + APPLIED)
            tar.addfile(info, io.BytesIO(PREPARED + APPLIED))
    with pytest.raises(startup.FilesystemStartupError):
        startup.verify_filesystem_applied(Container([data.getvalue()]), ID)


def test_archive_stream_is_bounded_before_buffering_and_always_closed(startup):
    class OversizedChunk(bytes):
        def __len__(self):
            return startup.STATUS_ARCHIVE_LIMIT_BYTES + 1
    closed = []
    def chunks():
        try:
            yield OversizedChunk(b"small backing allocation")
            pytest.fail("oversized archive should stop before requesting another chunk")
        finally:
            closed.append(True)
    with pytest.raises(startup.FilesystemStartupError, match="archive"):
        startup.verify_filesystem_applied(Container(chunks()), ID)
    assert closed == [True]


def test_archive_and_stream_errors_are_wrapped_and_closed(startup):
    for payload in [b"not a tar", archive(PREPARED)[:600]]:
        with pytest.raises(startup.FilesystemStartupError):
            startup.wait_for_filesystem_prepared(Container([payload]), ID)
    closed = []
    def broken_stream():
        try:
            raise OSError("stream broke")
            yield b""
        finally:
            closed.append(True)
    with pytest.raises(startup.FilesystemStartupError):
        startup.verify_filesystem_applied(Container(broken_stream()), ID)
    assert closed == [True]


def test_invalid_expected_policy_ids_are_rejected_before_reading(startup):
    for policy_id in ["", "A" * 64, "a" * 63, "a" * 65, None]:
        container = Mock()
        with pytest.raises(startup.FilesystemStartupError):
            startup.wait_for_filesystem_prepared(container, policy_id)
        container.get_archive.assert_not_called()


def test_status_parser_rejects_malformed_data_and_accepts_future_abi(startup):
    status = startup.parse_filesystem_status((PREPARED + APPLIED).replace(b"abi=7", b"abi=10"), ID)
    assert status.applied and status.abi == 10
    for data in [None, "text", bytearray(PREPARED), PREPARED.replace(b"abi=7", b"abi=1" * 1000)]:
        with pytest.raises(startup.FilesystemStartupError):
            startup.parse_filesystem_status(data, ID)


def test_every_valid_inflight_prepared_prefix_can_retry(startup, monkeypatch):
    monkeypatch.setattr(startup.time, "sleep", lambda _: None)
    for length in range(len(PREPARED)):
        container = Container([archive(PREPARED[:length])], [archive(PREPARED)])
        assert startup.wait_for_filesystem_prepared(container, ID).prepared
        assert container.calls == 2


@pytest.mark.parametrize("payload", [
    archive(PREPARED + APPLIED) + archive(PREPARED + APPLIED),
    archive(PREPARED + APPLIED) + b"hidden garbage",
    archive(PREPARED + APPLIED)[:1024],
], ids=["concatenated-tar", "trailing-garbage", "missing-end-marker"])
def test_hidden_trailing_tar_payload_or_missing_end_marker_is_rejected(startup, payload):
    with pytest.raises(startup.FilesystemStartupError):
        startup.verify_filesystem_applied(Container([payload]), ID)


def test_protected_regular_docker_tar_may_carry_precise_pax_timestamps(startup):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.PAX_FORMAT) as tar:
        info = tarfile.TarInfo("filesystem.status")
        info.mode, info.uid, info.gid, info.size = 0o600, 0, 0, len(PREPARED + APPLIED)
        info.pax_headers = {"mtime": "1754300000.123456789"}
        tar.addfile(info, io.BytesIO(PREPARED + APPLIED))
    assert startup.verify_filesystem_applied(Container([buffer.getvalue()]), ID).applied
