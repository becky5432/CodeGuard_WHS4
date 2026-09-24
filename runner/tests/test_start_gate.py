import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from runner.exceptions import ContainerExecutionError, SecurityVerificationError, TaskTrackingError
from runner.pipeline.start_gate import (
    START_READY_PATH,
    find_codeguard_init_tid,
    release_start_gate,
    wait_for_security_evidence,
)
from runner.security.filesystem_trace import TRACE_DIRECTORY


class StartGateTests(unittest.TestCase):
    def test_release_creates_root_owned_regular_marker_in_evidence_volume(self) -> None:
        container = MagicMock()
        container.put_archive.return_value = True

        release_start_gate(container)

        container.put_archive.assert_called_once()
        destination, data = container.put_archive.call_args.args
        self.assertEqual(destination, TRACE_DIRECTORY)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            member = archive.getmember(Path(START_READY_PATH).name)
            self.assertTrue(member.isfile())
            self.assertEqual(member.size, 0)
            self.assertEqual((member.uid, member.gid), (0, 0))

    def test_release_failure_is_not_silently_ignored(self) -> None:
        container = MagicMock()
        container.put_archive.return_value = False
        with self.assertRaises(ContainerExecutionError):
            release_start_gate(container)

    def test_finds_only_verified_direct_child_in_same_cgroup(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            proc = Path(root)
            parent = proc / "100" / "task" / "100"
            parent.mkdir(parents=True)
            (parent / "children").write_text("200 201 202", encoding="utf-8")
            (proc / "100" / "cgroup").write_text("0::/execution\n", encoding="utf-8")
            for tid, cgroup in ((200, "/other"), (201, "/execution"), (202, "/execution")):
                child = proc / str(tid)
                child.mkdir()
                (child / "cgroup").write_text(f"0::{cgroup}\n", encoding="utf-8")

            def executable(path):
                return "/usr/local/bin/codeguard-init" if path.parent.name in {"200", "202"} else "/bin/sh"

            with patch("runner.pipeline.start_gate.os.readlink", side_effect=executable):
                self.assertEqual(find_codeguard_init_tid(100, proc_root=proc), 202)

    def test_missing_verified_child_raises_tracking_error(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            proc = Path(root)
            parent = proc / "100" / "task" / "100"
            parent.mkdir(parents=True)
            (parent / "children").write_text("", encoding="utf-8")
            (proc / "100" / "cgroup").write_text("0::/execution\n", encoding="utf-8")
            with self.assertRaises(TaskTrackingError):
                find_codeguard_init_tid(100, proc_root=proc)

    def test_falls_back_to_cmdline_when_exe_is_not_readable(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            proc = Path(root)
            parent = proc / "100" / "task" / "100"
            parent.mkdir(parents=True)
            (parent / "children").write_text("200", encoding="utf-8")
            (proc / "100" / "cgroup").write_text("0::/execution\n", encoding="utf-8")
            child = proc / "200"
            child.mkdir()
            (child / "cgroup").write_text("0::/execution\n", encoding="utf-8")
            (child / "cmdline").write_bytes(
                b"/usr/local/bin/codeguard-init\0--security-fd\0"
            )

            with patch("runner.pipeline.start_gate.os.readlink", side_effect=PermissionError):
                self.assertEqual(find_codeguard_init_tid(100, proc_root=proc), 200)

            (child / "cmdline").write_bytes(b"/bin/sh\0")
            with patch("runner.pipeline.start_gate.os.readlink", side_effect=PermissionError):
                with self.assertRaises(TaskTrackingError):
                    find_codeguard_init_tid(100, proc_root=proc)

    def test_evidence_waits_for_pass_but_not_for_fail(self) -> None:
        container = MagicMock()
        pending = SecurityVerificationError("status not written yet")
        with patch(
            "runner.pipeline.start_gate.collect_runtime_permission_failure",
            side_effect=[pending, False],
        ) as collector, patch("runner.pipeline.start_gate.time.sleep"):
            wait_for_security_evidence(container, timeout_seconds=1)
        self.assertEqual(collector.call_count, 2)

        with patch("runner.pipeline.start_gate.collect_runtime_permission_failure", return_value=True):
            with self.assertRaises(SecurityVerificationError):
                wait_for_security_evidence(container, timeout_seconds=1)

    def test_evidence_timeout_is_internal_start_failure(self) -> None:
        container = MagicMock()
        with (
            patch(
                "runner.pipeline.start_gate.collect_runtime_permission_failure",
                side_effect=SecurityVerificationError("empty"),
            ),
            patch("runner.pipeline.start_gate.time.monotonic", side_effect=[0, 1]),
        ):
            with self.assertRaises(ContainerExecutionError):
                wait_for_security_evidence(container, timeout_seconds=0.5)


if __name__ == "__main__":
    unittest.main()
