"""Report contracts; these tests do not require Docker or Linux."""

import errno
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

from runner.tests import filesystem_acceptance as acceptance


def case(allowed=False):
    return acceptance.Case("N01", "그 밖의 경로", "/etc/probe", "create", allowed)


def stdout(rc, error, verified=1):
    return "CGFS_RESULT " + json.dumps({"rc": rc, "errno": error, "verified": verified}) + "\n"


def test_read_errno_does_not_turn_missing_file_into_a_policy_pass():
    result = acceptance.evaluate_probe(case(), stdout(-1, errno.ENOENT))
    assert result.status == "FAIL"
    assert "ENOENT" in result.actual


def test_cross_mount_error_does_not_prove_landlock_denial():
    assert acceptance.evaluate_probe(case(), stdout(-1, errno.EXDEV)).status == "FAIL"


def test_denial_requires_successful_precondition_and_real_permission_errno():
    for error in (errno.EACCES, errno.EPERM, errno.EROFS):
        assert acceptance.evaluate_probe(case(), stdout(-1, error)).status == "PASS"
        assert acceptance.evaluate_probe(case(), stdout(-1, error, 0)).status == "FAIL"


def test_normal_operation_requires_verified_effect():
    assert acceptance.evaluate_probe(case(True), stdout(0, 0)).status == "PASS"
    assert acceptance.evaluate_probe(case(True), stdout(0, 0, 0)).status == "FAIL"
    assert acceptance.evaluate_probe(case(True), stdout(-1, errno.EACCES)).status == "FAIL"


def test_missing_duplicate_or_malformed_probe_output_is_failure():
    for value in ("", "OPEN_ALLOWED", stdout(0, 0) * 2,
                  'CGFS_RESULT {"rc":0,"errno":0,"verified":true}\n'):
        assert acceptance.evaluate_probe(case(True), value).status == "FAIL"


def test_system_error_and_nonzero_exit_do_not_pass():
    assert acceptance.evaluate_probe(case(True), stdout(0, 0), exit_code=2).status == "ERROR"
    assert acceptance.evaluate_probe(case(True), stdout(0, 0), system_error="trace missing").status == "ERROR"


def test_catalog_uses_actual_manifest_paths_and_selected_devices():
    def rule(profile, path):
        return SimpleNamespace(profile=SimpleNamespace(value=profile), path=path)
    policy = SimpleNamespace(rules=(rule("WORK", "/workspace"),
        rule("FILE_EXEC", "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"),
        rule("FILE_READ", "/usr/local/lib64/libstdc++.so.6.0.99"),
        rule("DEVICE_RW", "/dev/null")))
    cases = acceptance.build_cases(policy)
    assert any(item.path.endswith("6.0.99") and item.operation == "read" for item in cases)
    assert any(item.path == "/dev/null" and item.operation == "device_write" and item.allowed for item in cases)
    assert not any(item.path == "/dev/random" for item in cases)
    assert len({item.id for item in cases}) == len(cases)
    assert {item.operation for item in cases if item.group == "/workspace"} >= {
        "read", "list", "create", "mkdir", "write", "append", "truncate",
        "delete", "rmdir", "rename", "move_dir", "exec",
    }


def test_report_keeps_enforcement_detection_and_skip_separate(tmp_path):
    row = acceptance.evaluate_probe(case(), stdout(-1, errno.EACCES))
    skip = acceptance.Row("E01", "eBPF", "/etc/probe", "거부 이벤트", "감지", "미지원", "SKIP")
    output = acceptance.write_report(tmp_path, [row, skip], {"image_id": "test-image"})
    data = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert data["counts"] == {"PASS": 1, "FAIL": 0, "ERROR": 0, "SKIP": 1}
    assert data["rows"][0]["path"] == "/etc/probe"
    assert output == tmp_path / "report.html"
    assert "SKIP" in output.read_text(encoding="utf-8")
    assert (tmp_path / "results.md").is_file()


def test_report_escapes_observed_output_and_paths(tmp_path):
    row = acceptance.Row("T01", "strace", '<script>alert(1)</script>', "쓰기", "거부", '<img src=x>', "FAIL")
    output = acceptance.write_report(tmp_path, [row], {})
    html = output.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;img src=x&gt;" in html


def test_report_exit_status_does_not_treat_all_skipped_as_success():
    def row(status):
        return acceptance.Row("E", "eBPF", "-", "-", "-", "-", status)
    assert acceptance.exit_status([row("PASS")]) == 0
    assert acceptance.exit_status([row("PASS"), row("FAIL")]) == 1
    assert acceptance.exit_status([row("SKIP")]) == 2


