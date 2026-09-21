"""실제 Docker Engine과 settings.cpp_image가 필요한 파일시스템 통합 테스트.

기본 이미지 준비: docker build -t codeguard-cpp:dev -f runner/container/cpp/Dockerfile .
실행: RUNNER_DOCKER_TESTS=1 python -m unittest runner.tests.test_filesystem_integration -v
활성화한 경우 Docker 연결/이미지 오류를 건너뛰지 않고 실패로 보고한다.
PID 판정 테스트는 Docker 호스트의 /proc 및 cgroup v2 접근이 필요하다.
보호 구조와 실행 환경: runner/security/FILESYSTEM_DETECTION.md
"""

import os
import unittest
from uuid import uuid4

from runner.config import settings
from runner.models.result import RunnerReasonCode, RunnerStage, RunnerStatus
from runner.pipeline.classifier import classify_execution
from runner.pipeline.compiler import (
    compile_source,
    create_compile_container,
    get_docker_client,
)
from runner.pipeline.execution import (
    ExecutionResult,
    create_execution_container,
    execute_program,
)
from runner.pipeline.workspace import create_workspace, remove_workspace
from runner.security.filesystem_trace import TRACE_DIRECTORY, TRACE_PATH


@unittest.skipUnless(
    os.environ.get("RUNNER_DOCKER_TESTS") == "1",
    "실제 Docker 통합 테스트: RUNNER_DOCKER_TESTS=1 필요",
)
class FilesystemIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = get_docker_client()
        cls.addClassCleanup(cls.client.close)
        cls.client.images.get(settings.cpp_image)

    def setUp(self) -> None:
        self.workspace = create_workspace(self.client, uuid4())
        self.addCleanup(remove_workspace, self.client, self.workspace)

    def _compile_and_execute(
        self, code: str, stdin: str = "", language: str = "C",
        timeout_ms: int = 3000, memory_limit_mb: int = 128,
        pids_limit: int = 32, output_limit_bytes: int = 1024 * 1024,
        validate_limits: bool = True, allow_system_error: bool = False,
    ) -> ExecutionResult:
        compile_container = create_compile_container(
            self.client, self.workspace, language,
        )
        self.addCleanup(compile_container.remove, force=True)
        compiled = compile_source(
            compile_container, self.workspace, language, code, stdin=stdin,
        )
        self.assertTrue(compiled.success, compiled.stderr)
        self.assertTrue(compiled.artifact_ready)
        compile_container.reload()
        self.assertFalse(compile_container.attrs["HostConfig"]["ReadonlyRootfs"])
        compile_mount = next(
            mount for mount in compile_container.attrs["Mounts"]
            if mount["Destination"] == "/workspace"
        )
        self.assertTrue(compile_mount["RW"])

        run_id = uuid4()
        container = create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin=stdin,
            job_id=self.workspace.job_id,
            run_id=run_id,
            memory_limit_mb=memory_limit_mb,
            cpu_bandwidth=1.0,
            pids_limit=pids_limit,
        )
        self.addCleanup(container.remove, force=True, v=True)
        container.reload()
        host_config = container.attrs["HostConfig"]
        self.assertTrue(host_config["ReadonlyRootfs"])
        self.assertFalse(host_config.get("Tmpfs"))
        self.assertTrue(any(m["Destination"] == "/run/codeguard-trace" and m["RW"] for m in container.attrs["Mounts"]))
        execution_mount = next(
            mount for mount in container.attrs["Mounts"]
            if mount["Destination"] == "/workspace"
        )
        self.assertFalse(execution_mount["RW"])
        self.assertEqual(host_config["NetworkMode"], "none")
        self.assertEqual(container.attrs["Config"]["User"], "0:0")
        self.assertEqual(host_config["CapDrop"], ["ALL"])
        self.assertEqual(set(host_config["CapAdd"]), {"SYS_PTRACE", "SETUID", "SETGID"})
        self.assertIn("no-new-privileges=true", host_config["SecurityOpt"])
        self.assertEqual(host_config["Memory"], memory_limit_mb * 1024 * 1024)
        self.assertEqual(host_config["MemorySwap"], memory_limit_mb * 1024 * 1024)
        self.assertEqual(host_config["NanoCpus"], 1_000_000_000)
        self.assertEqual(host_config["PidsLimit"], pids_limit)

        result = execute_program(
            container, self.workspace.job_id, run_id, timeout_ms=timeout_ms,
            output_limit_bytes=output_limit_bytes,
        )
        if not allow_system_error:
            self.assertIsNone(result.system_error, result.system_error)
        if validate_limits:
            self.assertFalse(result.timed_out)
            self.assertFalse(result.oom_killed)
            self.assertFalse(result.pids_limit_exceeded)
            self.assertFalse(result.output_limit_exceeded)
        return result

    def test_c_hello_succeeds_with_read_only_filesystem(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                printf("hello\n");
                return 0;
            }
        ''')

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "hello\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_cpp_hello_succeeds_with_read_only_filesystem(self) -> None:
        result = self._compile_and_execute(r'''
            #include <iostream>
            int main() {
                std::cout << "hello C++\n";
                return 0;
            }
        ''', language="CPP")

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "hello C++\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_stdin_remains_readable(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                int value;
                if (scanf("%d", &value) != 1) return 1;
                printf("%d\n", value * 2);
                return 0;
            }
        ''', stdin="21\n")

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "42\n")
        self.assertEqual(result.stderr, "")
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_user_read_only_message_is_not_a_violation(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                puts("Read-only file system");
                puts("openat(AT_FDCWD, \"/etc/test\", O_WRONLY|O_CREAT, 0666) = -1 EROFS");
                fputs("Read-only file system\n", stderr);
                return 0;
            }
        ''')

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Read-only file system\n", result.stdout)
        self.assertIn("= -1 EROFS", result.stdout)
        self.assertEqual(result.stderr, "Read-only file system\n")
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def _assert_write_is_read_only(self, path: str) -> None:
        # 이 프로그램의 exit code는 errno 검증용이며 Runner 감지 규칙이 아니다.
        result = self._compile_and_execute(r'''
            #include <errno.h>
            #include <stdio.h>
            int main(void) {
                FILE *fp = fopen("TEST_PATH", "w");
                if (fp == NULL) {
                    int error = errno;
                    fprintf(stderr, "errno=%d\n", error);
                    return error == EROFS ? 1 : 2;
                }
                fclose(fp);
                return 0;
            }
        '''.replace("TEST_PATH", path))

        self.assertEqual(
            result.exit_code, 1,
            f"{path}: expected EROFS (exit 1); "
            f"exit={result.exit_code}, stderr={result.stderr!r}",
        )
        classified = classify_execution(result)
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(classified.status, RunnerStatus.BLOCKED)
        self.assertEqual(classified.reason_code, RunnerReasonCode.FILESYSTEM_LIMIT)
        self.assertEqual(classified.stage, RunnerStage.EXECUTE)

    def test_rootfs_write_fails_with_erofs(self) -> None:
        self._assert_write_is_read_only("/etc/codeguard_test")

    def test_workspace_write_fails_with_erofs(self) -> None:
        self._assert_write_is_read_only("/workspace/codeguard_test")

    def test_tmp_write_fails_with_erofs(self) -> None:
        self._assert_write_is_read_only("/tmp/codeguard_test")


    def test_normal_read_succeeds(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                FILE *f = fopen("/etc/hosts", "r");
                if (!f) return 1;
                int c = fgetc(f);
                fclose(f);
                return c == EOF;
            }
        ''')
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)
        self.assertFalse(result.filesystem_limit_exceeded)

    def _assert_mutation_detected(self, operation: str) -> None:
        result = self._compile_and_execute('''
            #include <stdio.h>
            #include <unistd.h>
            #include <sys/stat.h>
            int main(void) { OPERATION; return 0; }
        '''.replace("OPERATION", operation))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(classify_execution(result).status, RunnerStatus.BLOCKED)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.FILESYSTEM_LIMIT)

    def test_unlink_is_detected_even_when_exit_zero(self) -> None:
        self._assert_mutation_detected('unlink("/workspace/main")')

    def test_rename_is_detected(self) -> None:
        self._assert_mutation_detected('rename("/workspace/main", "/workspace/renamed")')

    def test_mkdir_is_detected(self) -> None:
        self._assert_mutation_detected('mkdir("/workspace/test", 0700)')

    def test_child_write_is_detected(self) -> None:
        self._assert_mutation_detected('if (fork() == 0) { mkdir("/workspace/child", 0700); _exit(0); } else { sleep(1); }')

    def test_sigsegv_is_runtime_error(self) -> None:
        result = self._compile_and_execute('#include <signal.h>\nint main(void) { raise(SIGSEGV); }')
        self.assertFalse(result.timed_out)
        self.assertEqual(result.exit_code, 139)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.RUNTIME_ERROR)

    def test_timeout_still_kills_container(self) -> None:
        result = self._compile_and_execute('int main(void) { for (;;) {} }', timeout_ms=300, validate_limits=False)
        self.assertTrue(result.timed_out)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.TIME_LIMIT)

    def test_output_limit_counts_only_user_output(self) -> None:
        result = self._compile_and_execute('#include <stdio.h>\nint main(void) { for (;;) puts("0123456789"); }', output_limit_bytes=1000, validate_limits=False)
        self.assertTrue(result.output_limit_exceeded)
        self.assertEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 1000)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.OUTPUT_LIMIT)

    def test_trace_does_not_consume_user_output_budget(self) -> None:
        result = self._compile_and_execute('#include <stdio.h>\nint main(void) { puts("ok"); return 0; }', output_limit_bytes=3)
        self.assertEqual(result.stdout, "ok\n")
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_memory_limit_remains_enforced(self) -> None:
        result = self._compile_and_execute('''
            #include <stdlib.h>
            #include <string.h>
            int main(void) {
                for (;;) {
                    volatile unsigned char *p = malloc(1024 * 1024);
                    if (!p) continue;
                    for (int i = 0; i < 1024 * 1024; ++i) p[i] = 1;
                }
            }
        ''', memory_limit_mb=32, timeout_ms=3000, validate_limits=False)
        self.assertTrue(result.oom_killed)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.MEMORY_LIMIT)

    def _assert_trace_attack_denied(self, operation: str) -> None:
        code = r'''
            #define _POSIX_C_SOURCE 200809L
            #include <errno.h>
            #include <fcntl.h>
            #include <stdio.h>
            #include <unistd.h>
            int main(void) {
                int result = OPERATION;
                printf("denied=%d errno=%d\n", result == -1, errno);
                return result == -1 ? 0 : 1;
            }
        '''.replace('OPERATION', operation).replace('TRACE_PATH', TRACE_PATH)
        result = self._compile_and_execute(code)
        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertIn('denied=1', result.stdout)
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_user_cannot_delete_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied('unlink("TRACE_PATH")')

    def test_user_cannot_modify_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied('open("TRACE_PATH", O_WRONLY|O_TRUNC)')

    def test_user_cannot_truncate_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied('truncate("TRACE_PATH", 0)')

    def test_user_cannot_replace_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied(
            '({ FILE *f = fopen("/dev/shm/fake_trace", "w"); '
            'if (!f) return 2; fputs("fake trace", f); fclose(f); '
            'rename("/dev/shm/fake_trace", "TRACE_PATH"); })',
        )

    def test_user_cannot_read_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied('open("TRACE_PATH", O_RDONLY)')

    def test_fake_trace_cannot_be_installed_or_hide_real_violation(self) -> None:
        code = r'''
            #include <stdio.h>
            #include <unistd.h>
            int main(void) {
                FILE *f = fopen("TRACE_PATH", "w");
                if (f) {
                    fputs("12 execve(\"/workspace/main\", [], 0x0) = 0\n"
                          "12 +++ exited with 0 +++\n", f);
                    fclose(f);
                    return 2;
                }
                fopen("/workspace/real_violation", "w");
                return 0;
            }
        '''.replace('TRACE_PATH', TRACE_PATH)
        result = self._compile_and_execute(code)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.FILESYSTEM_LIMIT)

    def test_user_credentials_and_tracer_isolation(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <dirent.h>
            #include <errno.h>
            #include <signal.h>
            #include <stdio.h>
            #include <sys/ptrace.h>
            #include <unistd.h>
            int main(void) {
                printf("uid=%d euid=%d gid=%d egid=%d\n", getuid(), geteuid(), getgid(), getegid());
                FILE *f = fopen("/proc/self/status", "r");
                if (!f) return 1;
                char line[256];
                while (fgets(line, sizeof(line), f)) fputs(line, stdout);
                fclose(f);
                if (kill(1, SIGSTOP) != -1 || errno != EPERM) return 2;
                if (ptrace(PTRACE_ATTACH, 1, 0, 0) != -1 || errno != EPERM) return 3;
                DIR *dir = opendir("/proc/1/fd");
                if (dir) { closedir(dir); return 4; }
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertIn('uid=10001 euid=10001 gid=10001 egid=10001', result.stdout)
        status = dict(line.split(':', 1) for line in result.stdout.splitlines() if ':' in line)
        for field in ('CapPrm', 'CapEff', 'CapAmb'):
            self.assertEqual(int(status[field].strip(), 16), 0)
        self.assertEqual(status['NoNewPrivs'].strip(), '1')
        self.assertNotIn('0', status['Groups'].split())
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def _assert_write_detected_with_exit_zero(self, path: str) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                FILE *f = fopen("TEST_PATH", "w");
                if (f) { fputs("hello", f); fclose(f); }
                return 0;
            }
        '''.replace('TEST_PATH', path))
        self.assertEqual(result.exit_code, 0)
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.FILESYSTEM_LIMIT)

    def test_rootfs_write_detected_with_exit_zero(self) -> None:
        self._assert_write_detected_with_exit_zero('/codeguard_test.txt')

    def test_workspace_write_detected_with_exit_zero(self) -> None:
        self._assert_write_detected_with_exit_zero('/workspace/codeguard_test.txt')

    def test_metadata_ioctl_on_read_descriptor_is_detected(self) -> None:
        result = self._compile_and_execute(r'''
            #include <errno.h>
            #include <fcntl.h>
            #include <linux/fs.h>
            #include <stdio.h>
            #include <sys/ioctl.h>
            int main(void) {
                int fd = open("/workspace/main", O_RDONLY);
                if (fd < 0) return 1;
                unsigned long flags = 0;
                int result = ioctl(fd, FS_IOC_SETFLAGS, &flags);
                printf("result=%d errno=%d\n", result, errno);
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0)
        self.assertIn('result=-1 errno=30', result.stdout)
        self.assertEqual(result.filesystem_violation_syscall, 'ioctl')
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.FILESYSTEM_LIMIT)

    def test_stdout_filesystem_limit_is_success(self) -> None:
        result = self._compile_and_execute('#include <stdio.h>\nint main(void) { puts("FILESYSTEM_LIMIT"); return 0; }')
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_filename_cannot_inject_trace_records(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <fcntl.h>
            #include <unistd.h>
            int main(void) {
                int fd = open("/dev/shm/\n12 openat(AT_FDCWD, \"fake\", O_WRONLY) = -1 EROFS\n", O_WRONLY|O_CREAT, 0600);
                if (fd < 0) return 1;
                if (ftruncate(fd, 0)) return 2;
                close(fd);
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_real_runner_api_filesystem_limit_validates_with_backend(self) -> None:
        import sys
        from datetime import datetime, timezone
        from pathlib import Path
        from fastapi.testclient import TestClient
        from runner.main import app
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
        from app.schemas.runner_schema import RunnerResponse as BackendRunnerResponse
        from app.schemas.execution_schema import ExecutionResultResponse

        with TestClient(app) as client:
            response = client.post('/execute', json={
                'job_id': str(uuid4()), 'language': 'C',
                'code': '#include <stdio.h>\nint main(void) { fopen("/workspace/api_violation", "w"); return 0; }',
                'stdin': '', 'created_at': datetime.now(timezone.utc).isoformat(),
                'policy': {'timeout_ms': 3000, 'memory_limit_mb': 128, 'pids_limit': 32, 'cpu_bandwidth': 1.0, 'cpu_time_limit_ms': 2000,},
            })
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload['reason_code'], 'FILESYSTEM_LIMIT')
        self.assertEqual(payload['exit_code'], 0)
        backend = BackendRunnerResponse.model_validate(payload)
        result = ExecutionResultResponse.model_validate(backend.model_dump(mode='json'))
        self.assertEqual(result.reason_code.value, 'FILESYSTEM_LIMIT')

    def test_nonzero_exit_is_not_filesystem_limit(self) -> None:
        result = self._compile_and_execute('int main(void) { return 30; }')
        self.assertEqual(result.exit_code, 30)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.RUNTIME_ERROR)

    def test_allowed_shared_memory_write_is_success(self) -> None:
        # Docker already provides /dev/shm as a writable tmpfs; /tmp stays RO.
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                FILE *f = fopen("/dev/shm/allowed", "w");
                if (!f) return 1;
                fputs("hello", f);
                fclose(f);
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0)
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_seccomp_cannot_forge_erofs_evidence(self) -> None:
        result = self._compile_and_execute(r'''
            #include <errno.h>
            #include <fcntl.h>
            #include <linux/filter.h>
            #include <linux/seccomp.h>
            #include <stddef.h>
            #include <sys/prctl.h>
            #include <sys/syscall.h>
            #include <stdio.h>
            int main(void) {
                struct sock_filter filter[] = {
                    BPF_STMT(BPF_LD|BPF_W|BPF_ABS, offsetof(struct seccomp_data, nr)),
                    BPF_JUMP(BPF_JMP|BPF_JEQ|BPF_K, SYS_openat, 0, 1),
                    BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ERRNO|EROFS),
                    BPF_STMT(BPF_RET|BPF_K, SECCOMP_RET_ALLOW)
                };
                struct sock_fprog program = {.len = 4, .filter = filter};
                if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program)) return 1;
                int fd = open("/dev/shm/allowed", O_WRONLY|O_CREAT, 0600);
                printf("fd=%d errno=%d\n", fd, errno);
                return 0;
            }
        ''', allow_system_error=True)
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIsNotNone(result.system_error)
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.INTERNAL_ERROR)


    def test_pid_limit_remains_enforced_with_tracer_overhead(self) -> None:
        result = self._compile_and_execute(r'''
            #include <errno.h>
            #include <stdio.h>
            #include <unistd.h>
            #include <sys/wait.h>
            int main(void) {
                int count = 0, denied = 0;
                for (int i = 0; i < 16; ++i) {
                    pid_t pid = fork();
                    if (pid == 0) { sleep(1); _exit(0); }
                    if (pid < 0) { denied = errno == EAGAIN; break; }
                    ++count;
                }
                while (wait(NULL) > 0) {}
                printf("count=%d denied=%d\n", count, denied);
                return denied && count <= 2 ? 0 : 1;
            }
        ''', pids_limit=4, validate_limits=False)
        self.assertTrue(result.pids_limit_exceeded)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.PIDS_LIMIT)


    def test_untraced_child_cannot_hide_read_only_write(self) -> None:
        result = self._compile_and_execute(r'''
            #define _GNU_SOURCE
            #include <sched.h>
            #include <errno.h>
            #include <fcntl.h>
            #include <stdio.h>
            #include <stdlib.h>
            #include <sys/wait.h>
            #include <unistd.h>
            static int child(void *unused) {
                int fd = open("/untraced-write", O_WRONLY|O_CREAT, 0600);
                int denied = fd == -1 && errno == EROFS;
                dprintf(1, "untraced_write_erofs=%d\n", denied);
                if (fd >= 0) close(fd);
                return denied ? 0 : 2;
            }
            int main(void) {
                char *stack = malloc(65536);
                if (!stack) return 3;
                int pid = clone(child, stack + 65536, CLONE_UNTRACED|SIGCHLD, NULL);
                if (pid < 0) return 4;
                int status;
                if (waitpid(pid, &status, 0) < 0) return 5;
                free(stack);
                return WIFEXITED(status) ? WEXITSTATUS(status) : 6;
            }
        ''', allow_system_error=True)
        self.assertEqual(result.exit_code, 0)
        self.assertIn('untraced_write_erofs=1', result.stdout)
        self.assertIsNotNone(result.system_error)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.INTERNAL_ERROR)


    def test_trace_overflow_is_internal_error(self) -> None:
        result = self._compile_and_execute(r'''
            #include <fcntl.h>
            #include <unistd.h>
            int main(void) {
                for (int i = 0; i < 100000; ++i) {
                    int fd = open("/etc/hosts", O_RDONLY);
                    if (fd >= 0) close(fd);
                }
                return 0;
            }
        ''', timeout_ms=10000, validate_limits=False, allow_system_error=True)
        self.assertIsNotNone(result.system_error)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.INTERNAL_ERROR)


if __name__ == "__main__":
    unittest.main()
