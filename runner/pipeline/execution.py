import logging
import threading
import time
from dataclasses import dataclass
from uuid import UUID

import docker

from runner.config import settings
from runner.exceptions import ContainerExecutionError
from runner.metrics.cgroup_scope import CgroupMetrics, ExecutionCgroupScope
from runner.metrics.resource_monitor import ResourceMonitor
from runner.metrics.pids_monitor import PidsLimitMonitor
from runner.pipeline.workspace import VolumeWorkspace
from runner.policies import EXECUTION_OUTPUT_LIMIT_BYTES


logger = logging.getLogger("runner")


@dataclass
class ExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    system_error: str | None = None
    timed_out: bool = False
    output_limit_exceeded: bool = False
    oom_killed: bool = False
    wall_time_ms: int | None = None
    memory_peak_bytes: int | None = None
    pids_peak: int | None = None
    pids_limit_exceeded: bool = False


class _BoundedOutput:
    def __init__(self, limit_bytes: int) -> None:
        self.limit_bytes = limit_bytes
        self.stdout = bytearray()
        self.stderr = bytearray()
        self.exceeded = threading.Event()
        self._lock = threading.Lock()

    def append(self, stream: str, data: bytes | None) -> None:
        if not data:
            return

        with self._lock:
            current_size = len(self.stdout) + len(self.stderr)
            remaining = max(self.limit_bytes - current_size, 0)
            target = self.stdout if stream == "stdout" else self.stderr
            target.extend(data[:remaining])
            if len(data) > remaining:
                self.exceeded.set()

    def decode(self) -> tuple[str, str]:
        return (
            self.stdout.decode("utf-8", errors="replace"),
            self.stderr.decode("utf-8", errors="replace"),
        )


def _close_stream(stream) -> None:
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            close()
        except Exception as exc:
            logger.warning("event=execution_stream_close_error error=%s", exc)


def _stop_output_thread(frames, output_thread) -> bool:
    if output_thread is None:
        _close_stream(frames)
        return True

    output_thread.join(timeout=1.0)
    _close_stream(frames)
    if output_thread.is_alive():
        output_thread.join(timeout=1.0)
    return not output_thread.is_alive()


def create_execution_container(
    client,
    workspace: VolumeWorkspace,
    stdin: str,
    job_id: UUID,
    run_id: UUID,
    memory_limit_mb: int,
    cpu_limit: float,
    pids_limit: int,
    cgroup_scope: ExecutionCgroupScope | None = None,
):
    """Job Volume을 연결한 실행 컨테이너를 생성하고 반환한다."""

    command = ["/workspace/main"]
    if stdin:
        command = [
            "sh",
            "-c",
            "exec /workspace/main < /workspace/stdin",
        ]

    memory_limit_bytes = memory_limit_mb * 1024 * 1024
    nano_cpus_limit = int(cpu_limit * 1_000_000_000)

    container_options = {
        "image": settings.cpp_image,
        "command": command,
        "volumes": {
            workspace.volume_name: {
                "bind": "/workspace",
                "mode": "ro",
            }
        },
        "detach": True,
        "mem_limit": memory_limit_bytes,
        "memswap_limit": memory_limit_bytes,
        "nano_cpus": nano_cpus_limit,
        "pids_limit": pids_limit,
        "labels": {
            "codeguard.managed": "true",
            "codeguard.job_id": str(job_id),
            "codeguard.run_id": str(run_id),
            "codeguard.stage": "execute",
        },
    }
    if cgroup_scope is not None:
        container_options["cgroup_parent"] = cgroup_scope.docker_parent

    try:
        return client.containers.create(
            **container_options,
        )
    except docker.errors.DockerException as exc:
        raise ContainerExecutionError(
            "실행 컨테이너 생성에 실패했습니다.",
            details={"reason": str(exc)},
        ) from exc


def _collect_output(frames, output: _BoundedOutput) -> Exception | None:
    try:
        for frame in frames:
            if isinstance(frame, tuple):
                stdout, stderr = frame
                output.append("stdout", stdout)
                output.append("stderr", stderr)
            else:
                output.append("stdout", frame)
    except Exception as exc:
        return exc
    return None


