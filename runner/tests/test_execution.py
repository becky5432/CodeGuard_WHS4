import threading
import unittest
from unittest.mock import MagicMock, call, patch
from uuid import uuid4

import docker

from runner.config import settings
from runner.exceptions import ContainerExecutionError, TaskTrackingError
from runner.models.result import RunnerReasonCode, RunnerStatus
from runner.pipeline.classifier import classify_execution
from runner.metrics.cgroup_scope import CgroupMetrics
from runner.metrics.task_tracker import (
    ExecutionCgroupIdentity,
    PidsPeakSnapshot,
)
from runner.pipeline.execution import (
    create_execution_container,
    execute_program,
)
from runner.pipeline.workspace import VolumeWorkspace
from runner.security.filesystem_trace import (
    RAW_WRITE_SYSCALLS,
    TRACE_DIRECTORY,
    TRACE_PATH,
    TRACE_SYSCALLS,
    FilesystemViolation,
)


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        trace_patch = patch("runner.pipeline.execution.collect_filesystem_trace")
        self.trace_collector = trace_patch.start()
        self.addCleanup(trace_patch.stop)
        from runner.security.filesystem_trace import FilesystemViolation
        self.trace_collector.return_value = FilesystemViolation()
        security_patch = patch(
            "runner.pipeline.execution.collect_runtime_permission_failure",
            return_value=False,
        )
        self.security_collector = security_patch.start()
        self.addCleanup(security_patch.stop)
        self.client = MagicMock()
        self.container = MagicMock()
        self.client.containers.create.return_value = self.container
        self.container.wait.return_value = {"StatusCode": 0}
        self.container.attach.return_value = [(b"Hello\n", None)]
        self.container.attrs = {
            "Config": {"User": "0:0"},
            "HostConfig": {
                "CapDrop": ["ALL"],
                "CapAdd": ["SYS_PTRACE", "SETUID", "SETGID"],
                "SecurityOpt": ["no-new-privileges=true"],
            },
            "Mounts": [{"Destination": "/workspace", "RW": False}],
            "State": {"OOMKilled": False},
        }
        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )
        self.run_id = uuid4()
        self.affinity_patcher = patch(
            "runner.pipeline.execution.os.sched_getaffinity",
            return_value={0, 1},
        )
        self.mock_sched_getaffinity = self.affinity_patcher.start()
        self.addCleanup(self.affinity_patcher.stop)

        

    def test_create_execution_container_returns_registered_container(self) -> None:
        result = create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_bandwidth=1.0,
            pids_limit=10,
        )

        self.assertIs(result, self.container)
        self.client.containers.create.assert_called_once_with(
            image=settings.cpp_image,
            command=["sh", "-c", f"umask 077; set -C; exec 3>/run/codeguard-trace/security.status; ulimit -f 2048; exec strace -f -q -yy -s 4096 -u codeguard -o {TRACE_PATH} -e trace={TRACE_SYSCALLS} -e raw={RAW_WRITE_SYSCALLS} /usr/local/bin/codeguard-init --security-fd 3 -- /workspace/main"],
            mounts=[docker.types.Mount(target=TRACE_DIRECTORY, source="", type="volume")],
            volumes={
                self.workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "ro",
                }
            },
            detach=True,
            read_only=True,
            network_mode="none",
            user="0:0",
            cap_drop=["ALL"],
            cap_add=["SYS_PTRACE", "SETUID", "SETGID"],
            security_opt=["no-new-privileges=true"],
            mem_limit=128 * 1024 * 1024,
            memswap_limit=128 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            cpuset_cpus="0,1",
            pids_limit=10,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(self.workspace.job_id),
                "codeguard.run_id": str(self.run_id),
                "codeguard.stage": "execute",
            },
        )

    def test_create_execution_container_applies_internal_logical_cpu_limit(self) -> None:
        create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_bandwidth=1.0,
            pids_limit=10,
        )

        cpuset_cpus = self.client.containers.create.call_args.kwargs[
            "cpuset_cpus"
        ]
        self.assertEqual(cpuset_cpus, "0,1")

    def test_create_execution_container_uses_available_cpus_below_internal_limit(
        self,
    ) -> None:
        self.mock_sched_getaffinity.return_value = {0}

        create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_bandwidth=1.0,
            pids_limit=10,
        )

        cpuset_cpus = self.client.containers.create.call_args.kwargs[
            "cpuset_cpus"
        ]
        self.assertEqual(cpuset_cpus, "0")


    def test_create_execution_container_uses_stdin_file(self) -> None:
        create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="21\n",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_bandwidth=1.0,
            pids_limit=10,
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(
            command,
            ["sh", "-c", f"umask 077; set -C; exec 3>/run/codeguard-trace/security.status; ulimit -f 2048; exec strace -f -q -yy -s 4096 -u codeguard -o {TRACE_PATH} -e trace={TRACE_SYSCALLS} -e raw={RAW_WRITE_SYSCALLS} /usr/local/bin/codeguard-init --security-fd 3 --stdin /workspace/stdin -- /workspace/main"],
        )

    def test_create_execution_container_uses_cgroup_parent(self) -> None:
        cgroup_scope = MagicMock()
        cgroup_scope.docker_parent = "/codeguard/execution-test"

        create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_bandwidth=1.0,
            pids_limit=10,
            cgroup_scope=cgroup_scope,
        )

        self.assertEqual(
            self.client.containers.create.call_args.kwargs["cgroup_parent"],
            "/codeguard/execution-test",
        )

    def test_execute_program_collects_result_without_removing_container(self) -> None:
        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "Hello\n")
        self.assertEqual(result.stderr, "")
        self.container.start.assert_called_once_with()
        self.container.kill.assert_not_called()
        self.container.wait.assert_called_once_with()
        self.container.remove.assert_not_called()

    def test_create_execution_container_wraps_docker_failure(self) -> None:
        self.client.containers.create.side_effect = docker.errors.APIError(
            "create failed",
        )

        with self.assertRaises(ContainerExecutionError):
            create_execution_container(
                client=self.client,
                workspace=self.workspace,
                stdin="",
                job_id=self.workspace.job_id,
                run_id=self.run_id,
                memory_limit_mb=128,
                cpu_bandwidth=1.0,
                pids_limit=10,
            )

    def test_execute_program_returns_system_error_without_removing_container(self) -> None:
        self.container.start.side_effect = docker.errors.APIError("start failed")

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
        )

        self.assertIsNotNone(result.system_error)
        self.assertIsNone(result.exit_code)
        self.container.remove.assert_not_called()

    @patch("runner.pipeline.execution.PidsLimitMonitor")
    @patch("runner.pipeline.execution.ResourceMonitor")
    def test_execute_program_prefers_parent_cgroup_peak_values(
        self,
        resource_monitor_class,
        pids_monitor_class,
    ) -> None:
        resource_monitor_class.return_value.memory_peak_bytes = 100
        pids_monitor_class.return_value.pids_peak = 2
        pids_monitor_class.return_value.exceeded.return_value = False
        cgroup_scope = MagicMock()
        cgroup_scope.read_cpu_usage_usec.return_value = None
        cgroup_scope.snapshot.return_value = CgroupMetrics(
            memory_peak_bytes=16 * 1024 * 1024,
            pids_peak=18,
        )

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
        )

        self.assertEqual(result.memory_peak_bytes, 16 * 1024 * 1024)
        self.assertEqual(result.pids_peak, 18)
        cgroup_scope.read_cpu_usage_usec.assert_called_once_with()
        cgroup_scope.snapshot.assert_called_once_with()

    def test_execute_program_calculates_cpu_time_from_cgroup_usage(self) -> None:
        cgroup_scope = MagicMock()
        cgroup_scope.read_cpu_usage_usec.return_value = 10_000
        cgroup_scope.snapshot.return_value = CgroupMetrics(
            cpu_time_usec=85_000,
        )

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
        )

        self.assertEqual(result.cpu_time_ms, 75)
        cgroup_scope.read_cpu_usage_usec.assert_called_once_with()
        cgroup_scope.snapshot.assert_called_once_with()

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_execute_program_returns_user_task_peak_snapshot(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {"Memory": 128 * 1024 * 1024},
        }
        resolve_cgroup.return_value = ExecutionCgroupIdentity(
            cgroup_id=999,
            pids_current=1,
        )
        tracker = MagicMock()
        tracker.snapshot.return_value = PidsPeakSnapshot(
            user_task_peak=15,
            process_at_user_task_peak=3,
            thread_at_user_task_peak=12,
        )
        cgroup_scope = MagicMock()
        cgroup_scope.snapshot.return_value = CgroupMetrics(pids_peak=18)

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
            task_tracker=tracker,
        )

        tracker.register_cgroup.assert_called_once_with(self.run_id, 999, 1)
        tracker.register_root.assert_called_once_with(self.run_id, 321)
        tracker.snapshot.assert_called_once_with(self.run_id)
        tracker.remove.assert_called_once_with(self.run_id)
        self.container.kill.assert_not_called()
        self.assertEqual(result.pids_peak, 18)
        self.assertEqual(result.user_task_peak, 15)
        self.assertEqual(result.process_at_user_task_peak, 3)
        self.assertEqual(result.thread_at_user_task_peak, 12)

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_execute_program_keeps_user_snapshot_when_cgroup_peak_differs(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {},
        }
        resolve_cgroup.return_value = ExecutionCgroupIdentity(999, 1)
        tracker = MagicMock()
        tracker.snapshot.return_value = PidsPeakSnapshot(
            user_task_peak=1,
            process_at_user_task_peak=1,
            thread_at_user_task_peak=0,
        )
        cgroup_scope = MagicMock()
        cgroup_scope.snapshot.return_value = CgroupMetrics(pids_peak=18)

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
            task_tracker=tracker,
        )

        self.assertEqual(result.pids_peak, 18)
        self.assertEqual(result.user_task_peak, 1)
        self.assertEqual(result.process_at_user_task_peak, 1)
        self.assertEqual(result.thread_at_user_task_peak, 0)
        tracker.remove.assert_called_once_with(self.run_id)

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_tracker_registration_failure_does_not_block_execution(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {},
        }
        resolve_cgroup.return_value = ExecutionCgroupIdentity(999, 1)
        tracker = MagicMock()
        tracker.register_cgroup.side_effect = TaskTrackingError("offline")

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            task_tracker=tracker,
        )

        self.assertEqual(result.exit_code, 0)
        self.container.kill.assert_not_called()
        tracker.register_root.assert_not_called()
        tracker.snapshot.assert_not_called()
        tracker.remove.assert_called_once_with(self.run_id)

    @patch("runner.pipeline.execution.resolve_execution_cgroup")
    def test_tracker_pid_reload_failure_does_not_block_execution(
        self,
        resolve_cgroup,
    ) -> None:
        self.container.reload.side_effect = [
            docker.errors.APIError("tracking reload failed"),
            None,
            None,
        ]
        self.container.attrs = {
            "State": {"Pid": 321, "OOMKilled": False},
            "HostConfig": {},
        }
        tracker = MagicMock()

        result = execute_program(
            container=self.container,
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            timeout_ms=2000,
            task_tracker=tracker,
        )

        self.assertEqual(result.exit_code, 0)
        self.container.kill.assert_not_called()
        resolve_cgroup.assert_not_called()
        tracker.register_cgroup.assert_not_called()
        tracker.remove.assert_called_once_with(self.run_id)

    def test_trace_failure_is_system_error(self) -> None:
        self.trace_collector.side_effect = ValueError("trace corrupt")
        result = execute_program(
            self.container,
            self.workspace.job_id,
            self.run_id,
            timeout_ms=2000,
        )
        self.assertIsNotNone(result.system_error)
        self.assertFalse(result.filesystem_limit_exceeded)

    def test_execution_connects_trace_evidence(self) -> None:
        from runner.security.filesystem_trace import FilesystemViolation

        self.trace_collector.return_value = FilesystemViolation(
            True,
            "unlink",
            "/workspace/main",
            "EROFS",
        )
        result = execute_program(
            self.container,
            self.workspace.job_id,
            self.run_id,
            timeout_ms=2000,
        )
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(result.filesystem_violation_syscall, "unlink")
        self.assertEqual(result.filesystem_violation_path, "/workspace/main")

    @patch("runner.pipeline.execution.CpuUsageSampler")
    @patch("runner.pipeline.execution.PidsLimitMonitor")
    @patch("runner.pipeline.execution.ResourceMonitor")
    def test_cpu_time_limit_kills_container_and_sets_evidence(
        self,
        resource_monitor_class,
        pids_monitor_class,
        cpu_sampler_class,
    ) -> None:
        released = threading.Event()
        container = MagicMock()
        container.attrs = {"State": {"OOMKilled": False}}
        container.attach.return_value = []

        def wait_for_exit():
            if not released.wait(timeout=1):
                raise RuntimeError("cpu time kill did not release wait")
            return {"StatusCode": 137}

        def kill(*args, **kwargs):
            if kwargs.get("signal") == "SIGUSR1":
                return
            released.set()

        container.wait.side_effect = wait_for_exit
        container.kill.side_effect = kill

        resource_monitor_class.return_value.memory_peak_bytes = None
        pids_monitor_class.return_value.exceeded.return_value = False
        pids_monitor_class.return_value.pids_peak = None

        cgroup_scope = MagicMock()
        cgroup_scope.read_cpu_usage_usec.side_effect = [
            10_000,
            10_000,
            111_000,
        ]
        cgroup_scope.snapshot.return_value = CgroupMetrics(
            cpu_time_usec=111_000,
        )

        result = execute_program(
            container=container,
            job_id=uuid4(),
            run_id=uuid4(),
            timeout_ms=2000,
            cgroup_scope=cgroup_scope,
            cpu_time_limit_ms=100,
        )

        self.assertEqual(result.exit_code, 137)
        self.assertTrue(result.cpu_time_limit_exceeded)
        self.assertFalse(result.timed_out)
        self.assertEqual(
            container.kill.call_args_list,
            [
                call(signal="SIGUSR1"),
                call(),
            ],
        )

    def test_timeout_kill_race_preserves_natural_exit(self) -> None:
        for exit_code, expected_reason in (
            (139, RunnerReasonCode.RUNTIME_ERROR),
            (0, None),
            (137, RunnerReasonCode.TIME_LIMIT),
        ):
            with self.subTest(exit_code=exit_code):
                released = threading.Event()
                container = MagicMock()
                container.attrs = {"State": {"OOMKilled": False}}
                container.attach.return_value = []

                def wait_for_exit():
                    if not released.wait(timeout=1):
                        raise RuntimeError("timeout kill did not release wait")
                    return {"StatusCode": exit_code}

                container.wait.side_effect = wait_for_exit
                def kill(*args, **kwargs):
                    released.set()

                container.kill.side_effect = kill
                with patch(
                    "runner.pipeline.execution.ResourceMonitor",
                ) as resource, patch(
                    "runner.pipeline.execution.PidsLimitMonitor",
                ) as pids:
                    resource.return_value.memory_peak_bytes = None
                    pids.return_value.exceeded.return_value = False
                    pids.return_value.pids_peak = None
                    result = execute_program(
                        container,
                        uuid4(),
                        uuid4(),
                        timeout_ms=5,
                    )

                self.assertEqual(
                    container.kill.call_args_list,
                    [call()],
                )
                self.assertIsNone(result.system_error)
                self.assertEqual(result.exit_code, exit_code)
                self.assertEqual(result.timed_out, exit_code == 137)
                self.assertFalse(result.filesystem_limit_exceeded)
                self.assertEqual(
                    classify_execution(result).reason_code,
                    expected_reason,
                )


