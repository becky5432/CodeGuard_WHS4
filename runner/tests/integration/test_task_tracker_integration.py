import os
import platform
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import docker

from runner.config import settings
from runner.models.job import PolicyLimits, RunnerLanguage, RunnerRequest
from runner.models.result import RunnerReasonCode, RunnerStatus
from runner.metrics.task_tracker import TaskTrackerClient
from runner.pipeline import execution as execution_module
from runner.pipeline.executor import execute_job
from runner.tests.filesystem_helpers import pin_execution_image


SINGLE_TASK_SOURCE = r"""
#define _DEFAULT_SOURCE
#include <unistd.h>

int main(void) {
    usleep(200000);
    return 0;
}
"""

IMMEDIATE_EXIT_SOURCE = r"""
int main(void) {
    return 0;
}
"""

SLEEP_SOURCE = r"""
#define _DEFAULT_SOURCE
#include <unistd.h>

int main(void) {
    usleep(500000);
    return 0;
}
"""

FORK_CHILD_SOURCE = r"""
#define _DEFAULT_SOURCE
#include <sys/types.h>
#include <unistd.h>

int main(void) {
    pid_t pid = fork();
    if (pid < 0)
        return 1;
    if (pid == 0) {
        usleep(500000);
        return 0;
    }
    return 0;
}
"""

CHILD_EXEC_SOURCE = r"""
#define _DEFAULT_SOURCE
#include <sys/types.h>
#include <unistd.h>

int main(int argc, char **argv) {
    if (argc > 1) {
        usleep(300000);
        return 0;
    }

    usleep(400000);
    pid_t pid = fork();
    if (pid < 0)
        return 1;
    if (pid == 0) {
        execl("/workspace/app/main", argv[0], "child", (char *)0);
        _exit(127);
    }
    return 0;
}
"""

THREAD_BARRIER_SOURCE = r"""
#define _GNU_SOURCE
#include <pthread.h>
#include <unistd.h>

#define THREAD_COUNT 15

static pthread_barrier_t barrier;

static void *worker(void *unused) {
    (void)unused;
    pthread_barrier_wait(&barrier);
    usleep(200000);
    return 0;
}

int main(void) {
    pthread_t threads[THREAD_COUNT];
    if (pthread_barrier_init(&barrier, 0, THREAD_COUNT + 1) != 0)
        return 1;
    for (int i = 0; i < THREAD_COUNT; i++)
        if (pthread_create(&threads[i], 0, worker, 0) != 0)
            return 2;
    pthread_barrier_wait(&barrier);
    usleep(200000);
    for (int i = 0; i < THREAD_COUNT; i++)
        pthread_join(threads[i], 0);
    pthread_barrier_destroy(&barrier);
    return 0;
}
"""

MIXED_PROCESS_THREAD_SOURCE = r"""
#define _GNU_SOURCE
#include <pthread.h>
#include <sys/wait.h>
#include <unistd.h>

#define CHILD_COUNT 2
#define THREADS_PER_PROCESS 4

static pthread_barrier_t barrier;

static void *worker(void *unused) {
    (void)unused;
    pthread_barrier_wait(&barrier);
    usleep(400000);
    return 0;
}

static int run_process(void) {
    pthread_t threads[THREADS_PER_PROCESS];
    if (pthread_barrier_init(
            &barrier,
            0,
            THREADS_PER_PROCESS + 1
        ) != 0)
        return 10;
    for (int i = 0; i < THREADS_PER_PROCESS; i++)
        if (pthread_create(&threads[i], 0, worker, 0) != 0)
            return 11;
    pthread_barrier_wait(&barrier);
    usleep(400000);
    for (int i = 0; i < THREADS_PER_PROCESS; i++)
        pthread_join(threads[i], 0);
    return 0;
}

int main(void) {
    pid_t children[CHILD_COUNT];
    for (int i = 0; i < CHILD_COUNT; i++) {
        children[i] = fork();
        if (children[i] < 0)
            return 1;
        if (children[i] == 0)
            _exit(run_process());
    }

    int result = run_process();
    for (int i = 0; i < CHILD_COUNT; i++) {
        int status = 0;
        waitpid(children[i], &status, 0);
        if (!WIFEXITED(status) || WEXITSTATUS(status) != 0)
            result = 2;
    }
    return result;
}
"""

