import threading
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import docker

from runner.config import settings
from runner.exceptions import ContainerExecutionError
from runner.models.result import RunnerReasonCode
from runner.pipeline.classifier import classify_execution
from runner.metrics.cgroup_scope import CgroupMetrics
from runner.pipeline.execution import (
    create_execution_container,
    execute_program,
)
from runner.pipeline.workspace import VolumeWorkspace
from runner.security.filesystem_trace import TRACE_DIRECTORY, TRACE_PATH, TRACE_SYSCALLS, RAW_WRITE_SYSCALLS


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        trace_patch = patch("runner.pipeline.execution.collect_filesystem_trace")
        self.trace_collector = trace_patch.start()
        self.addCleanup(trace_patch.stop)
        from runner.security.filesystem_trace import FilesystemViolation
        self.trace_collector.return_value = FilesystemViolation()
        self.client = MagicMock()
        self.container = MagicMock()
        self.client.containers.create.return_value = self.container
        self.container.wait.return_value = {"StatusCode": 0}
        self.container.attach.return_value = [(b"Hello\n", None)]
        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test",
        )
        self.run_id = uuid4()

    def test_create_execution_container_returns_registered_container(self) -> None:
        result = create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_limit=1.0,
            pids_limit=10,
        )

        self.assertIs(result, self.container)
        self.client.containers.create.assert_called_once_with(
            image=settings.cpp_image,
            command=["sh", "-c", f"umask 077; ulimit -f 2048; exec strace -f -q -yy -s 4096 -u codeguard -o {TRACE_PATH} -e trace={TRACE_SYSCALLS} -e raw={RAW_WRITE_SYSCALLS} /workspace/main"],
            mounts=[docker.types.Mount(target=TRACE_DIRECTORY, source="", type="volume")],
            read_only=True,
            volumes={
                self.workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "ro",
                }
            },
            detach=True,
            network_mode="none",
            user="0:0",
            cap_drop=["ALL"],
            cap_add=["SYS_PTRACE", "SETUID", "SETGID"],
            security_opt=["no-new-privileges=true"],
            mem_limit=128 * 1024 * 1024,
            memswap_limit=128 * 1024 * 1024,
            nano_cpus=1_000_000_000,
            pids_limit=10,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(self.workspace.job_id),
                "codeguard.run_id": str(self.run_id),
                "codeguard.stage": "execute",
            },
        )

    def test_create_execution_container_uses_stdin_file(self) -> None:
        create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin="21\n",
            job_id=self.workspace.job_id,
            run_id=self.run_id,
            memory_limit_mb=128,
            cpu_limit=1.0,
            pids_limit=10,
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(
            command,
            ["sh", "-c", f"umask 077; ulimit -f 2048; exec strace -f -q -yy -s 4096 -u codeguard -o {TRACE_PATH} -e trace={TRACE_SYSCALLS} -e raw={RAW_WRITE_SYSCALLS} /workspace/main < /workspace/stdin"],
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
            cpu_limit=1.0,
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
                cpu_limit=1.0,
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
        cgroup_scope.snapshot.assert_called_once_with()


    def test_trace_failure_is_system_error(self) -> None:
        self.trace_collector.side_effect = ValueError("trace corrupt")
        result = execute_program(self.container, self.workspace.job_id, self.run_id, timeout_ms=2000)
        self.assertIsNotNone(result.system_error)
        self.assertFalse(result.filesystem_limit_exceeded)

    def test_execution_connects_trace_evidence(self) -> None:
        from runner.security.filesystem_trace import FilesystemViolation
        self.trace_collector.return_value = FilesystemViolation(True, "unlink", "/workspace/main", "EROFS")
        result = execute_program(self.container, self.workspace.job_id, self.run_id, timeout_ms=2000)
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(result.filesystem_violation_syscall, "unlink")
        self.assertEqual(result.filesystem_violation_path, "/workspace/main")

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
                container.kill.side_effect = released.set
                with patch("runner.pipeline.execution.ResourceMonitor") as resource, patch(
                    "runner.pipeline.execution.PidsLimitMonitor",
                ) as pids:
                    resource.return_value.memory_peak_bytes = None
                    pids.return_value.exceeded.return_value = False
                    pids.return_value.pids_peak = None
                    result = execute_program(container, uuid4(), uuid4(), timeout_ms=5)

                container.kill.assert_called_once_with()
                self.assertIsNone(result.system_error)
                self.assertEqual(result.exit_code, exit_code)
                self.assertEqual(result.timed_out, exit_code == 137)
                self.assertFalse(result.filesystem_limit_exceeded)
                self.assertEqual(classify_execution(result).reason_code, expected_reason)


if __name__ == "__main__":
    unittest.main()
