"""Bounded Linux native tests; no Docker or production requests."""

import os
import ctypes
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FS = ROOT / "native/filesystem"


@unittest.skipUnless(os.name == "posix" and shutil.which("gcc"), "requires Linux gcc")
class NativeFilesystemTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0,
                         "init identity/status integration requires root in isolated Linux test")
    def test_init_handshake_and_failures(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o755)
            marker = root / "start.ready"
            binary = root / "init"
            probe = root / "probe"
            modules = [FS / name for name in ("landlock_policy.c", "policy_reader.c", "startup_status.c")]
            result = subprocess.run(
                ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(FS),
                 f'-DSTART_READY_PATH="{marker}"', "-DSTART_WAIT_SECONDS=1",
                 str(ROOT / "native/task_tracker/codeguard_init.c"),
                 *map(str, modules), str(FS / "tests/init_syscalls.c"),
                 "-Wl,--wrap=fsync", "-Wl,--wrap=fstat", "-Wl,--wrap=syscall", "-o", str(binary)],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror",
                            str(FS / "tests/exec_probe.c"), "-o", str(probe)], check=True, timeout=20)
            work = root / "work"
            work.mkdir(mode=0o755)
            stdin = root / "stdin"
            stdin.write_text("hello", encoding="ascii")
            stdin.chmod(0o444)
            policy_id = "a" * 64
            policy = root / "policy"
            payload = f"CGFS\t2\t{policy_id}\nFILE_READ\t/etc/ld.so.cache\n"
            policy.write_text(payload, encoding="ascii")
            policy.chmod(0o400)
            libc = ctypes.CDLL(None, use_errno=True)

            def identity():
                os.setgroups([])
                os.setgid(10001)
                os.setuid(10001)
                if libc.prctl(38, 1, 0, 0, 0) != 0:
                    os._exit(90)

            def run_case(failure="", drop=True, ready=True, malformed=False, tracer=False, close_stdin=False,
                         unavailable_paths=False):
                if ready:
                    marker.touch()
                else:
                    marker.unlink(missing_ok=True)
                policy.write_text(payload + ("UNKNOWN\t/bad\n" if malformed else ""), encoding="ascii")
                status = root / "status"
                security = root / "security"
                status.write_bytes(b"")
                status.chmod(0o600)
                security.write_bytes(b"")
                security.chmod(0o600)
                with security.open("r+b") as sec, policy.open("rb") as pol, status.open("r+b") as sta:
                    # Simulate descriptors retained across an external tracer/launcher.
                    leak = os.open("/etc/passwd", os.O_RDONLY)
                    high = 4096
                    os.dup2(leak, high, inheritable=True)
                    command = [str(binary), "--security-fd", str(sec.fileno()),
                               "--filesystem-policy-fd", str(pol.fileno()),
                               "--filesystem-status-fd", str(sta.fileno()),
                               "--stdin", str(root / "missing-stdin" if unavailable_paths else stdin),
                               "--workdir", str(root / "missing-work" if unavailable_paths else work),
                               "--", str(probe)]
                    if tracer:
                        command = [shutil.which("strace"), "-f", "-o", str(root / "trace"), "--", *command]
                    try:
                        def child_identity():
                            if drop:
                                identity()
                            if close_stdin:
                                os.close(0)
                        process = subprocess.Popen(command, pass_fds=(sec.fileno(), pol.fileno(), sta.fileno(), leak, high),
                            preexec_fn=child_identity, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, env={**os.environ, "CG_NATIVE_FAILURE": failure})
                        if not ready and not failure:
                            deadline = time.monotonic() + 0.8
                            while not status.read_bytes() and time.monotonic() < deadline:
                                time.sleep(0.01)
                            self.assertIn(b"state=PREPARED", status.read_bytes())
                            self.assertIsNone(process.poll())
                        stdout, stderr = process.communicate(timeout=4)
                        return process.returncode, stdout, stderr, status.read_text(), security.read_text()
                    finally:
                        os.close(leak)
                        os.close(high)

            code, out, err, status, security = run_case()
            self.assertEqual(code, 0, err)
            self.assertIn("EXECUTED stdin=hello cwd=" + str(work), out)
            self.assertEqual(security, "SECURITY_VERIFICATION_PASSED\n")
            self.assertEqual(status, f"CGFS_STATUS 1 policy={policy_id} state=PREPARED abi=7\n"
                             f"CGFS_STATUS 1 policy={policy_id} state=APPLIED abi=7\n")
            for failure, expected_step in (("abi", "abi"), ("create", "create_ruleset"),
                                           ("add", "add_rule"), ("enforce", "enforce"), ("sync", "status")):
                code, out, err, status, _ = run_case(failure=failure)
                self.assertEqual(code, 126, err)
                self.assertNotIn("EXECUTED", out)
                self.assertIn("state=FAILED", status)
                self.assertIn("step=" + expected_step, status)
                self.assertNotIn("state=APPLIED", status)
            code, out, err, status, _ = run_case(failure="close_range")
            self.assertEqual(code, 126, err)
            self.assertNotIn("EXECUTED", out)
            self.assertIn("close_range", err)
            code, out, err, status, _ = run_case(failure="stdin_stat")
            self.assertEqual(code, 126, err)
            self.assertNotIn("EXECUTED", out)
            self.assertIn("step=stdin_type errno=5", status)
            code, out, err, status, _ = run_case(close_stdin=True)
            self.assertEqual(code, 0, err)
            self.assertIn("EXECUTED stdin=hello", out)
            code, out, err, status, _ = run_case(ready=False)
            self.assertEqual(code, 126, err)
            self.assertNotIn("EXECUTED", out)
            self.assertIn("step=start_gate errno=110", status)
            code, out, err, status, _ = run_case(drop=False)
            self.assertEqual(code, 200, err)
            self.assertNotIn("EXECUTED", out)
            self.assertIn("step=security errno=1", status)
            # Invalid identity must produce its trusted failure token even if
            # input/work paths are inaccessible or absent. No gate release.
            code, out, err, status, security = run_case(drop=False, unavailable_paths=True)
            self.assertEqual(code, 200, err)
            self.assertEqual(security, "SECURITY_VERIFICATION_FAILED\n")
            self.assertIn("step=security errno=1", status)
            self.assertNotIn("state=PREPARED", status)
            self.assertNotIn("state=APPLIED", status)
            self.assertNotIn("EXECUTED", out)
            code, out, err, status, _ = run_case(malformed=True)
            self.assertEqual(code, 126, err)
            self.assertIn("step=policy_parse errno=22", status)
            for args in (["--security-fd", "3", "--", str(probe)],
                         ["--security-fd", "3", "--filesystem-policy-fd", "4", "--", str(probe)]):
                result = subprocess.run([str(binary), *args], capture_output=True, text=True, timeout=3)
                self.assertEqual(result.returncode, 126)
                self.assertNotIn("EXECUTED", result.stdout)
            if shutil.which("strace"):
                # Trace destination must be writable after dropping identity.
                trace = root / "trace"
                trace.touch(mode=0o666)
                trace.chmod(0o666)
                code, out, err, status, _ = run_case(tracer=True)
                self.assertEqual(code, 0, err)
                self.assertIn("EXECUTED", out)

    @unittest.skipUnless(os.name == "posix" and shutil.which("strace"), "strace not installed")
    def test_init_fds_through_strace(self):
        self.test_init_handshake_and_failures()

    def test_init_mandatory_filesystem_contract(self):
        source = (ROOT / "native/task_tracker/codeguard_init.c").read_text(encoding="utf-8")
        self.assertIn("--filesystem-policy-fd", source)
        self.assertIn("--filesystem-status-fd", source)
        self.assertIn("--workdir", source)
        self.assertIn("cg_fs_prepare_ruleset", source)
        self.assertIn("cg_fs_enforce", source)
        self.assertIn("SYS_close_range", source)

    def test_native_contract(self):
        self.assertTrue((FS / "landlock_policy.h").is_file(), "native filesystem API is missing")
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "native-contract"
            result = subprocess.run(
                ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(FS),
                 str(FS / "tests/contract.c"), str(FS / "policy_reader.c"),
                 str(FS / "landlock_policy.c"), str(FS / "startup_status.c"),
                 "-o", str(binary)], capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_status_failure_injection(self):
        self._run_harness("status_failures.c")

    def test_path_rule_validation(self):
        self._run_harness("path_rules.c")

    @unittest.skipUnless(os.environ.get("CG_RUN_LANDLOCK_SMOKE") == "1", "opt in with CG_RUN_LANDLOCK_SMOKE=1")
    def test_actual_landlock_abi_smoke(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "abi-smoke"
            result = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(FS),
                str(FS / "tests/abi_smoke.c"), str(FS / "landlock_policy.c"), "-o", str(binary)],
                capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            if result.returncode == 77:
                self.skipTest(result.stdout.strip())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def _run_harness(self, name):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "harness"
            result = subprocess.run(["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(FS),
                str(FS / "tests" / name), "-o", str(binary)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_image_build_contract(self):
        dockerfile = (ROOT / "container/cpp/Dockerfile").read_text(encoding="utf-8")
        self.assertIn("landlock_policy.c", dockerfile)
        self.assertIn("policy_reader.c", dockerfile)
        self.assertIn("startup_status.c", dockerfile)
        self.assertIn("workspace_prepare.c", dockerfile)
        self.assertIn("/usr/local/bin/codeguard-workspace-prepare", dockerfile)
        self.assertIn("build-filesystem-manifest.py", dockerfile)
        self.assertIn("/usr/local/share/codeguard/filesystem-runtime.json", dockerfile)
        self.assertIn("python3", dockerfile)
        self.assertNotIn("chown 10001:10001 /workspace", dockerfile)


if __name__ == "__main__":
    unittest.main()