PIDS_LIMIT_SOURCE = r"""
#define _GNU_SOURCE
#include <pthread.h>
#include <unistd.h>

#define THREAD_COUNT 40

static void *worker(void *unused) {
    (void)unused;
    usleep(500000);
    return 0;
}

int main(void) {
    pthread_t threads[THREAD_COUNT];
    for (int i = 0; i < THREAD_COUNT; i++) {
        if (pthread_create(&threads[i], 0, worker, 0) != 0)
            usleep(500000);
    }
    return 0;
}
"""

SHORT_THREADS_SOURCE = r"""
#include <pthread.h>

static void *worker(void *unused) {
    (void)unused;
    return 0;
}

int main(void) {
    for (int i = 0; i < 100; i++) {
        pthread_t thread;
        if (pthread_create(&thread, 0, worker, 0) != 0)
            return 1;
        pthread_join(thread, 0);
    }
    return 0;
}
"""

SHORT_PROCESSES_SOURCE = r"""
#include <sys/wait.h>
#include <unistd.h>

int main(void) {
    for (int i = 0; i < 100; i++) {
        pid_t child = fork();
        if (child < 0)
            return 1;
        if (child == 0)
            _exit(0);
        if (waitpid(child, 0, 0) < 0)
            return 2;
    }
    return 0;
}
"""

MAIN_THREAD_EXITS_SOURCE = r"""
#define _DEFAULT_SOURCE
#include <pthread.h>
#include <unistd.h>

static void *worker(void *unused) {
    (void)unused;
    usleep(300000);
    return 0;
}

int main(void) {
    pthread_t first;
    pthread_t second;
    if (pthread_create(&first, 0, worker, 0) != 0)
        return 1;
    if (pthread_create(&second, 0, worker, 0) != 0)
        return 2;
    pthread_exit(0);
}
"""

MAIN_THREAD_EXITS_THEN_SPAWNS_SOURCE = r"""
#define _DEFAULT_SOURCE
#include <pthread.h>
#include <unistd.h>

static void *leaf(void *unused) {
    (void)unused;
    usleep(300000);
    return 0;
}

static void *survivor(void *unused) {
    (void)unused;
    pthread_t first;
    pthread_t second;

    usleep(100000);
    if (pthread_create(&first, 0, leaf, 0) != 0)
        return (void *)1;
    if (pthread_create(&second, 0, leaf, 0) != 0)
        return (void *)2;

    pthread_join(first, 0);
    pthread_join(second, 0);
    return 0;
}

int main(void) {
    pthread_t worker;
    if (pthread_create(&worker, 0, survivor, 0) != 0)
        return 1;
    pthread_exit(0);
}
"""

TIMEOUT_SOURCE = r"""
int main(void) {
    for (;;) { }
}
"""


