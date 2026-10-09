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
from unittest.mock import patch

from runner.config import settings
from runner.metrics.cgroup_scope import ExecutionCgroupScope, validate_docker_cgroup_driver
from runner.models.result import RunnerReasonCode, RunnerStatus
from runner.pipeline.classifier import classify_execution, classify_policy_violations
from runner.pipeline.compiler import get_docker_client
from runner.pipeline.execution import (
    ExecutionResult,
    create_execution_container,
    execute_program,
)
from runner.pipeline.workspace import create_workspace, remove_workspace
from runner.security.filesystem_trace import TRACE_PATH
from runner.tests.filesystem_helpers import pin_execution_image, prepare_and_compile


@unittest.skipUnless(
    os.environ.get("RUNNER_DOCKER_TESTS") == "1",
    "실제 Docker 통합 테스트: RUNNER_DOCKER_TESTS=1 필요",
)
class FilesystemIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = get_docker_client()
        cls.addClassCleanup(cls.client.close)
        cls.image_id = pin_execution_image(cls.client)

    def setUp(self) -> None:
        device_settings = patch.object(settings, "filesystem_device_paths", ())
        device_settings.start()
        self.addCleanup(device_settings.stop)
        self.workspace = create_workspace(self.client, uuid4())
        self.addCleanup(remove_workspace, self.client, self.workspace)

    def _compile_and_execute(
        self, code: str, stdin: str = "", language: str = "C",
        timeout_ms: int = 3000, memory_limit_mb: int = 128,
        pids_limit: int = 32, output_limit_bytes: int = 1024 * 1024,
        validate_limits: bool = True, allow_system_error: bool = False,
    ) -> ExecutionResult:
        policy, compile_container = prepare_and_compile(
            self, self.workspace, code, stdin, language,
        )
        compile_container.reload()
        self.assertFalse(compile_container.attrs["HostConfig"]["ReadonlyRootfs"])
        compile_mount = next(
            mount for mount in compile_container.attrs["Mounts"]
            if mount["Destination"] == "/workspace"
        )
        self.assertTrue(compile_mount["RW"])
        self.assertEqual(compile_mount["Name"], self.workspace.volume_name)

        run_id = uuid4()
        cgroup_scope = ExecutionCgroupScope.create(
            root=settings.execution_cgroup_root,
            run_id=run_id,
            driver=validate_docker_cgroup_driver(self.client),
        )
        # unittest cleanups run in reverse order: remove the container first,
        # then its cgroup, then the named workspace volumes.
        self.addCleanup(cgroup_scope.remove)
        container = create_execution_container(
            client=self.client,
            workspace=self.workspace,
            stdin=stdin,
            job_id=self.workspace.job_id,
            run_id=run_id,
            memory_limit_mb=memory_limit_mb,
            cpu_bandwidth=1.0,
            pids_limit=pids_limit,
            cgroup_scope=cgroup_scope,
            filesystem_policy=policy,
        )
        self.addCleanup(container.remove, force=True, v=True)
        container.reload()
        host_config = container.attrs["HostConfig"]
        self.assertTrue(host_config["ReadonlyRootfs"])
        self.assertFalse(host_config.get("Tmpfs"))
        self.assertTrue(any(m["Destination"] == "/run/codeguard-trace" and m["RW"] for m in container.attrs["Mounts"]))
        mounts = {mount["Destination"]: mount for mount in container.attrs["Mounts"]}
        self.assertEqual(mounts["/workspace"]["Name"], self.workspace.volume_name)
        self.assertTrue(mounts["/workspace"]["RW"])
        self.assertFalse(any(path.startswith("/workspace/") for path in mounts))
        self.assertEqual(container.attrs["Image"], self.image_id)
        self.assertEqual(host_config["NetworkMode"], settings.execution_network)
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
            cgroup_scope=cgroup_scope,
            filesystem_policy_id=policy.policy_id,
            filesystem_policy=policy,
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

    def test_exact_app_and_input_files_remain_readable(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                FILE *app = fopen("/workspace/main", "rb");
                FILE *source = fopen("/workspace/main.c", "r");
                FILE *input = fopen("/workspace/stdin", "r");
                if (!app || !source || !input) return 1;
                int value = 0;
                if (fgetc(app) != 0x7f || fgetc(source) == EOF ||
                    fscanf(input, "%d", &value) != 1 || value != 21) return 2;
                fclose(app); fclose(source); fclose(input);
                puts("app_source_input_readable=1");
                return 0;
            }
        ''', stdin="21\n")
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn('app_source_input_readable=1', result.stdout)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_relative_work_file_and_directory_lifecycle(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <stdio.h>
            #include <string.h>
            #include <sys/stat.h>
            #include <unistd.h>
            int main(void) {
                char cwd[128], data[32] = {0};
                if (!getcwd(cwd, sizeof(cwd)) || strcmp(cwd, "/workspace")) return 1;
                struct stat st;
                if (stat(".", &st)) { perror("stat work"); return 10; }
                if (getuid() != 10001 || getgid() != 10001 ||
                    st.st_uid != 10001 || st.st_gid != 10001 ||
                    (st.st_mode & 07777) != 0700) {
                    fprintf(stderr, "work owner=%u:%u mode=%o uid=%u gid=%u\n",
                            (unsigned)st.st_uid, (unsigned)st.st_gid,
                            (unsigned)(st.st_mode & 07777),
                            (unsigned)getuid(), (unsigned)getgid());
                    return 11;
                }
                FILE *f = fopen("result.txt", "w+");
                if (!f) { perror("fopen work"); return 2; }
                if (fputs("initial", f) == EOF) { perror("fputs work"); return 2; }
                rewind(f);
                if (!fgets(data, sizeof(data), f) || strcmp(data, "initial")) return 3;
                if (ftruncate(fileno(f), 0)) return 4;
                rewind(f);
                if (fputs("modified", f) == EOF || fclose(f)) return 5;
                if (mkdir("first", 0700) || mkdir("second", 0700)) return 6;
                if (rename("result.txt", "first/result.txt") ||
                    rename("first/result.txt", "second/result.txt")) return 7;
                f = fopen("second/result.txt", "r");
                if (!f || !fgets(data, sizeof(data), f) || strcmp(data, "modified")) return 8;
                if (fclose(f) || unlink("second/result.txt") ||
                    rmdir("first") || rmdir("second")) return 9;
                puts("work_lifecycle=1");
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0, (result.stdout, result.stderr))
        self.assertIn('work_lifecycle=1', result.stdout)
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_source_and_stdin_mutations_succeed(self) -> None:
        for path in ('/workspace/main.c', '/workspace/stdin'):
            with self.subTest(path=path):
                result = self._compile_and_execute(r'''#include <stdio.h>
                    int main(void) {
                        FILE *f = fopen("TEST_PATH", "w");
                        if (!f || fputs("modified", f) == EOF || fclose(f)) return 1;
                        return 0;
                    }'''.replace('TEST_PATH', path))
                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_proc_sys_and_dev_file_reads_are_denied(self) -> None:
        for path in ('/proc/self/status', '/proc/sys/kernel/hostname',
                     '/sys/kernel/uevent_seqnum', '/dev/null'):
            with self.subTest(path=path):
                result = self._compile_and_execute(r'''
                    #include <fcntl.h>
                    #include <stdio.h>
                    #include <unistd.h>
                    int main(void) {
                        int fd = open("TEST_PATH", O_RDONLY);
                        printf("denied=%d\n", fd == -1);
                        if (fd >= 0) close(fd);
                        return fd == -1 ? 0 : 1;
                    }
                '''.replace('TEST_PATH', path))
                self._assert_denied_result(result)

    def test_selected_devices_have_only_read_or_read_write_access(self) -> None:
        with patch.object(settings, "filesystem_device_paths", ("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom")):
            result = self._compile_and_execute(r'''
                #include <fcntl.h>
                #include <unistd.h>
                int main(void) {
                    const char *paths[] = {"/dev/zero", "/dev/random", "/dev/urandom"};
                    char data[8];
                    int fd = open("/dev/null", O_RDWR);
                    if (fd < 0 || write(fd, "ok", 2) != 2 || close(fd)) return 1;
                    for (unsigned i = 0; i < 3; i++) {
                        fd = open(paths[i], O_RDONLY | O_NONBLOCK);
                        if (fd < 0) return 2;
                        if (read(fd, data, sizeof(data)) < 0 || close(fd)) return 3;
                        fd = open(paths[i], O_WRONLY);
                        if (fd >= 0) { close(fd); return 4; }
                    }
                    return 0;
                }
            ''')
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_dev_null_write_is_denied(self) -> None:
        self._assert_write_is_read_only('/dev/null')

    def test_work_internal_hardlink_has_same_policy_semantics(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <errno.h>
            #include <stdio.h>
            #include <sys/stat.h>
            #include <unistd.h>
            int main(void) {
                FILE *f = fopen("original", "w");
                if (!f || fputs("x", f) == EOF || fclose(f)) return 1;
                if (mkdir("nested", 0700)) return 2;
                if (link("original", "nested/linked") == 0) {
                    struct stat a, b;
                    if (stat("original", &a) || stat("nested/linked", &b) ||
                        a.st_ino != b.st_ino || a.st_dev != b.st_dev) return 3;
                    f = fopen("nested/linked", "r");
                    if (!f || fgetc(f) != 'x' || fclose(f)) return 4;
                    if (unlink("nested/linked")) return 5;
                    puts("internal_link=allowed");
                } else {
                    /* Backing filesystems can lack hardlink support. Never
                     * weaken/replace the policy or skip the integration test. */
                    if (errno != EPERM && errno != EOPNOTSUPP && errno != EXDEV) return 6;
                    puts("internal_link=unsupported");
                }
                if (unlink("original") || rmdir("nested")) return 7;
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertRegex(result.stdout, r'internal_link=(allowed|unsupported)')
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_cross_boundary_rename_and_hardlink_are_denied(self) -> None:
        for operation in (
            'rename("/workspace/main", "/etc/moved")',
            'link("/workspace/main", "/etc/linked")',
            'rename("/workspace/stdin", "/tmp/moved")',
            'link("/workspace/stdin", "/tmp/linked")',
            '({ FILE *f = fopen("/workspace/local", "w"); '
            'if (!f || fclose(f)) return 2; '
            'rename("/workspace/local", "/etc/moved"); })',
            '({ FILE *f = fopen("/workspace/local", "w"); '
            'if (!f || fclose(f)) return 2; '
            'link("/workspace/local", "/etc/linked"); })',
        ):
            with self.subTest(operation=operation):
                self._assert_mutation_detected(operation)

    def test_work_symlink_fifo_and_socket_creation_succeed(self) -> None:
        for operation in (
            'symlink("/workspace/main", "/workspace/link")',
            'mkfifo("/workspace/fifo", 0600)',
            '({ int fd = socket(AF_UNIX, SOCK_STREAM, 0); if (fd < 0) return 2; '
            'struct sockaddr_un address = {.sun_family = AF_UNIX}; '
            'strcpy(address.sun_path, "/workspace/socket"); '
            'int rc = bind(fd, (struct sockaddr *)&address, sizeof(address)); '
            'close(fd); rc; })',
        ):
            with self.subTest(operation=operation):
                result = self._compile_and_execute(r'''
                    #define _POSIX_C_SOURCE 200809L
                    #include <stdio.h>
                    #include <string.h>
                    #include <sys/socket.h>
                    #include <sys/stat.h>
                    #include <sys/un.h>
                    #include <unistd.h>
                    int main(void) {
                        int rc = OPERATION;
                        printf("created=%d\n", rc == 0);
                        return rc == 0 ? 0 : 1;
                    }
                '''.replace('OPERATION', operation))
                self.assertEqual(result.exit_code, 0, result.stderr)
                self.assertIn("created=1", result.stdout)
                self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_shell_execution_is_denied(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <stdio.h>
            #include <sys/wait.h>
            #include <unistd.h>
            int main(void) {
                pid_t child = fork();
                if (child < 0) return 1;
                if (child == 0) {
                    execl("/bin/sh", "sh", "-c", "exit 42", (char *)0);
                    dprintf(1, "shell_denied=1\n"); _exit(0);
                }
                int status;
                if (waitpid(child, &status, 0) < 0) return 2;
                return WIFEXITED(status) ? WEXITSTATUS(status) : 3;
            }
        ''')
        self._assert_denied_result(result, 'shell_denied=1')

    def test_direct_work_binary_execution_succeeds(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <fcntl.h>
            #include <stdio.h>
            #include <sys/wait.h>
            #include <unistd.h>
            int main(int argc, char **argv) {
                (void)argv;
                if (argc > 1) { puts("work_exec_succeeded=1"); return 0; }
                int src = open("/workspace/main", O_RDONLY);
                int dst = open("/workspace/copy", O_WRONLY|O_CREAT|O_EXCL, 0700);
                if (src < 0 || dst < 0) return 1;
                char buffer[4096]; ssize_t count;
                while ((count = read(src, buffer, sizeof(buffer))) > 0)
                    if (write(dst, buffer, count) != count) return 2;
                if (count < 0 || close(src) || close(dst)) return 3;
                pid_t child = fork();
                if (child < 0) return 4;
                if (child == 0) {
                    execl("/workspace/copy", "copy", "child", (char *)0);
                    _exit(42);
                }
                int status;
                if (waitpid(child, &status, 0) < 0) return 5;
                return WIFEXITED(status) ? WEXITSTATUS(status) : 6;
            }
        ''')
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn('work_exec_succeeded=1', result.stdout)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_fork_and_thread_inherit_filesystem_restrictions(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <fcntl.h>
            #include <pthread.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <sys/wait.h>
            #include <unistd.h>
            static int probe(void) {
                int fd = open("/etc/hosts", O_RDONLY);
                if (fd >= 0) { close(fd); return 1; }
                fd = open("/workspace/stdin", O_RDONLY);
                if (fd < 0) return 2;
                close(fd); return 0;
            }
            static void *worker(void *unused) {
                (void)unused;
                return (void *)(intptr_t)probe();
            }
            int main(void) {
                if (probe()) return 1;
                pid_t child = fork();
                if (child < 0) return 2;
                if (child == 0) _exit(probe());
                int status;
                if (waitpid(child, &status, 0) < 0 || !WIFEXITED(status) || WEXITSTATUS(status)) return 3;
                pthread_t thread; void *value;
                if (pthread_create(&thread, NULL, worker, NULL) ||
                    pthread_join(thread, &value) || value != NULL) return 4;
                puts("fork_thread_inherited=1");
                return 0;
            }
        ''')
        self._assert_denied_result(result, 'fork_thread_inherited=1')

    def test_fork_and_thread_mutation_denials_are_reported(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <errno.h>
            #include <fcntl.h>
            #include <pthread.h>
            #include <stdint.h>
            #include <stdio.h>
            #include <sys/wait.h>
            #include <unistd.h>
            static int probe(const char *path) {
                int fd = open(path, O_WRONLY|O_CREAT|O_EXCL, 0600);
                int error = errno;
                int denied = fd < 0 && (error == EACCES || error == EROFS);
                printf("path=%s errno=%d denied=%d\n", path, error, denied);
                if (fd >= 0) { close(fd); unlink(path); }
                return denied;
            }
            static void *worker(void *unused) {
                (void)unused;
                return (void *)(intptr_t)!probe("/tmp/codeguard-thread-test");
            }
            int main(void) {
                pid_t child = fork();
                if (child < 0) return 1;
                if (child == 0) _exit(probe("/tmp/codeguard-child-test") ? 0 : 2);
                int status;
                if (waitpid(child, &status, 0) < 0 || !WIFEXITED(status) || WEXITSTATUS(status)) return 3;
                pthread_t thread; void *value;
                if (pthread_create(&thread, NULL, worker, NULL) ||
                    pthread_join(thread, &value) || value != NULL) return 4;
                puts("fork_thread_mutation_denied=1");
                return 0;
            }
        ''')
        self._assert_denied_result(result, 'fork_thread_mutation_denied=1')
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(classify_policy_violations(result), [RunnerReasonCode.FILESYSTEM_LIMIT])

    def test_user_read_only_message_is_not_a_violation(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                puts("Read-only file system");
                puts("openat(AT_FDCWD, \"/etc/test\", O_WRONLY|O_CREAT, 0666) = -1 EROFS");
                puts("EACCES EPERM FILESYSTEM_LIMIT");
                fputs("Read-only file system\n", stderr);
                return 0;
            }
        ''')

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Read-only file system\n", result.stdout)
        self.assertIn("= -1 EROFS", result.stdout)
        self.assertEqual(result.stderr, "Read-only file system\n")
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def _assert_denied_result(self, result: ExecutionResult, marker="denied=1") -> None:
        self.assertEqual(result.exit_code, 0, (result.stdout, result.stderr))
        self.assertIn(marker, result.stdout)
        classified = classify_execution(result)
        self.assertEqual(classified.status, RunnerStatus.SUCCESS)
        self.assertIsNone(classified.reason_code)
        self.assertEqual(classify_policy_violations(result),
                         [RunnerReasonCode.FILESYSTEM_LIMIT] if result.filesystem_limit_exceeded else [])

    def _assert_write_is_read_only(self, path: str) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                FILE *fp = fopen("TEST_PATH", "w");
                printf("denied=%d\n", fp == NULL);
                if (fp) fclose(fp);
                return fp == NULL ? 0 : 1;
            }
        '''.replace("TEST_PATH", path))
        self._assert_denied_result(result)
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(classify_policy_violations(result), [RunnerReasonCode.FILESYSTEM_LIMIT])

    def test_rootfs_write_is_denied(self) -> None:
        self._assert_write_is_read_only("/etc/codeguard_test")

    def test_workspace_root_write_succeeds(self) -> None:
        result = self._compile_and_execute('#include <stdio.h>\nint main(void) { FILE *f = fopen("/workspace/new", "w"); return !f || fclose(f); }')
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_tmp_write_is_denied(self) -> None:
        self._assert_write_is_read_only("/tmp/codeguard_test")

    def test_workspace_unix_permission_error_is_not_filesystem_limit(self) -> None:
        result = self._compile_and_execute(r'''
            #include <errno.h>
            #include <fcntl.h>
            #include <stdio.h>
            #include <sys/stat.h>
            #include <unistd.h>
            int main(void) {
                int fd = open("/workspace/no_permission", O_CREAT|O_WRONLY, 0600);
                if (fd < 0 || close(fd) || chmod("/workspace/no_permission", 0000)) return 1;
                errno = 0;
                fd = open("/workspace/no_permission", O_WRONLY);
                int denied = fd == -1 && errno == EACCES;
                if (fd >= 0) close(fd);
                printf("unix_permission_denied=%d\n", denied);
                return denied ? 0 : 2;
            }
        ''')
        self.assertEqual(result.exit_code, 0, result.stderr)
        self.assertIn("unix_permission_denied=1", result.stdout)
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_policy_violations(result), [])

    def test_workspace_symlink_to_external_write_is_detected(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <errno.h>
            #include <fcntl.h>
            #include <stdio.h>
            #include <unistd.h>
            int main(void) {
                if (symlink("/etc", "/workspace/outside")) return 2;
                errno = 0;
                int fd = open("/workspace/outside/codeguard-test", O_WRONLY|O_CREAT, 0600);
                int error = errno;
                int denied = fd < 0 && (error == EACCES || error == EROFS);
                if (fd >= 0) close(fd);
                printf("denied=%d errno=%d\n", denied, error);
                return denied ? 0 : 3;
            }
        ''')
        self._assert_denied_result(result)
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(result.filesystem_violation_path, "/workspace/outside/codeguard-test")

    def test_external_unix_socket_bind_is_detected(self) -> None:
        result = self._compile_and_execute(r'''
            #include <errno.h>
            #include <stdio.h>
            #include <string.h>
            #include <sys/socket.h>
            #include <sys/un.h>
            #include <unistd.h>
            int main(void) {
                int sock = socket(AF_UNIX, SOCK_STREAM, 0);
                if (sock < 0) return 2;
                struct sockaddr_un address = {.sun_family = AF_UNIX};
                strcpy(address.sun_path, "/tmp/codeguard-bind-test");
                errno = 0;
                int rc = bind(sock, (struct sockaddr *)&address, sizeof(address));
                int error = errno;
                int denied = rc < 0 && (error == EACCES || error == EROFS);
                close(sock);
                printf("denied=%d errno=%d\n", denied, error);
                return denied ? 0 : 3;
            }
        ''')
        self._assert_denied_result(result)
        self.assertTrue(result.filesystem_limit_exceeded)
        self.assertEqual(result.filesystem_violation_syscall, "bind")

    def test_external_write_denial_and_nonzero_exit_keep_both_results(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                FILE *file = fopen("/etc/codeguard-test", "w");
                if (file) { fclose(file); return 2; }
                return 1;
            }
        ''')
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(classify_execution(result).status, RunnerStatus.ERROR)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.RUNTIME_ERROR)
        self.assertEqual(classify_policy_violations(result), [RunnerReasonCode.FILESYSTEM_LIMIT])


    def test_etc_hosts_read_is_denied(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            int main(void) {
                FILE *f = fopen("/etc/hosts", "r");
                printf("denied=%d\n", f == NULL);
                if (f) fclose(f);
                return f == NULL ? 0 : 1;
            }
        ''')
        self._assert_denied_result(result)

    def _assert_mutation_detected(self, operation: str, allowed=False) -> None:
        code = r'''
            #include <stdio.h>
            #include <unistd.h>
            #include <sys/stat.h>
            #include <sys/wait.h>
            int main(void) {
                int rc = OPERATION;
                printf("denied=%d\n", rc == -1);
                return rc == -1 ? 0 : 1;
            }
        '''.replace("OPERATION", operation)
        if allowed:
            code = code.replace("rc == -1", "rc == 0")
        result = self._compile_and_execute(code)
        if allowed:
            self.assertEqual(result.exit_code, 0, result.stderr)
            self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)
        else:
            self._assert_denied_result(result)

    def test_workspace_main_unlink_succeeds(self) -> None:
        self._assert_mutation_detected('unlink("/workspace/main")', allowed=True)

    def test_workspace_main_rename_succeeds(self) -> None:
        self._assert_mutation_detected('rename("/workspace/main", "/workspace/renamed")', allowed=True)

    def test_workspace_mkdir_succeeds(self) -> None:
        self._assert_mutation_detected('mkdir("/workspace/test", 0700)', allowed=True)

    def test_workspace_child_write_succeeds(self) -> None:
        self._assert_mutation_detected(
            '({ pid_t child = fork(); if (child < 0) return 2; '
            'if (child == 0) _exit(mkdir("/workspace/child", 0700) == 0 ? 0 : 1); '
            'int status; if (waitpid(child, &status, 0) < 0) return 3; '
            'WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1; })', allowed=True
        )

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
        self._assert_denied_result(result)

    def test_user_cannot_delete_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied('unlink("TRACE_PATH")')

    def test_user_cannot_modify_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied('open("TRACE_PATH", O_WRONLY|O_TRUNC)')

    def test_user_cannot_truncate_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied('truncate("TRACE_PATH", 0)')

    def test_user_cannot_replace_filesystem_trace(self) -> None:
        self._assert_trace_attack_denied(
            '({ FILE *f = fopen("/workspace/fake_trace", "w"); '
            'if (!f) return 2; fputs("fake trace", f); fclose(f); '
            'rename("/workspace/fake_trace", "TRACE_PATH"); })',
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
                f = fopen("/real_violation", "w");
                printf("denied=%d\n", f == NULL);
                if (f) fclose(f);
                return f == NULL ? 0 : 1;
            }
        '''.replace('TRACE_PATH', TRACE_PATH)
        result = self._compile_and_execute(code)
        self._assert_denied_result(result)

    def test_user_credentials_and_tracer_isolation(self) -> None:
        result = self._compile_and_execute(r'''
            #define _GNU_SOURCE
            #include <dirent.h>
            #include <errno.h>
            #include <linux/capability.h>
            #include <signal.h>
            #include <stdio.h>
            #include <sys/prctl.h>
            #include <sys/ptrace.h>
            #include <sys/syscall.h>
            #include <unistd.h>
            int main(void) {
                printf("uid=%d euid=%d gid=%d egid=%d\n", getuid(), geteuid(), getgid(), getegid());
                struct __user_cap_header_struct header = {
                    .version = _LINUX_CAPABILITY_VERSION_3, .pid = 0
                };
                struct __user_cap_data_struct caps[2] = {{0}};
                if (syscall(SYS_capget, &header, caps)) return 1;
                for (int i = 0; i < 2; ++i)
                    if (caps[i].effective || caps[i].permitted || caps[i].inheritable) return 5;
                if (prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1) return 6;
                for (int cap = 0; cap <= CAP_LAST_CAP; ++cap)
                    if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, cap, 0, 0) != 0) return 7;
                gid_t groups[16];
                int count = getgroups(16, groups);
                if (count < 0) return 8;
                for (int i = 0; i < count; ++i) if (groups[i] == 0) return 9;
                if (kill(1, SIGSTOP) != -1 || errno != EPERM) return 2;
                if (ptrace(PTRACE_ATTACH, 1, 0, 0) != -1 || errno != EPERM) return 3;
                DIR *dir = opendir("/proc/1/fd");
                if (dir) { closedir(dir); return 4; }
                puts("caps=empty nnp=1 proc_denied=1 tracer_isolated=1");
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0, result.stdout)
        self.assertIn('uid=10001 euid=10001 gid=10001 egid=10001', result.stdout)
        self.assertIn('caps=empty nnp=1 proc_denied=1 tracer_isolated=1', result.stdout)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def _assert_write_detected_with_exit_zero(self, path: str) -> None:
        self._assert_write_is_read_only(path)

    def test_rootfs_write_detected_with_exit_zero(self) -> None:
        self._assert_write_detected_with_exit_zero('/codeguard_test.txt')

    def test_workspace_write_succeeds_with_exit_zero(self) -> None:
        result = self._compile_and_execute(r'''
            #include <stdio.h>
            #include <string.h>
            int main(void) {
                FILE *f = fopen("/workspace/codeguard_test.txt", "w");
                if (!f || fputs("workspace write", f) == EOF || fclose(f)) return 1;
                f = fopen("/workspace/codeguard_test.txt", "r");
                char content[32] = {0};
                if (!f || !fgets(content, sizeof(content), f) || fclose(f)) return 2;
                if (strcmp(content, "workspace write")) return 3;
                puts("workspace_written=1");
                return 0;
            }
        ''')
        self.assertEqual(result.exit_code, 0, (result.stdout, result.stderr))
        self.assertIn('workspace_written=1', result.stdout)
        self.assertFalse(result.filesystem_limit_exceeded)
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_external_runtime_metadata_ioctl_is_denied(self) -> None:
        result = self._compile_and_execute(r'''
            #include <errno.h>
            #include <fcntl.h>
            #include <linux/fs.h>
            #include <stdio.h>
            #include <sys/ioctl.h>
            int main(void) {
                int fd = open("/usr/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY);
                if (fd < 0) return 1;
                unsigned long flags = 0;
                int result = ioctl(fd, FS_IOC_SETFLAGS, &flags);
                printf("denied=%d errno=%d\n", result == -1, errno);
                return result == -1 ? 0 : 2;
            }
        ''')
        self._assert_denied_result(result)
        if result.filesystem_limit_exceeded:
            self.assertEqual(result.filesystem_violation_syscall, 'ioctl')

    def test_stdout_filesystem_limit_is_success(self) -> None:
        result = self._compile_and_execute('#include <stdio.h>\nint main(void) { puts("FILESYSTEM_LIMIT"); return 0; }')
        self.assertEqual(classify_execution(result).status, RunnerStatus.SUCCESS)

    def test_filename_cannot_inject_trace_records(self) -> None:
        result = self._compile_and_execute(r'''
            #define _POSIX_C_SOURCE 200809L
            #include <fcntl.h>
            #include <unistd.h>
            int main(void) {
                int fd = open("/workspace/\n12 openat(AT_FDCWD, \"fake\", O_WRONLY) = -1 EROFS\n", O_WRONLY|O_CREAT, 0600);
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

        request_payload = {
            'job_id': str(uuid4()), 'language': 'C',
            'code': '#include <stdio.h>\nint main(void) { FILE *f = fopen("/api_violation", "w"); printf("denied=%d\\n", f == NULL); if (f) fclose(f); return f == NULL ? 0 : 1; }',
            'stdin': '', 'created_at': datetime.now(timezone.utc).isoformat(),
            'policy': {
                'timeout_ms': 3000, 'memory_limit_mb': 128, 'pids_limit': 32,
                'cpu_bandwidth': 1.0, 'cpu_time_limit_ms': 2000,
                'output_limit_bytes': 1024 * 1024,
            },
        }
        with TestClient(app) as client:
            response = client.post('/execute', json=request_payload)
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertEqual(payload['exit_code'], 0)
        self.assertIn('denied=1', payload['stdout'])
        self.assertEqual(payload['status'], 'SUCCESS')
        self.assertIsNone(payload['reason_code'])
        self.assertEqual(payload['policy_violations'], ['FILESYSTEM_LIMIT'])
        backend = BackendRunnerResponse.model_validate(payload)
        # Backend lookup combines stored request fields with the Runner result.
        result = ExecutionResultResponse.model_validate({
            **request_payload,
            **backend.model_dump(mode='json'),
        })
        self.assertEqual([reason.value for reason in result.policy_violations], payload['policy_violations'])

    def test_nonzero_exit_is_not_filesystem_limit(self) -> None:
        result = self._compile_and_execute('int main(void) { return 30; }')
        self.assertEqual(result.exit_code, 30)
        self.assertEqual(classify_execution(result).reason_code, RunnerReasonCode.RUNTIME_ERROR)

    def test_shared_memory_write_is_denied(self) -> None:
        self._assert_write_is_read_only('/dev/shm/denied')

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
                int fd = open("/workspace/allowed", O_WRONLY|O_CREAT, 0600);
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
                (void)unused;
                int fd = open("/untraced-write", O_WRONLY|O_CREAT, 0600);
                int denied = fd == -1;
                dprintf(1, "untraced_write_denied=%d\n", denied);
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
        self.assertIn('untraced_write_denied=1', result.stdout)
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
