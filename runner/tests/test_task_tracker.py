import struct
import tempfile
import unittest
from pathlib import Path
from uuid import UUID, uuid4

from runner.exceptions import TaskTrackingError
from runner.metrics.task_tracker import (
    CG_ERR_COUNTER,
    CG_OP_REGISTER_CGROUP,
    CG_OP_REGISTER_ROOT,
    CG_OP_REMOVE,
    CG_OP_SNAPSHOT,
    CG_TRACKER_MAGIC,
    CG_TRACKER_VERSION,
    REQUEST_STRUCT,
    RESPONSE_STRUCT,
    TaskTrackerClient,
    resolve_execution_cgroup,
)


class FakeTransport:
    def __init__(
        self,
        *,
        status: int = 0,
        user_task_peak: int = 0,
        process_at_user_task_peak: int = 0,
        thread_at_user_task_peak: int = 0,
        error_flags: int = 0,
        magic: int = CG_TRACKER_MAGIC,
        version: int = CG_TRACKER_VERSION,
    ) -> None:
        self.requests: list[bytes] = []
        self.response = RESPONSE_STRUCT.pack(
            magic,
            version,
            0,
            status,
            user_task_peak,
            process_at_user_task_peak,
            thread_at_user_task_peak,
            error_flags,
        )

    def request(self, payload: bytes, response_size: int) -> bytes:
        self.requests.append(payload)
        if response_size != len(self.response):
            raise AssertionError("unexpected response size")
        return self.response


