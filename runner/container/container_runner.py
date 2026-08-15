import logging
import threading
from dataclasses import dataclass
from time import monotonic, sleep
from uuid import UUID

import docker

from runner.config import settings
from runner.metrics.cgroup_reader import (
    CgroupSnapshot,
    read_snapshot,
    resolve_cgroup_path,
)
from runner.models.job import PolicyLimits
from runner.pipeline.workspace import VolumeWorkspace


logger = logging.getLogger("runner")


@dataclass
class ExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    output_limit_exceeded: bool = False
    oom_killed: bool = False
    pids_limit_hit: bool = False
    term_signal: int | None = None
    system_error: str | None = None
    metrics: CgroupSnapshot | None = None


def _empty_snapshot() -> CgroupSnapshot:
    return CgroupSnapshot(
        memory_peak_bytes=None,
        pids_peak=None,
        cpu_usage_usec=None,
        oom_kill_count=0,
        pids_max_events=0,
    )


def _safe_kill(container) -> None:
    try:
        container.kill()
    except docker.errors.DockerException:
        pass


def _wait_for_exit(container, timeout_seconds: float) -> int | None:
    """Poll container state without relying on transport-level HTTP timeouts."""

    deadline = monotonic() + timeout_seconds
    while True:
        container.reload()
        state = container.attrs.get("State", {})
        if not bool(state.get("Running", False)):
            return int(state.get("ExitCode", 0))
        if monotonic() >= deadline:
            return None
        sleep(min(0.02, max(deadline - monotonic(), 0)))


def execute_program(
    client,
    workspace: VolumeWorkspace,
    stdin: str,
    policy: PolicyLimits,
    job_id: UUID,
    run_id: UUID,
) -> ExecutionResult:
    """컴파일된 프로그램을 강화된 별도 컨테이너에서 실행한다."""

    container = None
    output_streams = []
    output_threads = []
    stdout_buffer = bytearray()
    stderr_buffer = bytearray()
    output_limit_exceeded = False
    timed_out = False
    cgroup_path = None
    before_snapshot = _empty_snapshot()
    after_snapshot = _empty_snapshot()
    result: ExecutionResult | None = None

    output_lock = threading.Lock()

    try:
        command = ["/workspace/main"]
        if stdin:
            command = [
                "sh",
                "-c",
                "exec /workspace/main < /workspace/stdin",
            ]

        container = client.containers.create(
            image=settings.cpp_image,
            command=command,
            volumes={
                workspace.volume_name: {
                    "bind": "/workspace",
                    "mode": "ro",
                }
            },
            read_only=True,
            tmpfs={
                "/tmp": (
                    "rw,nosuid,nodev,noexec,"
                    f"size={settings.execution_tmpfs_limit_mb}m"
                )
            },
            network_disabled=True,
            user=settings.runtime_user,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            mem_limit=f"{policy.memory_limit_mb}m",
            memswap_limit=f"{policy.memory_limit_mb}m",
            nano_cpus=int(policy.cpu_limit * 1_000_000_000),
            pids_limit=policy.process_limit,
            detach=True,
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(job_id),
                "codeguard.run_id": str(run_id),
                "codeguard.stage": "execute",
            },
        )

        container.start()
        container.reload()
        pid = int(container.attrs.get("State", {}).get("Pid") or 0)
        if pid > 0:
            cgroup_path = resolve_cgroup_path(pid)
        if cgroup_path is not None:
            before_snapshot = read_snapshot(cgroup_path)

        stdout_stream = container.logs(
            stream=True,
            follow=True,
            stdout=True,
            stderr=False,
        )
        stderr_stream = container.logs(
            stream=True,
            follow=True,
            stdout=False,
            stderr=True,
        )
        output_streams.extend((stdout_stream, stderr_stream))

        def collect_output(stream, target: bytearray) -> None:
            nonlocal output_limit_exceeded
            try:
                for data in stream:
                    if not data:
                        continue
                    with output_lock:
                        used = len(stdout_buffer) + len(stderr_buffer)
                        remaining = max(
                            settings.execution_output_limit_bytes - used,
                            0,
                        )
                        target.extend(data[:remaining])
                        if len(data) > remaining:
                            output_limit_exceeded = True
                    if output_limit_exceeded:
                        _safe_kill(container)
                        return
            except Exception as exc:
                logger.warning(
                    "event=execution_output_collection_failed job_id=%s",
                    job_id,
                    exc_info=exc,
                )

        for stream_name, stream, target in (
            ("stdout", stdout_stream, stdout_buffer),
            ("stderr", stderr_stream, stderr_buffer),
        ):
            thread = threading.Thread(
                target=collect_output,
                args=(stream, target),
                name=f"runner-{stream_name}-{run_id}",
                daemon=True,
            )
            output_threads.append(thread)
            thread.start()

        exit_code = _wait_for_exit(
            container,
            timeout_seconds=policy.timeout_ms / 1000,
        )
        if exit_code is None:
            timed_out = True
            _safe_kill(container)
            exit_code = _wait_for_exit(container, timeout_seconds=2)

        for thread in output_threads:
            thread.join(timeout=2)

        container.reload()
        state = container.attrs.get("State", {})
        docker_oom_killed = bool(state.get("OOMKilled", False))

        if cgroup_path is not None:
            after_snapshot = read_snapshot(cgroup_path)

        oom_killed = (
            docker_oom_killed
            or after_snapshot.oom_kill_count > before_snapshot.oom_kill_count
        )
        pids_limit_hit = (
            after_snapshot.pids_max_events > before_snapshot.pids_max_events
        )
        term_signal = (
            exit_code - 128
            if exit_code is not None and 128 <= exit_code <= 255
            else None
        )

        result = ExecutionResult(
            exit_code=exit_code,
            stdout=bytes(stdout_buffer).decode("utf-8", errors="replace"),
            stderr=bytes(stderr_buffer).decode("utf-8", errors="replace"),
            timed_out=timed_out,
            output_limit_exceeded=output_limit_exceeded,
            oom_killed=oom_killed,
            pids_limit_hit=pids_limit_hit,
            term_signal=term_signal,
            metrics=after_snapshot,
        )

    except docker.errors.DockerException as exc:
        logger.error(
            "event=execution_container_error job_id=%s run_id=%s error=%s",
            job_id,
            run_id,
            exc,
        )
        result = ExecutionResult(
            exit_code=None,
            stdout=bytes(stdout_buffer).decode("utf-8", errors="replace"),
            stderr=bytes(stderr_buffer).decode("utf-8", errors="replace"),
            system_error="Execution Container 처리에 실패했습니다.",
            metrics=after_snapshot,
        )
    finally:
        for stream in output_streams:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if container is not None:
            try:
                container.remove(force=True)
            except docker.errors.DockerException as exc:
                logger.error(
                    "event=execution_container_cleanup_failed "
                    "job_id=%s run_id=%s error=%s",
                    job_id,
                    run_id,
                    exc,
                )
                if result is None:
                    result = ExecutionResult(
                        exit_code=None,
                        stdout="",
                        stderr="",
                        system_error="Execution Container 정리에 실패했습니다.",
                    )
                else:
                    result.system_error = "Execution Container 정리에 실패했습니다."

    return result
