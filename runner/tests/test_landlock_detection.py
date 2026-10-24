"""IPC and policy evidence contracts for optional Landlock detection."""
import unittest
from uuid import uuid4

from runner.exceptions import TaskTrackingError
from runner.metrics.task_tracker import (
    CG_OP_FILESYSTEM, CG_TRACKER_MAGIC, CG_TRACKER_VERSION,
    FILESYSTEM_RESPONSE_STRUCT, REQUEST_STRUCT, TaskTrackerClient,
)
from runner.security.filesystem_trace import analyze_filesystem_trace


class FilesystemTrackerProtocolTests(unittest.TestCase):
    def setUp(self):
        self.run_id = uuid4()
        self.fields = [CG_TRACKER_MAGIC, CG_TRACKER_VERSION, 3, 0, 0,
                       self.run_id.bytes, 987, 123456, 42, 100, 101, 4, 0]
        self.requests = []

    def query(self, raw=None):
        response = FILESYSTEM_RESPONSE_STRUCT.pack(*self.fields) if raw is None else raw
        owner = self
        class Transport:
            def request(self, payload, response_size):
                owner.requests.append(payload)
                owner.assertEqual(response_size, 72)
                return response
        return TaskTrackerClient(Transport()).filesystem_snapshot(self.run_id)

    def test_serializes_request_and_read_event(self):
        snapshot = self.query()
        request = REQUEST_STRUCT.unpack(self.requests[0])
        self.assertEqual(request[2], CG_OP_FILESYSTEM)
        self.assertEqual(request[3], self.run_id.bytes)
        self.assertEqual(request[4:], (0, 0, 0))
        event, = snapshot.violations
        self.assertTrue(snapshot.active)
        self.assertEqual(event.run_id, self.run_id)
        self.assertEqual((event.pid, event.tid, event.cgroup_id), (100, 101, 987))
        self.assertEqual((event.timestamp_ns, event.domain_id, event.blockers), (123456, 42, 4))
        self.assertEqual(event.source, "LANDLOCK_EBPF")

    def test_no_violation_returns_empty_active_snapshot(self):
        self.fields[2] = 1
        self.fields[6:] = [0] * 7
        self.assertEqual(self.query().violations, ())
        self.assertTrue(self.query().active)

    def test_unavailable_returns_empty_inactive_snapshot(self):
        self.fields[2] = 0
        self.fields[6:] = [0] * 7
        self.assertFalse(self.query().active)
        self.assertEqual(self.query().violations, ())

    def test_other_run_rejected(self):
        self.fields[5] = uuid4().bytes
        with self.assertRaises(TaskTrackingError):
            self.query()

    def test_invalid_headers_and_event_rejected(self):
        original = list(self.fields)
        for index, value in ((0, 0), (1, 999), (2, 7), (2, 2), (3, -5),
                             (4, 1), (6, 0), (7, 0), (9, 0), (10, 0), (11, 0), (12, 1)):
            with self.subTest(index=index, value=value):
                self.fields = list(original)
                self.fields[index] = value
                with self.assertRaises(TaskTrackingError):
                    self.query()

    def test_empty_flags_with_event_bytes_rejected(self):
        self.fields[2] = 1
        with self.assertRaises(TaskTrackingError):
            self.query()

    def test_legacy_daemon_response_rejected_for_runner_fallback(self):
        with self.assertRaises(TaskTrackingError):
            self.query(bytes(48))

    def test_oversized_response_rejected(self):
        with self.assertRaises(TaskTrackingError):
            self.query(bytes(73))


class FilesystemTraceFallbackTests(unittest.TestCase):
    def test_active_detector_suppresses_eacces_inference(self):
        trace = 'openat(AT_FDCWD, "/dev/shm/test", O_WRONLY|O_CREAT, 0600) = -1 EACCES (Permission denied)'
        self.assertTrue(analyze_filesystem_trace(trace).detected)
        self.assertFalse(analyze_filesystem_trace(trace, landlock_fallback=False).detected)

    def test_erofs_preserved_with_active_detector(self):
        trace = 'openat(AT_FDCWD, "/tmp/test", O_WRONLY|O_CREAT, 0600) = -1 EROFS (Read-only file system)'
        self.assertTrue(analyze_filesystem_trace(trace, landlock_fallback=False).detected)

    def test_read_eacces_keeps_legacy_compatibility(self):
        trace = 'openat(AT_FDCWD, "/etc/hosts", O_RDONLY) = -1 EACCES (Permission denied)'
        self.assertFalse(analyze_filesystem_trace(trace).detected)

    def test_active_detector_does_not_bypass_trace_integrity(self):
        trace = 'seccomp(SECCOMP_SET_MODE_FILTER, 0, {len=1}) = 0'
        with self.assertRaises(ValueError):
            analyze_filesystem_trace(trace, landlock_fallback=False)
