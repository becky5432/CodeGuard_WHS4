import os
import platform
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
from runner.pipeline.executor import execute_job


SINGLE_TASK_SOURCE = r"""
#include <unistd.h>

int main(void) {
    usleep(200000);
    return 0;
}
"""

THREAD_BARRIER_SOURCE = r"""
#define _GNU_SOURCE
#include <pthread.h>

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


class TaskTrackerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if os.getenv("CODEGUARD_EBPF_INTEGRATION") != "1":
            raise unittest.SkipTest("CODEGUARD_EBPF_INTEGRATION=1 required")
        if platform.system() != "Linux":
            raise unittest.SkipTest("Linux required")
        for required in (
            Path("/sys/kernel/btf/vmlinux"),
            Path("/sys/fs/cgroup/cgroup.controllers"),
            settings.task_tracker_socket,
        ):
            if not required.exists():
                raise unittest.SkipTest(f"missing integration dependency: {required}")
        try:
            cls.docker_client = docker.from_env()
            cls.docker_client.ping()
            cls.docker_client.images.get(settings.cpp_image)
        except docker.errors.DockerException as exc:
            raise unittest.SkipTest(f"Docker integration unavailable: {exc}")

        try:
            TaskTrackerClient.from_settings(settings).health()
        except Exception as exc:
            raise unittest.SkipTest(f"task tracker unavailable: {exc}")

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