def test_ebpf_unsupported_and_wrong_run_cannot_pass():
    rows = acceptance.ebpf_rows([], supported=False, enabled=False, required=False)
    assert rows and all(row.status == "SKIP" for row in rows)
    rows = acceptance.ebpf_rows([], supported=False, enabled=True, required=True)
    assert rows and all(row.status == "FAIL" for row in rows)
    event = {"source": "LANDLOCK_EBPF_FENTRY", "run_id": "wrong", "pid": 1, "tid": 1}
    sample = {"case": case(), "run_id": "expected", "events": [event], "violation": True, "probe_pass": True}
    rows = acceptance.ebpf_rows([sample], supported=True, enabled=True, required=True)
    assert next(row for row in rows if row.id == "E03").status == "FAIL"


def test_ebpf_requires_an_intentional_landlock_case_not_only_erofs():
    item = acceptance.Case("N03", "그 밖의 경로", "/etc/probe", "create", False)
    sample = {"case": item, "run_id": "r", "probe_pass": True, "violation": True,
              "events": [{"run_id": "r", "source": "LANDLOCK_EBPF_FENTRY"}]}
    rows = acceptance.ebpf_rows([sample], supported=True, enabled=True, required=True)
    assert next(row for row in rows if row.id == "E01").status == "FAIL"


def test_inactive_ebpf_detector_is_skipped_not_reported_as_quiet_success():
    sample = {"case": case(True), "run_id": "r", "probe_pass": True,
              "violation": False, "events": [], "collector_active": False}
    rows = acceptance.ebpf_rows([sample], supported=True, enabled=True, required=False)
    assert all(row.status == "SKIP" for row in rows)
    rows = acceptance.ebpf_rows([sample], supported=True, enabled=True, required=True)
    assert all(row.status == "FAIL" for row in rows)
    sample["collector_active"] = None
    rows = acceptance.ebpf_rows([sample], supported=True, enabled=True, required=False)
    assert all(row.status == "FAIL" for row in rows)


def test_tracker_proxy_preserves_real_snapshot_and_observes_active_state():
    snapshot = SimpleNamespace(active=True, violations=())
    calls = []
    client = SimpleNamespace(filesystem_snapshot=lambda run: (calls.append(run), snapshot)[1],
                             register_root=lambda *args: calls.append(args))
    tracker = acceptance.ObservedTaskTracker(client)
    tracker.register_root("r", 12)
    assert tracker.filesystem_snapshot("r") is snapshot
    assert tracker.active_by_run == {"r": True}
    assert calls == [("r", 12), "r"]


def test_active_ebpf_detector_requires_denied_event_and_quiet_normal_run():
    denied = {"case": acceptance.Case("N02", "그 밖의 경로", "/proc/self/status", "read", False),
              "run_id": "denied", "probe_pass": True, "violation": True, "collector_active": True,
              "events": [{"run_id": "denied", "source": "LANDLOCK_EBPF_FENTRY"}]}
    normal = {"case": case(True), "run_id": "normal", "probe_pass": True,
              "violation": False, "events": [], "collector_active": True}
    rows = acceptance.ebpf_rows([denied, normal], supported=True, enabled=True, required=True)
    assert all(row.status == "PASS" for row in rows)
    normal["collector_active"] = False
    assert all(row.status == "FAIL" for row in acceptance.ebpf_rows(
        [denied, normal], supported=True, enabled=True, required=False))


def test_dirfd_trace_evidence_selects_target_not_unrelated_loader_probe():
    trace = ('openat(AT_FDCWD, "/etc/ld.so.preload", O_RDONLY) = -1 EACCES (Permission denied)\n'
             'openat(3</etc>, "cg-dirfd", O_WRONLY|O_CREAT|O_EXCL, 0600) = -1 EROFS (Read-only file system)\n')
    item = acceptance.Case("N08", "그 밖의 경로", "/etc (dirfd) / cg-dirfd", "dirfd_write", False, "cg-dirfd", True)
    execution = SimpleNamespace(filesystem_limit_exceeded=True)
    enforcement = acceptance.Row("N08", item.group, item.path, "쓰기", "거부", "EROFS", "PASS")
    row = acceptance.trace_row(item, execution, trace, enforcement)
    assert row.status == "PASS"
    assert "cg-dirfd" in row.actual
    assert "ld.so.preload" not in row.actual


def test_trace_report_distinguishes_strace_attempt_from_landlock_event():
    item = acceptance.Case("A02W", "외부 허용목록", "/etc/ld.so.cache", "write_existing", False)
    trace = 'openat(AT_FDCWD, "/etc/ld.so.cache", O_WRONLY) = -1 EACCES (Permission denied)'
    enforcement = acceptance.evaluate_probe(item, stdout(-1, errno.EACCES))
    execution = SimpleNamespace(filesystem_limit_exceeded=True,
        filesystem_violation_source="STRACE", filesystem_violations=())
    row = acceptance.trace_row(item, execution, trace, enforcement)
    assert row.status == "PASS"
    assert "Runner 근거=STRACE" in row.actual
    assert "Landlock 거부 이벤트=0건" in row.actual
    execution.filesystem_limit_exceeded = False
    assert acceptance.trace_row(item, execution, trace, enforcement).status == "FAIL"