def execute_program(
    container,
    job_id: UUID,
    run_id: UUID,
    timeout_ms: int,
    output_limit_bytes: int = EXECUTION_OUTPUT_LIMIT_BYTES,
    cgroup_scope: ExecutionCgroupScope | None = None,
) -> ExecutionResult:
    """제한을 감시하며 실행 컨테이너의 종료 정보와 출력을 수집한다."""

    start = time.monotonic()
    output = _BoundedOutput(output_limit_bytes)
    monitor = ResourceMonitor(container)
    pids_monitor = PidsLimitMonitor(container)
    wait_done = threading.Event()
    wait_state: dict[str, object] = {}
    output_state: dict[str, Exception | None] = {}
    timed_out = False
    pids_limit_exceeded = False
    system_error = None
    output_thread = None
    monitor_started = False
    output_thread_stopped = True

    def wait_for_container() -> None:
        try:
            wait_state["result"] = container.wait()
        except Exception as exc:
            wait_state["error"] = exc
        finally:
            wait_state["finished_at"] = time.monotonic()
            wait_done.set()

    def collect_output() -> None:
        output_state["error"] = _collect_output(frames, output)

    try:
        # 빠르게 종료되는 프로그램의 출력도 놓치지 않도록 start 전에
        # attach 연결을 준비한다.
        frames = container.attach(
            stdout=True,
            stderr=True,
            stream=True,
            logs=True,
            demux=True,
        )
        try:
            start = time.monotonic()
            container.start()

            pids_monitor.start()

            thread = threading.Thread(
                target=collect_output,
                name="runner-output-monitor",
                daemon=True,
            )
            thread.start()
            output_thread = thread

            wait_thread = threading.Thread(
                target=wait_for_container,
                name="runner-container-wait",
                daemon=True,
            )
            wait_thread.start()

            monitor_started = True
            monitor.start()

            timeout_seconds = timeout_ms / 1000
            while not wait_done.is_set():

                if output.exceeded.is_set():
                    break

                if pids_monitor.exceeded():
                    pids_limit_exceeded = True
                    break

                remaining = timeout_seconds - (time.monotonic() - start)
                if remaining <= 0:
                    timed_out = True
                    break
                wait_done.wait(timeout=min(remaining, 0.01))

            finished_at = wait_state.get("finished_at")
            if (
                not timed_out
                and isinstance(finished_at, float)
                and finished_at - start > timeout_seconds
            ):
                timed_out = True

            policy_kill = timed_out or output.exceeded.is_set() or pids_limit_exceeded
            if policy_kill and not wait_done.is_set():
                try:
                    container.kill()
                except docker.errors.DockerException as exc:
                    logger.warning(
                        "event=execution_container_kill_error "
                        "job_id=%s run_id=%s error=%s",
                        job_id,
                        run_id,
                        exc,
                    )

                if not wait_done.wait(timeout=2.0):
                    system_error = "실행 컨테이너를 종료하지 못했습니다."

            wait_done.wait(timeout=1.0)
        finally:
            output_thread_stopped = _stop_output_thread(
                frames,
                output_thread,
            )
            if monitor_started:
                try:
                    monitor.stop()
                except Exception as exc:
                    logger.warning(
                        "event=resource_monitor_cleanup_error "
                        "job_id=%s run_id=%s error=%s",
                        job_id,
                        run_id,
                        exc,
                    )
            pids_monitor.sample()

        final_policy_kill = timed_out or output.exceeded.is_set() or pids_limit_exceeded
        if not output_thread_stopped:
            system_error = "실행 출력 수집기를 종료하지 못했습니다."
            logger.error(
                "event=execution_output_thread_stop_timeout "
                "job_id=%s run_id=%s",
                job_id,
                run_id,
            )

        wait_error = wait_state.get("error")
        if wait_error is not None:
            system_error = "실행 컨테이너 처리에 실패했습니다."
            logger.error(
                "event=execution_container_wait_error "
                "job_id=%s run_id=%s error=%s",
                job_id,
                run_id,
                wait_error,
            )

        output_error = output_state.get("error")
        if output_error is not None and not final_policy_kill:
            system_error = "실행 출력 수집에 실패했습니다."
            logger.error(
                "event=execution_output_error "
                "job_id=%s run_id=%s error=%s",
                job_id,
                run_id,
                output_error,
            )

        exit_code = None
        wait_result = wait_state.get("result")
        if isinstance(wait_result, dict):
            exit_code = int(wait_result["StatusCode"])

        oom_killed = False
        try:
            container.reload()
            oom_killed = (
                container.attrs.get("State", {}).get("OOMKilled", False)
                is True
            )
        except docker.errors.DockerException as exc:
            system_error = "실행 컨테이너 상태 확인에 실패했습니다."
            logger.error(
                "event=execution_container_reload_error "
                "job_id=%s run_id=%s error=%s",
                job_id,
                run_id,
                exc,
            )

        cgroup_metrics = CgroupMetrics()
        if cgroup_scope is not None:
            try:
                cgroup_metrics = cgroup_scope.snapshot()
            except Exception as exc:
                logger.warning(
                    "event=execution_cgroup_snapshot_error "
                    "job_id=%s run_id=%s error=%s",
                    job_id,
                    run_id,
                    exc,
                )

        memory_peak_bytes = (
            cgroup_metrics.memory_peak_bytes
            if cgroup_metrics.memory_peak_bytes is not None
            else monitor.memory_peak_bytes
        )
        pids_peak = (
            cgroup_metrics.pids_peak
            if cgroup_metrics.pids_peak is not None
            else pids_monitor.pids_peak
        )
        oom_killed = oom_killed or cgroup_metrics.oom_killed
        pids_limit_exceeded = (
            pids_limit_exceeded
            or cgroup_metrics.pids_limit_exceeded
        )
        if oom_killed:
            memory_limit_bytes = (
                container.attrs.get("HostConfig", {}).get("Memory")
            )
            if (
                isinstance(memory_limit_bytes, int)
                and memory_limit_bytes > 0
                and (
                    memory_peak_bytes is None
                    or memory_peak_bytes < memory_limit_bytes
                )
            ):
                memory_peak_bytes = memory_limit_bytes

        if output_thread_stopped:
            stdout, stderr = output.decode()
        else:
            stdout, stderr = "", ""
        finished_at = wait_state.get("finished_at")
        if not isinstance(finished_at, float):
            finished_at = time.monotonic()
        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            system_error=system_error,
            timed_out=timed_out,
            output_limit_exceeded=output.exceeded.is_set(),
            oom_killed=oom_killed,
            wall_time_ms=int((finished_at - start) * 1000),
            memory_peak_bytes=memory_peak_bytes,
            pids_peak=pids_peak,
            pids_limit_exceeded=pids_limit_exceeded,
        )
    except docker.errors.DockerException as exc:
        logger.error(
            "event=execution_container_error job_id=%s run_id=%s error=%s",
            job_id,
            run_id,
            exc,
        )
        return ExecutionResult(
            exit_code=None,
            stdout="",
            stderr="",
            system_error="실행 컨테이너 처리에 실패했습니다.",
            wall_time_ms=int((time.monotonic() - start) * 1000),
            memory_peak_bytes=monitor.memory_peak_bytes,
            pids_peak=pids_monitor.pids_peak,
        )