class TaskTrackerClientTests(unittest.TestCase):
    def test_snapshot_returns_user_task_peak_snapshot(self) -> None:
        transport = FakeTransport(
            user_task_peak=15,
            process_at_user_task_peak=3,
            thread_at_user_task_peak=12,
        )
        client = TaskTrackerClient(transport=transport)
        run_id = uuid4()

        result = client.snapshot(run_id)

        self.assertEqual(result.user_task_peak, 15)
        self.assertEqual(result.process_at_user_task_peak, 3)
        self.assertEqual(result.thread_at_user_task_peak, 12)
        request = REQUEST_STRUCT.unpack(transport.requests[0])
        self.assertEqual(request[2], CG_OP_SNAPSHOT)
        self.assertEqual(UUID(bytes=request[3]), run_id)

    def test_register_cgroup_sends_initial_task_count(self) -> None:
        transport = FakeTransport()
        client = TaskTrackerClient(transport=transport)
        run_id = uuid4()

        client.register_cgroup(run_id, cgroup_id=1234, initial_task_count=6)

        request = REQUEST_STRUCT.unpack(transport.requests[0])
        self.assertEqual(request[2], CG_OP_REGISTER_CGROUP)
        self.assertEqual(request[4], 1234)
        self.assertEqual(request[5], 0)
        self.assertEqual(request[6], 6)

    def test_register_root_and_remove_use_expected_operations(self) -> None:
        transport = FakeTransport()
        client = TaskTrackerClient(transport=transport)
        run_id = uuid4()

        client.register_root(run_id, root_tid=4321)
        client.remove(run_id)

        first = REQUEST_STRUCT.unpack(transport.requests[0])
        second = REQUEST_STRUCT.unpack(transport.requests[1])
        self.assertEqual(first[2], CG_OP_REGISTER_ROOT)
        self.assertEqual(first[5], 4321)
        self.assertEqual(second[2], CG_OP_REMOVE)

    def test_snapshot_rejects_measurement_error_flags(self) -> None:
        client = TaskTrackerClient(
            transport=FakeTransport(error_flags=CG_ERR_COUNTER),
        )

        with self.assertRaises(TaskTrackingError):
            client.snapshot(uuid4())

    def test_response_rejects_protocol_version_mismatch(self) -> None:
        client = TaskTrackerClient(
            transport=FakeTransport(version=CG_TRACKER_VERSION + 1),
        )

        with self.assertRaises(TaskTrackingError):
            client.snapshot(uuid4())

    def test_response_rejects_nonzero_status(self) -> None:
        client = TaskTrackerClient(transport=FakeTransport(status=-2))

        with self.assertRaises(TaskTrackingError):
            client.snapshot(uuid4())

    def test_shared_header_defines_binary_protocol_fields(self) -> None:
        header = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "task_tracker"
            / "task_tracker_shared.h"
        ).read_text(encoding="utf-8")

        for field in (
            "initial_task_count",
            "user_task_current",
            "user_task_peak",
            "process_at_user_task_peak",
            "thread_at_user_task_peak",
            "error_flags",
        ):
            self.assertIn(field, header)
        self.assertEqual(REQUEST_STRUCT.size, 40)
        self.assertEqual(RESPONSE_STRUCT.size, 28)

    def test_ebpf_metrics_only_track_user_task_lineage(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        header = (native_dir / "task_tracker_shared.h").read_text(
            encoding="utf-8"
        )
        source = (native_dir / "task_tracker.bpf.c").read_text(
            encoding="utf-8"
        )

        for token in ("container_task_current", "container_task_peak"):
            self.assertNotIn(token, header)
            self.assertNotIn(token, source)

    def test_bpf_program_tracks_fork_exit_and_same_peak_snapshot(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        source = (native_dir / "task_tracker.bpf.c").read_text(
            encoding="utf-8"
        )

        for token in (
            'SEC("tp_btf/sched_process_fork")',
            'SEC("tp_btf/sched_process_exit")',
            "tracked_cgroups SEC(\".maps\")",
            "tracked_tasks SEC(\".maps\")",
            "process_tasks SEC(\".maps\")",
            "run_metrics SEC(\".maps\")",
            "process_at_user_task_peak = metrics->process_current",
            "thread_at_user_task_peak = metrics->thread_current",
        ):
            self.assertIn(token, source)

        makefile = (native_dir / "Makefile").read_text(encoding="utf-8")
        self.assertIn("task_tracker.skel.h", makefile)
        self.assertIn("codeguard-task-tracker", makefile)
        self.assertIn("codeguard-init", makefile)

    def test_bpf_derives_additional_threads_from_live_user_tasks(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        source = (native_dir / "task_tracker.bpf.c").read_text(
            encoding="utf-8"
        )

        self.assertIn(
            "metrics->user_task_current - metrics->process_current",
            source,
        )
        self.assertNotIn("metrics->thread_current += 1;", source)
        self.assertNotIn("metrics->thread_current -= 1;", source)

    def test_native_daemon_uses_interruptible_signal_handlers(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        source = (native_dir / "task_trackerd.c").read_text(
            encoding="utf-8"
        )

        self.assertIn("sigaction(SIGINT, &action, NULL)", source)
        self.assertIn("sigaction(SIGTERM, &action, NULL)", source)
        self.assertIn(".sa_flags = 0", source)
        self.assertIn(
            "pipe2(signal_pipe_fds, O_NONBLOCK | O_CLOEXEC)",
            source,
        )
        self.assertIn("write(signal_pipe_fds[1]", source)
        self.assertIn("poll(poll_fds, 2, -1)", source)
        self.assertIn("poll(client_poll_fds, 2, -1)", source)
        self.assertNotIn("signal(SIGINT, handle_signal)", source)
        self.assertNotIn("signal(SIGTERM, handle_signal)", source)

    def test_bpf_rejects_parent_task_from_another_run(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        source = (native_dir / "task_tracker.bpf.c").read_text(
            encoding="utf-8"
        )

        self.assertIn("static __always_inline bool same_run_id", source)
        self.assertIn(
            "same_run_id(&parent_value->run_id, &cgroup_value->run_id)",
            source,
        )
        self.assertIn(
            "same_run_id(&task_value->run_id, &cgroup_value->run_id)",
            source,
        )

    def test_native_cleanup_propagates_map_errors(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        source = (native_dir / "task_trackerd.c").read_text(
            encoding="utf-8"
        )

        self.assertIn("static void remember_errno", source)
        self.assertGreaterEqual(source.count("return first_error;"), 4)
        for call in (
            "result = remove_cgroups(",
            "result = remove_tasks(",
            "result = remove_processes(",
        ):
            self.assertIn(call, source)

    def test_bpf_process_reference_count_uses_verifier_safe_locking(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        header = (native_dir / "task_tracker_shared.h").read_text(
            encoding="utf-8"
        )
        source = (native_dir / "task_tracker.bpf.c").read_text(
            encoding="utf-8"
        )

        self.assertIn("struct bpf_spin_lock lock;", header)
        self.assertIn("bpf_spin_lock(&process_value->lock);", source)
        self.assertIn("bpf_spin_unlock(&process_value->lock);", source)
        self.assertNotIn("__sync_fetch_and_sub", source)

    def test_native_controller_exposes_all_protocol_operations(self) -> None:
        native_dir = (
            Path(__file__).resolve().parents[1] / "native" / "task_tracker"
        )
        source = (native_dir / "task_trackerd.c").read_text(
            encoding="utf-8"
        )
        for token in (
            "CG_OP_HEALTH",
            "CG_OP_REGISTER_CGROUP",
            "CG_OP_REGISTER_ROOT",
            "CG_OP_SNAPSHOT",
            "CG_OP_REMOVE",
            "task_tracker_bpf__open_and_load",
            "task_tracker_bpf__attach",
            "SOCK_SEQPACKET",
        ):
            self.assertIn(token, source)

        service = (
            native_dir / "codeguard-task-tracker.service"
        ).read_text(encoding="utf-8")
        self.assertIn("CAP_BPF", service)
        self.assertIn("CAP_PERFMON", service)
        self.assertIn("NoNewPrivileges=true", service)

    def test_codeguard_init_execs_without_signal_or_forking(self) -> None:
        source = (
            Path(__file__).resolve().parents[1]
            / "native"
            / "task_tracker"
            / "codeguard_init.c"
        ).read_text(encoding="utf-8")

        self.assertNotIn("sigwait", source)
        self.assertIn("execv", source)
        self.assertIn('strcmp(argv[index], "--stdin")', source)
        self.assertNotIn("fork(", source)

    def test_resolve_execution_cgroup_reads_id_and_current_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc_root = root / "proc"
            cgroup_mount = root / "cgroup"
            (proc_root / "321").mkdir(parents=True)
            cgroup_path = cgroup_mount / "docker" / "execution"
            cgroup_path.mkdir(parents=True)
            (proc_root / "321" / "cgroup").write_text(
                "0::/docker/execution\n",
                encoding="utf-8",
            )
            (cgroup_path / "pids.current").write_text(
                "6\n",
                encoding="utf-8",
            )

            result = resolve_execution_cgroup(
                321,
                proc_root=proc_root,
                cgroup_mount=cgroup_mount,
                cgroup_id_resolver=lambda path: path.stat().st_ino,
            )

            self.assertEqual(result.cgroup_id, cgroup_path.stat().st_ino)
            self.assertEqual(result.pids_current, 6)

    def test_resolve_execution_cgroup_rejects_missing_unified_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proc_root = root / "proc"
            (proc_root / "321").mkdir(parents=True)
            (proc_root / "321" / "cgroup").write_text(
                "2:cpu:/legacy\n",
                encoding="utf-8",
            )

            with self.assertRaises(TaskTrackingError):
                resolve_execution_cgroup(
                    321,
                    proc_root=proc_root,
                    cgroup_mount=root / "cgroup",
                )


if __name__ == "__main__":
    unittest.main()