def test_trace_report_keeps_real_landlock_event_source():
    item = acceptance.Case("N05", "그 밖의 경로", "/dev/shm/cg-created", "create", False)
    trace = 'openat(AT_FDCWD, "/dev/shm/cg-created", O_WRONLY|O_CREAT, 0600) = -1 EACCES'
    execution = SimpleNamespace(filesystem_limit_exceeded=True,
        filesystem_violation_source="LANDLOCK_EBPF_FENTRY", filesystem_violations=(object(),))
    enforcement = acceptance.evaluate_probe(item, stdout(-1, errno.EACCES))
    row = acceptance.trace_row(item, execution, trace, enforcement)
    assert row.status == "PASS"
    assert "Runner 근거=LANDLOCK_EBPF_FENTRY" in row.actual
    assert "Landlock 거부 이벤트=1건 (실행 단위)" in row.actual


def test_cli_prints_failed_detection_details(tmp_path, capsys):
    from unittest.mock import patch
    row = acceptance.Row("T-A02W", "strace", "/etc/ld.so.cache", "쓰기", "감지",
                         "Runner 정책 위반=없음", "FAIL")
    with patch.object(acceptance, "run_checks", side_effect=lambda args, output, rows, meta: rows.append(row)):
        code = acceptance.main(["--output-dir", str(tmp_path), "--ebpf", "off"])
    assert code == 1
    assert "[T-A02W] FAIL | strace | /etc/ld.so.cache | Runner 정책 위반=없음" in capsys.readouterr().out


@unittest.skipUnless(sys.platform == "linux" and os.environ.get("CODEGUARD_TEST_NATIVE") == "1"
                     and shutil.which("gcc"), "Linux gcc 진단 프로그램 검증: CODEGUARD_TEST_NATIVE=1 필요")
class NativeAcceptanceProbeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = tempfile.TemporaryDirectory(prefix="cg-acceptance-build-")
        cls.addClassCleanup(directory.cleanup)
        cls.binary = Path(directory.name) / "probe"
        source = Path(__file__).with_name("filesystem_acceptance_probe.c")
        subprocess.run(["gcc", "-std=c17", "-O2", "-Wall", "-Wextra", "-Werror",
                        str(source), "-o", str(cls.binary)], check=True, capture_output=True, text=True)

    def test_normal_operations_verify_real_file_effects(self):
        with tempfile.TemporaryDirectory(prefix="cg-acceptance-cases-") as directory:
            root = Path(directory)
            read_path = root / "input"
            read_path.write_text("test input\n", encoding="utf-8")
            operations = ("read", "list", "create", "mkdir", "write", "append", "truncate",
                          "delete", "rmdir", "rename", "move_dir", "symlink_write", "dirfd_write", "fake")
            for operation in operations:
                with self.subTest(operation=operation):
                    path = str(root / operation)
                    aux = str(root / (operation + "-destination"))
                    if operation == "read":
                        path = str(read_path)
                    if operation in {"list", "dirfd_write"}:
                        path = str(root)
                    if operation == "dirfd_write":
                        aux = "dirfd-child"
                    run = subprocess.run([str(self.binary)], input=f"{operation}\n{path}\n{aux}\n",
                                         capture_output=True, text=True, timeout=5)
                    item = acceptance.Case(operation, "/workspace", path, operation, True)
                    result = acceptance.evaluate_probe(item, run.stdout, exit_code=run.returncode)
                    self.assertEqual(result.status, "PASS", (operation, run.stdout, run.stderr))

    def test_absent_file_is_not_reported_as_access_denial(self):
        with tempfile.TemporaryDirectory(prefix="cg-acceptance-absent-") as directory:
            path = str(Path(directory) / "absent")
            run = subprocess.run([str(self.binary)], input=f"read\n{path}\n\n", capture_output=True, text=True, timeout=5)
            result = acceptance.evaluate_probe(acceptance.Case("N", "그 밖의 경로", path, "read", False), run.stdout)
            self.assertEqual(result.status, "FAIL")
            self.assertIn("ENOENT", result.actual)

    def test_exec_child_protocol(self):
        run = subprocess.run([str(self.binary), "--cg-child"], capture_output=True, text=True, timeout=5)
        result = acceptance.evaluate_probe(acceptance.Case("X", "/workspace", str(self.binary), "exec", True), run.stdout)
        self.assertEqual(result.status, "PASS")

    @unittest.skipUnless(shutil.which("g++"), "C++ 진단 프로그램 검증에 g++ 필요")
    def test_cpp_standard_library_probe(self):
        with tempfile.TemporaryDirectory(prefix="cg-acceptance-cpp-") as directory:
            source = Path(directory) / "main.cpp"
            binary = Path(directory) / "main"
            source.write_text(acceptance.CPP_SOURCE, encoding="utf-8")
            subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", str(source), "-o", str(binary)],
                           check=True, capture_output=True, text=True, timeout=30)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=5)
            row = acceptance.evaluate_probe(acceptance.Case("CPP", "/workspace", str(binary), "exec", True), run.stdout)
            self.assertEqual(row.status, "PASS", run.stderr)
