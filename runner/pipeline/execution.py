import logging
import os
import threading
import time
from dataclasses import dataclass
from uuid import UUID

import docker

from runner.config import settings
from runner.exceptions import (
    ContainerExecutionError,
    SecurityVerificationError,
    RunnerError,
    TaskTrackingError,
)
from runner.metrics.network_detector import detect_network_block
from runner.metrics.cgroup_scope import CgroupMetrics, ExecutionCgroupScope
from runner.metrics.resource_monitor import ResourceMonitor
from runner.metrics.pids_monitor import PidsLimitMonitor
from runner.metrics.task_tracker import (
    PidsPeakSnapshot,
    TaskTrackerClient,
    resolve_execution_cgroup,
)
from runner.metrics.cpu_usage_sampler import CpuUsageSampler
from runner.metrics.memory_usage_sampler import MemoryUsageSampler
from runner.models.result import CpuUsageSample, MemoryUsageSample
from runner.pipeline.workspace import VolumeWorkspace, execution_mounts
from runner.policies.filesystem import FilesystemPolicy, validate_filesystem_policy
from runner.security.filesystem_startup import (
    POLICY_PATH, STATUS_PATH, build_policy_archive,
    wait_for_filesystem_prepared, verify_filesystem_applied,
)
from runner.pipeline.start_gate import (
    find_codeguard_init_tid,
    release_start_gate,
    wait_for_security_evidence,
)
from runner.policies import (
    EXECUTION_LOGICAL_CPU_LIMIT,
    EXECUTION_OUTPUT_LIMIT_BYTES,
)
from runner.security import (
    SECURITY_CAP_DROP,
    SECURITY_GID,
    SECURITY_NO_NEW_PRIVILEGES,
    SECURITY_OPT,
    SECURITY_UID,
    verify_container_security_config,
)
from runner.security.runtime_verification import (
    SECURITY_STATUS_PATH,
    collect_runtime_permission_failure,
)
from runner.security.filesystem_trace import (
    TRACE_DIRECTORY, TRACE_PATH, TRACE_SYSCALLS, RAW_WRITE_SYSCALLS,
    FilesystemViolation, collect_filesystem_trace,
)


logger = logging.getLogger("runner")

EXECUTION_UID = SECURITY_UID
EXECUTION_GID = SECURITY_GID
EXECUTION_CAP_DROP = SECURITY_CAP_DROP
EXECUTION_NO_NEW_PRIVILEGES = SECURITY_NO_NEW_PRIVILEGES
TRACER_CAP_ADD = ("SYS_PTRACE", "SETUID", "SETGID")


@dataclass
class ExecutionResult:
    exit_code: int | None
    stdout: str
    stderr: str
    system_error: str | None = None
    timed_out: bool = False
    output_limit_exceeded: bool = False
    oom_killed: bool = False
    cpu_time_limit_exceeded: bool = False
    wall_time_ms: int | None = None
    cpu_time_ms: int | None = None
    memory_peak_bytes: int | None = None
    pids_peak: int | None = None
    user_task_peak: int | None = None
    process_at_user_task_peak: int | None = None
    thread_at_user_task_peak: int | None = None
    pids_limit_exceeded: bool = False
    filesystem_limit_exceeded: bool = False
    filesystem_violation_syscall: str | None = None
    filesystem_violation_path: str | None = None
    cpu_usage_samples: list[CpuUsageSample] | None = None
    memory_usage_samples: list[MemoryUsageSample] | None = None
    network_blocked: bool = False


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


def _monotonic_ns() -> int:
    """Read the monotonic clock domain used by bpf_ktime_get_ns()."""
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC)