class ExecutionTimeoutRaceTests(unittest.TestCase):
    def setUp(self) -> None:
        trace_patch = patch(
            "runner.pipeline.execution.collect_filesystem_trace",
            return_value=FilesystemViolation(),
        )
        trace_patch.start()
        self.addCleanup(trace_patch.stop)
        security_patch = patch(
            "runner.pipeline.execution.collect_runtime_permission_failure",
            return_value=False,
        )
        security_patch.start()
        self.addCleanup(security_patch.stop)

    def run_execution(
        self,
        exit_code,
        *,
        timeout_reached=False,
        finished_at=0.1,
        kill_error=None,
        oom_killed=False,
        cgroup_metrics=None,
        output=None,
        pids_exceeded=False,
    ):
        """Use real worker threads and barriers, without timing-sensitive sleeps."""
        container = MagicMock()
        container.attrs = {"State": {"OOMKilled": oom_killed}}
        container.attach.return_value = output or [(b"ok", None)]
        release_wait = threading.Event()
        clock = [0.0]
        workers = {}
        real_thread = threading.Thread

        def make_thread(*args, **kwargs):
            worker = real_thread(*args, **kwargs)
            workers[kwargs["name"]] = worker
            return worker

        def wait_for_exit():
            if not release_wait.wait(timeout=2.0):
                raise AssertionError("test did not release container.wait()")
            return {"StatusCode": exit_code}

        def finish_wait():
            clock[0] = finished_at
            release_wait.set()
            worker = workers["runner-container-wait"]
            worker.join(timeout=1.0)
            self.assertFalse(worker.is_alive())

        def start_monitor():
            workers["runner-output-monitor"].join(timeout=1.0)
            if timeout_reached:
                clock[0] = 1.001
            elif output is None and not pids_exceeded:
                finish_wait()

        def kill(*args, **kwargs):
            finish_wait()
            if kill_error is not None:
                raise kill_error

        container.wait.side_effect = wait_for_exit
        container.kill.side_effect = kill
        cgroup_scope = None
        if cgroup_metrics is not None:
            cgroup_scope = MagicMock()
            cgroup_scope.read_cpu_usage_usec.return_value = None
            cgroup_scope.snapshot.return_value = cgroup_metrics

        with (
            patch("runner.pipeline.execution.ResourceMonitor") as resource_class,
            patch("runner.pipeline.execution.PidsLimitMonitor") as pids_class,
            patch("runner.pipeline.execution.threading.Thread", side_effect=make_thread),
            patch("runner.pipeline.execution.time.monotonic", side_effect=lambda: clock[0]),
        ):
            resource_class.return_value.start.side_effect = start_monitor
            resource_class.return_value.memory_peak_bytes = None
            pids_class.return_value.exceeded.return_value = pids_exceeded
            pids_class.return_value.pids_peak = None
            try:
                result = execute_program(
                    container, uuid4(), uuid4(), timeout_ms=1000,
                    output_limit_bytes=16, cgroup_scope=cgroup_scope,
                )
            finally:
                release_wait.set()
                for worker in workers.values():
                    worker.join(timeout=1.0)
        self.assertIsNone(result.system_error)
        self.assertEqual(result.exit_code, exit_code)
        return result, container

    def assert_classification(self, result, reason, status):
        classification = classify_execution(result)
        self.assertEqual(classification.reason_code, reason)
        self.assertEqual(classification.status, status)
        self.assertFalse(
            result.exit_code == 139
            and classification.reason_code == RunnerReasonCode.TIME_LIMIT,
        )

    def assert_start_only(self, container):
        container.kill.assert_not_called()

    def assert_policy_kill(self, container):
        self.assertEqual(
            container.kill.call_args_list,
            [call()],
        )

    def test_sigsegv_before_timeout_is_runtime_error(self):
        result, container = self.run_execution(139)
        self.assertFalse(result.timed_out)
        self.assert_start_only(container)
        self.assert_classification(result, RunnerReasonCode.RUNTIME_ERROR, RunnerStatus.ERROR)

    def test_timeout_kill_exit_137_is_time_limit(self):
        result, container = self.run_execution(137, timeout_reached=True, finished_at=1.1)
        self.assertTrue(result.timed_out)
        self.assert_policy_kill(container)
        self.assert_classification(result, RunnerReasonCode.TIME_LIMIT, RunnerStatus.BLOCKED)

    def test_sigsegv_wins_race_with_successful_timeout_kill_request(self):
        result, container = self.run_execution(139, timeout_reached=True, finished_at=1.1)
        self.assertFalse(result.timed_out)
        self.assert_policy_kill(container)
        self.assert_classification(result, RunnerReasonCode.RUNTIME_ERROR, RunnerStatus.ERROR)

    def test_timeout_kill_of_already_stopped_sigsegv_is_runtime_error(self):
        from requests import Response

        response = Response()
        response.status_code = 409
        error = docker.errors.APIError(
            "409 Conflict", response=response, explanation="container already stopped",
        )
        result, container = self.run_execution(
            139, timeout_reached=True, finished_at=1.1, kill_error=error,
        )
        self.assertFalse(result.timed_out)
        self.assert_policy_kill(container)
        self.assert_classification(result, RunnerReasonCode.RUNTIME_ERROR, RunnerStatus.ERROR)

    def test_delayed_sigsegv_result_exceeding_wall_timeout_is_runtime_error(self):
        result, container = self.run_execution(139, finished_at=1.5)
        self.assertGreater(result.wall_time_ms, 1000)
        self.assertFalse(result.timed_out)
        self.assert_start_only(container)
        self.assert_classification(result, RunnerReasonCode.RUNTIME_ERROR, RunnerStatus.ERROR)

    def test_delayed_success_result_exceeding_wall_timeout_is_success(self):
        result, container = self.run_execution(0, finished_at=1.5)
        self.assertGreater(result.wall_time_ms, 1000)
        self.assertFalse(result.timed_out)
        self.assert_start_only(container)
        self.assert_classification(result, None, RunnerStatus.SUCCESS)

    def test_exit_137_without_timeout_kill_is_runtime_error(self):
        result, container = self.run_execution(137, finished_at=1.5)
        self.assertFalse(result.timed_out)
        self.assert_start_only(container)
        self.assert_classification(result, RunnerReasonCode.RUNTIME_ERROR, RunnerStatus.ERROR)

    def test_failed_timeout_kill_does_not_claim_exit_137(self):
        result, container = self.run_execution(
            137, timeout_reached=True, finished_at=1.1,
            kill_error=docker.errors.APIError("kill failed"),
        )
        self.assertFalse(result.timed_out)
        self.assert_policy_kill(container)
        self.assert_classification(result, RunnerReasonCode.RUNTIME_ERROR, RunnerStatus.ERROR)

    def test_oom_evidence_keeps_priority_over_timeout(self):
        for evidence in (
            {"oom_killed": True},
            {"cgroup_metrics": CgroupMetrics(oom_killed=True)},
        ):
            with self.subTest(evidence=evidence):
                result, _ = self.run_execution(
                    137, timeout_reached=True, finished_at=1.1, **evidence,
                )
                self.assertTrue(result.timed_out)
                self.assert_classification(result, RunnerReasonCode.MEMORY_LIMIT, RunnerStatus.BLOCKED)

    def test_cgroup_pids_evidence_keeps_priority_over_timeout(self):
        result, _ = self.run_execution(
            137, timeout_reached=True, finished_at=1.1,
            cgroup_metrics=CgroupMetrics(pids_limit_exceeded=True),
        )
        self.assertTrue(result.timed_out)
        self.assert_classification(result, RunnerReasonCode.PIDS_LIMIT, RunnerStatus.BLOCKED)

    def test_output_kill_exit_137_is_output_limit(self):
        result, container = self.run_execution(137, output=[(b"x" * 17, None)])
        self.assertFalse(result.timed_out)
        self.assert_policy_kill(container)
        self.assertTrue(result.output_limit_exceeded)
        self.assert_classification(result, RunnerReasonCode.OUTPUT_LIMIT, RunnerStatus.BLOCKED)

    def test_pids_kill_exit_137_is_pids_limit(self):
        result, container = self.run_execution(137, pids_exceeded=True)
        self.assertFalse(result.timed_out)
        self.assert_policy_kill(container)
        self.assertTrue(result.pids_limit_exceeded)
        self.assert_classification(result, RunnerReasonCode.PIDS_LIMIT, RunnerStatus.BLOCKED)
if __name__ == "__main__":
    unittest.main()