@unittest.skipUnless(
    os.environ.get("RUNNER_DOCKER_TESTS") == "1",
    "Docker/eBPF integration requires RUNNER_DOCKER_TESTS=1",
)
class TaskTrackerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.docker_client = docker.from_env()
        cls.addClassCleanup(cls.docker_client.close)
        cls.docker_client.ping()
        cls.image_id = pin_execution_image(cls.docker_client)
        if platform.system() != "Linux":
            raise AssertionError("Enabled Docker/eBPF integration requires Linux")
        for required in (
            Path("/sys/kernel/btf/vmlinux"),
            Path("/sys/fs/cgroup/cgroup.controllers"),
            settings.task_tracker_socket,
        ):
            if not required.exists():
                raise AssertionError(f"missing enabled integration dependency: {required}")
        TaskTrackerClient.from_settings(settings).health()

    def execute(
        self,
        source: str,
        *,
        pids_limit: int = 32,
        timeout_ms: int = 3000,
    ):
        request = RunnerRequest(
            job_id=uuid4(),
            language=RunnerLanguage.C,
            code=source,
            policy=PolicyLimits(
                timeout_ms=timeout_ms,
                memory_limit_mb=64,
                pids_limit=pids_limit,
                cpu_bandwidth=1.0,
                cpu_time_limit_ms=2000,
            ),
            created_at=datetime.now(timezone.utc),
        )
        with (
            patch.object(settings, "execution_cgroup_enabled", True),
            patch.object(settings, "task_tracker_enabled", True),
        ):
            return execute_job(request)

    def assert_snapshot(
        self,
        response,
        user_tasks: int,
        processes: int,
        threads: int,
    ) -> None:
        self.assertEqual(response.status, RunnerStatus.SUCCESS)
        self.assertIsNotNone(response.resource_usage)
        self.assertIsNotNone(response.resource_usage.pids_peak)
        self.assertEqual(
            response.resource_usage.user_task_peak,
            user_tasks,
        )
        self.assertEqual(
            response.resource_usage.process_at_user_task_peak,
            processes,
        )
        self.assertEqual(
            response.resource_usage.thread_at_user_task_peak,
            threads,
        )

    def test_single_task_snapshot_is_stable_across_30_runs(self) -> None:
        for _ in range(30):
            self.assert_snapshot(self.execute(SINGLE_TASK_SOURCE), 1, 1, 0)

    def test_immediate_exit_has_persisted_exec_boundaries(self) -> None:
        real_tracker = TaskTrackerClient.from_settings(settings)
        observed_snapshots = []

        class RecordingTracker:
            def register_cgroup(
                self,
                run_id,
                cgroup_id,
                initial_task_count,
            ):
                return real_tracker.register_cgroup(
                    run_id,
                    cgroup_id,
                    initial_task_count,
                )

            def register_root(self, run_id, root_tid):
                return real_tracker.register_root(run_id, root_tid)

            def snapshot(self, run_id):
                snapshot = real_tracker.snapshot(run_id)
                observed_snapshots.append(snapshot)
                return snapshot

            def remove(self, run_id):
                return real_tracker.remove(run_id)

        with patch(
            "runner.pipeline.executor.TaskTrackerClient.from_settings",
            return_value=RecordingTracker(),
        ):
            response = self.execute(IMMEDIATE_EXIT_SOURCE)

        self.assertEqual(response.status, RunnerStatus.SUCCESS)
        self.assertIsNotNone(response.resource_usage)
        self.assertIsNotNone(response.resource_usage.wall_time_ms)
        self.assertGreaterEqual(response.resource_usage.wall_time_ms, 0)
        completed = [
            snapshot
            for snapshot in observed_snapshots
            if snapshot.exec_start_ns > 0 and snapshot.exec_end_ns > 0
        ]
        self.assertTrue(completed)
        self.assertGreaterEqual(
            completed[-1].exec_end_ns,
            completed[-1].exec_start_ns,
        )

    def test_sleep_wall_time_starts_at_exec(self) -> None:
        response = self.execute(SLEEP_SOURCE)

        self.assertEqual(response.status, RunnerStatus.SUCCESS)
        self.assertGreaterEqual(response.resource_usage.wall_time_ms, 450)
        self.assertLess(response.resource_usage.wall_time_ms, 1000)

    def test_timeout_uses_exec_start_deadline(self) -> None:
        response = self.execute(TIMEOUT_SOURCE, timeout_ms=1000)

        self.assertEqual(response.status, RunnerStatus.BLOCKED)
        self.assertEqual(response.reason_code, RunnerReasonCode.TIME_LIMIT)
        self.assertGreaterEqual(response.resource_usage.wall_time_ms, 950)
        self.assertLess(response.resource_usage.wall_time_ms, 2000)

    def test_last_fork_child_exit_sets_wall_end(self) -> None:
        response = self.execute(FORK_CHILD_SOURCE)

        self.assertEqual(response.status, RunnerStatus.SUCCESS)
        self.assertGreaterEqual(response.resource_usage.wall_time_ms, 450)
        self.assertLess(response.resource_usage.wall_time_ms, 1000)

    def test_child_exec_does_not_overwrite_root_exec_start(self) -> None:
        response = self.execute(CHILD_EXEC_SOURCE)

        self.assertEqual(response.status, RunnerStatus.SUCCESS)
        self.assertGreaterEqual(response.resource_usage.wall_time_ms, 650)
        self.assertLess(response.resource_usage.wall_time_ms, 1200)

    def test_gate_preparation_delay_is_excluded_from_wall_time(self) -> None:
        real_release = execution_module.release_start_gate

        def delayed_release(container) -> None:
            time.sleep(0.5)
            real_release(container)

        with patch(
            "runner.pipeline.execution.release_start_gate",
            side_effect=delayed_release,
        ):
            response = self.execute(SINGLE_TASK_SOURCE)

        self.assertEqual(response.status, RunnerStatus.SUCCESS)
        self.assertGreaterEqual(response.resource_usage.wall_time_ms, 150)
        self.assertLess(response.resource_usage.wall_time_ms, 500)

    def test_thread_barrier_snapshot(self) -> None:
        self.assert_snapshot(self.execute(THREAD_BARRIER_SOURCE), 16, 1, 15)

    def test_three_processes_and_twelve_additional_threads(self) -> None:
        self.assert_snapshot(
            self.execute(MIXED_PROCESS_THREAD_SOURCE),
            15,
            3,
            12,
        )

    def test_pids_limit_remains_cgroup_policy_source(self) -> None:
        response = self.execute(PIDS_LIMIT_SOURCE, pids_limit=32)

        self.assertEqual(response.status, RunnerStatus.BLOCKED)
        self.assertEqual(response.reason_code, RunnerReasonCode.PIDS_LIMIT)
        self.assertLessEqual(response.resource_usage.pids_peak, 32)

    def test_short_lived_threads_are_not_missed(self) -> None:
        for _ in range(30):
            self.assert_snapshot(self.execute(SHORT_THREADS_SOURCE), 2, 1, 1)

    def test_short_lived_processes_are_not_missed(self) -> None:
        for _ in range(30):
            self.assert_snapshot(self.execute(SHORT_PROCESSES_SOURCE), 2, 2, 0)

    def test_main_thread_exit_keeps_process_identity(self) -> None:
        self.assert_snapshot(self.execute(MAIN_THREAD_EXITS_SOURCE), 3, 1, 2)

    def test_main_thread_exit_then_new_threads_keeps_snapshot_consistent(
        self,
    ) -> None:
        self.assert_snapshot(
            self.execute(MAIN_THREAD_EXITS_THEN_SPAWNS_SOURCE),
            3,
            1,
            2,
        )

    def test_timeout_cleanup_does_not_pollute_next_run(self) -> None:
        blocked = self.execute(TIMEOUT_SOURCE, timeout_ms=100)
        self.assertEqual(blocked.status, RunnerStatus.BLOCKED)
        self.assertEqual(blocked.reason_code, RunnerReasonCode.TIME_LIMIT)

        self.assert_snapshot(self.execute(SINGLE_TASK_SOURCE), 1, 1, 0)

    def test_concurrent_runs_do_not_mix_metrics(self) -> None:
        sources = [SINGLE_TASK_SOURCE, THREAD_BARRIER_SOURCE] * 5
        with ThreadPoolExecutor(max_workers=10) as executor:
            responses = list(executor.map(self.execute, sources))

        expected = [(1, 1, 0), (16, 1, 15)] * 5
        for response, snapshot in zip(responses, expected):
            self.assert_snapshot(response, *snapshot)


if __name__ == "__main__":
    unittest.main()