def _merge_exec_timing_snapshot(
    snapshot: PidsPeakSnapshot,
    *,
    expected_root_tid: int,
    observed_start_ns: int | None,
    observed_end_ns: int,
    observed_now_ns: int,
) -> tuple[int | None, int]:
    """Validate and merge an immutable eBPF execution boundary snapshot."""
    if snapshot.root_tid != expected_root_tid:
        raise TaskTrackingError(
            "Task tracker returned an unexpected root TID."
        )
    if snapshot.exec_start_ns > observed_now_ns:
        raise TaskTrackingError(
            "Exec start timestamp is ahead of CLOCK_MONOTONIC."
        )
    if snapshot.exec_end_ns > observed_now_ns:
        raise TaskTrackingError(
            "Exec end timestamp is ahead of CLOCK_MONOTONIC."
        )
    if snapshot.exec_end_ns != 0 and (
        snapshot.exec_start_ns == 0
        or snapshot.exec_end_ns < snapshot.exec_start_ns
    ):
        raise TaskTrackingError("Invalid task tracker exec timing state.")
    if (
        observed_start_ns is not None
        and snapshot.exec_start_ns != observed_start_ns
    ):
        raise TaskTrackingError(
            "Root exec timestamp changed unexpectedly."
        )
    if observed_end_ns > 0 and snapshot.exec_end_ns != observed_end_ns:
        raise TaskTrackingError(
            "Final exec timestamp changed unexpectedly."
        )
    if snapshot.exec_start_ns == 0:
        return observed_start_ns, observed_end_ns
    return snapshot.exec_start_ns, snapshot.exec_end_ns


def _wall_time_ms(
    *,
    exec_start_ns: int | None,
    exec_end_ns: int,
    fallback_start_ns: int,
    finished_at_ns: int,
) -> int:
    if (
        exec_start_ns is not None
        and exec_end_ns >= exec_start_ns
        and exec_end_ns > 0
    ):
        return (exec_end_ns - exec_start_ns) // 1_000_000
    return max(finished_at_ns - fallback_start_ns, 0) // 1_000_000


def _resolve_cpuset() -> str:
    available_cpus = sorted(os.sched_getaffinity(0))

    if not available_cpus:
        raise ContainerExecutionError(
            "Runner에서 사용 가능한 논리 CPU를 확인할 수 없습니다.",
        )

    selected_cpus = available_cpus[:EXECUTION_LOGICAL_CPU_LIMIT]
    return ",".join(str(cpu) for cpu in selected_cpus)

