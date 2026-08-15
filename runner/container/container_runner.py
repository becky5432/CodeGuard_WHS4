import io
import logging
import tarfile
import threading
from dataclasses import dataclass
from uuid import UUID

import docker
from requests.exceptions import ReadTimeout

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


def _build_stdin_archive(stdin: str) -> bytes:
    data = stdin.encode("utf-8")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo(name="stdin")
        info.size = len(data)
        info.mode = 0o444
        archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _safe_kill(container) -> None:
    try:
        container.kill()
    except docker.errors.DockerException:
        pass


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
    output_stream = None
    output_thread = None
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
            command = ["sh", "-c", "exec /workspace/main < /tmp/stdin"]

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

        if stdin:
            container.put_archive(
                path="/tmp",
                data=_build_stdin_archive(stdin),
            )

        container.start()
        container.reload()
        pid = int(container.attrs.get("State", {}).get("Pid") or 0)
        if pid > 0:
            cgroup_path = resolve_cgroup_path(pid)
        if cgroup_path is not None:
            before_snapshot = read_snapshot(cgroup_path)

        output_stream = container.attach(
            stream=True,
            stdout=True,
            stderr=True,
            logs=True,
            demux=True,
        )

        def collect_output() -> None:
            nonlocal output_limit_exceeded
            try:
                for chunk in output_stream:
                    if isinstance(chunk, tuple):
                        stdout_chunk, stderr_chunk = chunk
                    else:
                        stdout_chunk, stderr_chunk = chunk, None

                    for target, data in (
                        (stdout_buffer, stdout_chunk),
                        (stderr_buffer, stderr_chunk),
                    ):
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
            except docker.errors.DockerException:
                logger.exception(
                    "event=execution_output_collection_failed job_id=%s",
                    job_id,
                )

        output_thread = threading.Thread(
            target=collect_output,
            name=f"runner-output-{run_id}",
            daemon=True,
        )
        output_thread.start()

        try:
            wait_result = container.wait(timeout=policy.timeout_ms / 1000)
            exit_code = int(wait_result["StatusCode"])
        except ReadTimeout:
            timed_out = True
            exit_code = None
            _safe_kill(container)
            try:
                wait_result = container.wait(timeout=2)
                exit_code = int(wait_result["StatusCode"])
            except (ReadTimeout, docker.errors.DockerException):
                exit_code = None

        if output_thread is not None:
            output_thread.join(timeout=2)

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
        close = getattr(output_stream, "close", None)
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