def create_execution_container(
    client,
    workspace: VolumeWorkspace,
    stdin: str,
    job_id: UUID,
    run_id: UUID,
    memory_limit_mb: int,
    cpu_bandwidth: float,
    pids_limit: int,
    cgroup_scope: ExecutionCgroupScope | None = None,
    filesystem_policy: FilesystemPolicy | None = None,
    network_mode: str | None = None,
    dns: list[str] | None = None,
):
    """Job Volume을 연결한 실행 컨테이너를 생성하고 반환한다."""

    if filesystem_policy is None:
        raise ContainerExecutionError("파일시스템 정책 없이 사용자 코드를 실행할 수 없습니다.")
    validate_filesystem_policy(filesystem_policy)

    # A persistent anonymous evidence volume survives container exit, unlike tmpfs.
    # Keep strace as container PID 1 so Docker wait observes the tracer only after
    # it has followed codeguard-init/user exit and completed the trace footer.
    trace_command = (
        f"umask 077; set -Ce; exec 3>{SECURITY_STATUS_PATH}; "
        f"exec 4<{POLICY_PATH}; exec 5>{STATUS_PATH}; ulimit -f 2048; "
        f"exec strace -f -q -yy -s 4096 "
        f"-u codeguard "
        f"-o {TRACE_PATH} -e trace={TRACE_SYSCALLS} "
        f"-e raw={RAW_WRITE_SYSCALLS} /usr/local/bin/codeguard-init "
        f"--security-fd 3 --filesystem-policy-fd 4 --filesystem-status-fd 5 "
        f"--stdin /workspace/input/stdin --workdir /workspace/work "
        f"-- /workspace/app/main"
    )
    command = ["sh", "-c", trace_command]

    memory_limit_bytes = memory_limit_mb * 1024 * 1024
    nano_cpus_limit = int(cpu_bandwidth * 1_000_000_000)
    cpuset_cpus = _resolve_cpuset()

    container_options = {
        "image": filesystem_policy.image_id,
        "command": command,
        "mounts": execution_mounts(workspace),
        "detach": True,
        "read_only": True,
        
        "network_mode": network_mode or settings.execution_network,
        "user": "0:0",
        "cap_drop": list(EXECUTION_CAP_DROP),
        "cap_add": list(TRACER_CAP_ADD),
        "security_opt": [SECURITY_OPT],
        "mem_limit": memory_limit_bytes,
        "memswap_limit": memory_limit_bytes,
        "nano_cpus": nano_cpus_limit,
        "cpuset_cpus": cpuset_cpus,
        "pids_limit": pids_limit,
        "labels": {
            "codeguard.managed": "true",
            "codeguard.job_id": str(job_id),
            "codeguard.run_id": str(run_id),
            "codeguard.stage": "execute",
        },
    }
    # 프리셋 DNS: None 이면 Docker 기본(내장 resolver) 유지, 리스트면 resolv.conf 고정.
    # P0 는 ["127.0.0.1"] 로 내장 resolver 를 제거해 DNS 해석까지 차단한다.
    if dns is not None:
        container_options["dns"] = dns
    if cgroup_scope is not None:
        container_options["cgroup_parent"] = cgroup_scope.docker_parent

    try:
        container = client.containers.create(
            **container_options,
        )
        try:
            verify_container_security_config(
                container,
                stage="execute",
                workspace_mode="ro",
                expected_user="0:0",
                required_cap_add=TRACER_CAP_ADD,
                expected_workspace=workspace,
                expected_image_id=filesystem_policy.image_id,
            )
            uploaded = container.put_archive(
                TRACE_DIRECTORY, build_policy_archive(filesystem_policy),
            )
            if uploaded is not True:
                raise ContainerExecutionError("파일시스템 정책 전달에 실패했습니다.")
        except RunnerError:
            try:
                container.remove(force=True, v=True)
            except docker.errors.DockerException:
                pass
            raise
        except Exception:
            try:
                container.remove(force=True, v=True)
            except docker.errors.DockerException:
                logger.exception("event=filesystem_setup_cleanup_error")
            raise
        return container
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
    task_tracker: TaskTrackerClient | None = None,
    cpu_time_limit_ms: int | None = None,
    filesystem_policy_id: str | None = None,
) -> ExecutionResult:
    """제한을 감시하며 실행 컨테이너의 종료 정보와 출력을 수집한다."""

    if (
        not isinstance(filesystem_policy_id, str) or len(filesystem_policy_id) != 64
        or any(c not in "0123456789abcdef" for c in filesystem_policy_id)
    ):
        raise ContainerExecutionError("파일시스템 정책 식별값을 확인하지 못했습니다.")

    operation_start_ns = _monotonic_ns()
    gate_start_ns: int | None = None
    registered_root_tid: int | None = None
    observed_exec_start_ns: int | None = None
    observed_exec_end_ns = 0
    network_start = time.time()
    output = _BoundedOutput(output_limit_bytes)
    monitor = ResourceMonitor(container)
    pids_monitor = PidsLimitMonitor(container)
    wait_done = threading.Event()
    wait_state: dict[str, object] = {}
    output_state: dict[str, Exception | None] = {}
    normal_timeout_reached = False
    legacy_timeout_reached = False
    timing_safety_timeout_reached = False
    timeout_kill_requested = False
    cpu_time_limit_reached = False
    cpu_time_kill_requested = False
    pids_limit_exceeded = False
    system_error = None
    output_thread = None
    monitor_started = False
    output_thread_stopped = True
    container_started = False
    cgroup_registered = False
    root_registered = False
    task_metrics: PidsPeakSnapshot | None = None
    exec_timing_error_logged = False
    cpu_start_usec: int | None = None
    cpu_sampler: CpuUsageSampler | None = None
    memory_sampler: MemoryUsageSampler | None = None
    container_ip = None
    network_blocked = False

    def wait_for_container() -> None:
        try:
            wait_state["result"] = container.wait()
        except Exception as exc:
            wait_state["error"] = exc
        finally:
            wait_state["finished_at_ns"] = _monotonic_ns()
            wait_done.set()

    def collect_output() -> None:
        output_state["error"] = _collect_output(frames, output)

    def poll_exec_timing() -> int:
        """Merge one tracker snapshot without discarding known timestamps."""
        nonlocal observed_exec_start_ns
        nonlocal observed_exec_end_ns
        nonlocal exec_timing_error_logged

        assert task_tracker is not None
        assert registered_root_tid is not None
        try:
            timing_snapshot = task_tracker.snapshot(run_id)
            observed_now_ns = _monotonic_ns()
            previous_start_ns = observed_exec_start_ns
            (
                observed_exec_start_ns,
                observed_exec_end_ns,
            ) = _merge_exec_timing_snapshot(
                timing_snapshot,
                expected_root_tid=registered_root_tid,
                observed_start_ns=observed_exec_start_ns,
                observed_end_ns=observed_exec_end_ns,
                observed_now_ns=observed_now_ns,
            )
            if (
                previous_start_ns is None
                and observed_exec_start_ns is not None
            ):
                logger.info(
                    "event=execution_timing_ebpf_started "
                    "job_id=%s run_id=%s exec_start_ns=%s",
                    job_id,
                    run_id,
                    observed_exec_start_ns,
                )
            return observed_now_ns
        except TaskTrackingError as exc:
            if not exec_timing_error_logged:
                logger.warning(
                    "event=task_tracker_exec_timing_error "
                    "job_id=%s run_id=%s error=%s",
                    job_id,
                    run_id,
                    exc,
                )
                exec_timing_error_logged = True
            return _monotonic_ns()

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
            container.start()
            container_started = True

            container.reload()

            networks = container.attrs.get(
                "NetworkSettings", {}
            ).get("Networks", {})

            for network in networks.values():
                ip = network.get("IPAddress")
                if ip:
                    container_ip = ip
                    break

            # codeguard-init has written trusted evidence and is still waiting
            # for start.ready; neither user code nor its first fork can run yet.
            wait_for_security_evidence(container)
            wait_for_filesystem_prepared(
                container, filesystem_policy_id,
                timeout_seconds=settings.filesystem_startup_timeout_seconds,
            )

            if task_tracker is not None:
                try:
                    container.reload()
                    tracer_tid = container.attrs.get("State", {}).get("Pid")
                    if not isinstance(tracer_tid, int) or tracer_tid <= 0:
                        raise TaskTrackingError(
                            "Execution Container의 strace TID가 없습니다."
                        )
                    root_tid = find_codeguard_init_tid(tracer_tid)
                    registered_root_tid = root_tid
                    identity = resolve_execution_cgroup(root_tid)
                    task_tracker.register_cgroup(
                        run_id,
                        identity.cgroup_id,
                        identity.pids_current,
                    )
                    cgroup_registered = True
                    task_tracker.register_root(run_id, root_tid)
                    root_registered = True
                except (
                    TaskTrackingError,
                    docker.errors.DockerException,
                ) as exc:
                    logger.warning(
                        "event=task_tracker_registration_error "
                        "job_id=%s run_id=%s error=%s",
                        job_id,
                        run_id,
                        exc,
                    )


            # Take the CPU baseline while codeguard-init is still gated.
            if cgroup_scope is not None:
                try:
                    cpu_start_usec = cgroup_scope.read_cpu_usage_usec()
                except Exception as exc:
                    logger.warning(
                        "event=execution_cpu_baseline_error "
                        "job_id=%s run_id=%s error=%s",
                        job_id,
                        run_id,
                        exc,
                    )

            if cpu_time_limit_ms is not None and cpu_start_usec is None:
                raise ContainerExecutionError(
                    "CPU 사용시간 제한 적용을 위한 기준값을 측정하지 못했습니다."
                )

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

            monitoring_start = time.monotonic()
            if cgroup_scope is not None and cpu_start_usec is not None:
                cpu_sampler = CpuUsageSampler(
                    cgroup_scope=cgroup_scope,
                    start_cpu_usec=cpu_start_usec,
                    start_time=monitoring_start,
                )
            if cgroup_scope is not None:
                memory_sampler = MemoryUsageSampler(
                    cgroup_scope=cgroup_scope,
                    start_time=monitoring_start,
                )
            # The gate timestamp is a safety/legacy origin only. In the normal
            # tracker path sched_process_exec supplies the wall-time origin.
            gate_start_ns = _monotonic_ns()
            release_start_gate(container)

            if task_tracker is None:
                logger.info(
                    "event=execution_timing_legacy_mode "
                    "job_id=%s run_id=%s reason=tracker_disabled",
                    job_id,
                    run_id,
                )
            elif not (cgroup_registered and root_registered):
                logger.warning(
                    "event=execution_timing_safety_mode "
                    "job_id=%s run_id=%s reason=tracker_registration_failed",
                    job_id,
                    run_id,
                )

            timeout_ns = timeout_ms * 1_000_000
            while not wait_done.is_set():

                if output.exceeded.is_set():
                    break

                if pids_monitor.exceeded():
                    pids_limit_exceeded = True
                    break

                now = time.monotonic()
                timeout_now_ns = _monotonic_ns()

                if (
                    task_tracker is not None
                    and cgroup_registered
                    and root_registered
                    and observed_exec_end_ns == 0
                ):
                    timeout_now_ns = poll_exec_timing()

                if cpu_sampler is not None:
                    cpu_sampler.sample_if_due(now)
                if memory_sampler is not None:
                    memory_sampler.sample_if_due(now)

                if (
                    cpu_time_limit_ms is not None
                    and cgroup_scope is not None
                    and cpu_start_usec is not None
                ):
                    try:
                        current_cpu_usec = cgroup_scope.read_cpu_usage_usec()
                    except Exception as exc:
                        logger.warning(
                            "event=execution_cpu_time_measurement_error "
                            "job_id=%s run_id=%s error=%s",
                            job_id,
                            run_id,
                            exc,
                        )
                        current_cpu_usec = None

                    if current_cpu_usec is not None:
                        cpu_time_used_usec = max(
                            current_cpu_usec - cpu_start_usec,
                            0,
                        )

                        if cpu_time_used_usec >= cpu_time_limit_ms * 1000:
                            cpu_time_limit_reached = True
                            break

                assert gate_start_ns is not None
                exec_deadline_ns = (
                    observed_exec_start_ns + timeout_ns
                    if observed_exec_start_ns is not None
                    else None
                )
                if observed_exec_end_ns > 0:
                    if (
                        exec_deadline_ns is not None
                        and observed_exec_end_ns > exec_deadline_ns
                    ):
                        normal_timeout_reached = True
                    break

                deadline_ns = (
                    exec_deadline_ns
                    if exec_deadline_ns is not None
                    else gate_start_ns + timeout_ns
                )
                remaining_ns = deadline_ns - timeout_now_ns
                if remaining_ns <= 0:
                    # A fresh eBPF snapshot closes the natural-exit race at
                    # the deadline. Only an end at/before the deadline wins.
                    if (
                        task_tracker is not None
                        and cgroup_registered
                        and root_registered
                        and observed_exec_end_ns == 0
                    ):
                        timeout_now_ns = poll_exec_timing()
                        exec_deadline_ns = (
                            observed_exec_start_ns + timeout_ns
                            if observed_exec_start_ns is not None
                            else None
                        )
                        if (
                            observed_exec_end_ns > 0
                            and exec_deadline_ns is not None
                            and observed_exec_end_ns <= exec_deadline_ns
                        ):
                            break
                        deadline_ns = (
                            exec_deadline_ns
                            if exec_deadline_ns is not None
                            else gate_start_ns + timeout_ns
                        )
                        if timeout_now_ns < deadline_ns:
                            continue

                    if observed_exec_start_ns is not None:
                        normal_timeout_reached = True
                        logger.info(
                            "event=normal_time_limit "
                            "job_id=%s run_id=%s exec_start_ns=%s",
                            job_id,
                            run_id,
                            observed_exec_start_ns,
                        )
                    elif task_tracker is None:
                        legacy_timeout_reached = True
                        logger.warning(
                            "event=execution_timing_legacy_timeout "
                            "job_id=%s run_id=%s gate_start_ns=%s",
                            job_id,
                            run_id,
                            gate_start_ns,
                        )
                    else:
                        timing_safety_timeout_reached = True
                        logger.error(
                            "event=execution_timing_safety_timeout "
                            "job_id=%s run_id=%s gate_start_ns=%s",
                            job_id,
                            run_id,
                            gate_start_ns,
                        )
                    break
                wait_done.wait(
                    timeout=min(remaining_ns / 1_000_000_000, 0.01)
                )

            wall_timeout_reached = (
                normal_timeout_reached
                or legacy_timeout_reached
                or timing_safety_timeout_reached
            )
            policy_kill = (
                wall_timeout_reached
                or output.exceeded.is_set()
                or pids_limit_exceeded
                or cpu_time_limit_reached
            )
            if policy_kill and not wait_done.is_set():
                try:
                    container.kill()
                    if normal_timeout_reached or legacy_timeout_reached:
                        timeout_kill_requested = True
                    if cpu_time_limit_reached:
                        cpu_time_kill_requested = True 
                    
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

            if not wait_done.wait(timeout=1.0) and (
                observed_exec_end_ns > 0 and not policy_kill
            ):
                system_error = (
                    "사용자 실행 종료 후 Container 정리를 "
                    "완료하지 못했습니다."
                )
                logger.error(
                    "event=execution_cleanup_wait_timeout "
                    "job_id=%s run_id=%s exec_end_ns=%s",
                    job_id,
                    run_id,
                    observed_exec_end_ns,
                )
                try:
                    container.kill()
                except docker.errors.DockerException as exc:
                    logger.warning(
                        "event=execution_cleanup_kill_error "
                        "job_id=%s run_id=%s error=%s",
                        job_id,
                        run_id,
                        exc,
                    )
                if not wait_done.wait(timeout=2.0):
                    system_error = "실행 컨테이너를 종료하지 못했습니다."
        except Exception:
            if container_started:
                try:
                    container.kill()
                except docker.errors.DockerException as exc:
                    logger.warning(
                        "event=execution_start_abort_error job_id=%s run_id=%s error=%s",
                        job_id, run_id, exc,
                    )
            raise
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

        final_policy_kill = (
            normal_timeout_reached
            or legacy_timeout_reached
            or timing_safety_timeout_reached
            or output.exceeded.is_set()
            or pids_limit_exceeded
            or cpu_time_limit_reached
        )
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

        # Reaching the deadline does not prove timeout caused the exit: a natural
        # exit (e.g. SIGSEGV/139) can win the race with a successful kill request.
        timed_out = timeout_kill_requested and exit_code == 137
        cpu_time_limit_exceeded = (
            cpu_time_kill_requested
            and exit_code == 137
        )

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

        cpu_time_ms: int | None = None

        if (
            cpu_start_usec is not None
            and cgroup_metrics.cpu_time_usec is not None
        ):
            cpu_time_ms = max(
                cgroup_metrics.cpu_time_usec - cpu_start_usec,
                0,
            ) // 1000

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

        if task_tracker is not None and cgroup_registered and root_registered:
            try:
                task_metrics = task_tracker.snapshot(run_id)
                assert registered_root_tid is not None
                (
                    observed_exec_start_ns,
                    observed_exec_end_ns,
                ) = _merge_exec_timing_snapshot(
                    task_metrics,
                    expected_root_tid=registered_root_tid,
                    observed_start_ns=observed_exec_start_ns,
                    observed_end_ns=observed_exec_end_ns,
                    observed_now_ns=_monotonic_ns(),
                )
            except TaskTrackingError as exc:
                task_metrics = None
                logger.warning(
                    "event=task_tracker_snapshot_error "
                    "job_id=%s run_id=%s error=%s",
                    job_id,
                    run_id,
                    exc,
                )

        user_task_peak = None
        process_at_user_task_peak = None
        thread_at_user_task_peak = None
        if task_metrics is not None:
            user_task_peak = task_metrics.user_task_peak
            process_at_user_task_peak = (
                task_metrics.process_at_user_task_peak
            )
            thread_at_user_task_peak = (
                task_metrics.thread_at_user_task_peak
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
        finished_at_ns = wait_state.get("finished_at_ns")
        if not isinstance(finished_at_ns, int):
            finished_at_ns = _monotonic_ns()
        finished_at = finished_at_ns / 1_000_000_000

        fallback_start_ns = (
            observed_exec_start_ns
            if observed_exec_start_ns is not None
            else (
                gate_start_ns
                if gate_start_ns is not None
                else operation_start_ns
            )
        )
        wall_time_ms = _wall_time_ms(
            exec_start_ns=observed_exec_start_ns,
            exec_end_ns=observed_exec_end_ns,
            fallback_start_ns=fallback_start_ns,
            finished_at_ns=finished_at_ns,
        )

        if (
            observed_exec_start_ns is not None
            and observed_exec_end_ns > 0
        ):
            exec_deadline_ns = observed_exec_start_ns + timeout_ms * 1_000_000
            if observed_exec_end_ns <= exec_deadline_ns:
                timed_out = False
            else:
                timed_out = True

        if cpu_sampler is not None:
            cpu_sampler.sample_final(
                finished_at=finished_at,
                final_cpu_usec=cgroup_metrics.cpu_time_usec,
            )
        if memory_sampler is not None:
            try:
                final_memory_bytes = (
                    cgroup_scope.read_memory_current_bytes()
                    if cgroup_scope is not None
                    else None
                )
            except Exception as exc:
                logger.warning(
                    "event=execution_memory_final_sample_error "
                    "job_id=%s run_id=%s error=%s",
                    job_id,
                    run_id,
                    exc,
                )
                final_memory_bytes = None
            memory_sampler.sample_final(
                finished_at=finished_at,
                final_memory_bytes=final_memory_bytes,
            )
        
        if collect_runtime_permission_failure(container):
            raise SecurityVerificationError(
                "Execution Container 권한 제한 적용을 검증하지 못했습니다."
            )

        filesystem_violation = FilesystemViolation()
        if system_error is None:
            try:
                verify_filesystem_applied(container, filesystem_policy_id)
            except RunnerError as exc:
                system_error = exc.message
                logger.error("event=filesystem_startup_invalid error=%s", exc)
        if system_error is None:
            try:
                filesystem_violation = collect_filesystem_trace(
                    container,
                    interrupted=final_policy_kill or oom_killed or pids_limit_exceeded,
                )
            except Exception as exc:
                system_error = "파일시스템 추적 증거 수집 또는 분석에 실패했습니다."
                logger.error(
                    "event=filesystem_trace_error job_id=%s run_id=%s error=%s",
                    job_id, run_id, exc,
                )

        if container_ip:
            network_blocked = detect_network_block(
                container_ip,
                network_start,
            )

        if (
            timing_safety_timeout_reached
            and system_error is None
            and not output.exceeded.is_set()
            and not cpu_time_limit_exceeded
            and not oom_killed
            and not pids_limit_exceeded
        ):
            system_error = (
                "eBPF 실행 시작시각을 측정하지 못해 "
                "Gate 기준 Safety Timeout으로 종료했습니다."
            )

        return ExecutionResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            system_error=system_error,
            timed_out=timed_out,
            output_limit_exceeded=output.exceeded.is_set(),
            cpu_time_limit_exceeded=cpu_time_limit_exceeded,
            oom_killed=oom_killed,
            wall_time_ms=wall_time_ms,
            cpu_time_ms=cpu_time_ms,
            cpu_usage_samples=(
                cpu_sampler.samples
                if cpu_sampler is not None
                else None
            ),
            memory_usage_samples=(
                memory_sampler.samples
                if memory_sampler is not None
                else None
            ),
            memory_peak_bytes=memory_peak_bytes,
            pids_peak=pids_peak,
            user_task_peak=user_task_peak,
            process_at_user_task_peak=process_at_user_task_peak,
            thread_at_user_task_peak=thread_at_user_task_peak,
            pids_limit_exceeded=pids_limit_exceeded,
            filesystem_limit_exceeded=filesystem_violation.detected,
            filesystem_violation_syscall=filesystem_violation.syscall,
            filesystem_violation_path=filesystem_violation.path,
            network_blocked=network_blocked,
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
            wall_time_ms=max(
                _monotonic_ns()
                - (
                    observed_exec_start_ns
                    if observed_exec_start_ns is not None
                    else (
                        gate_start_ns
                        if gate_start_ns is not None
                        else operation_start_ns
                    )
                ),
                0,
            ) // 1_000_000,
            memory_peak_bytes=monitor.memory_peak_bytes,
            pids_peak=pids_monitor.pids_peak,
            network_blocked=network_blocked,
        )
    finally:
        if task_tracker is not None:
            try:
                task_tracker.remove(run_id)
            except TaskTrackingError as exc:
                logger.warning(
                    "event=task_tracker_cleanup_error "
                    "job_id=%s run_id=%s error=%s",
                    job_id,
                    run_id,
                    exc,
                )
