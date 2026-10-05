"""Docker integration tests for the minimal pre-exec permission check."""

import os
import time
import unittest
from pathlib import Path
from uuid import uuid4

from runner.exceptions import SecurityVerificationError
from runner.pipeline.compiler import get_docker_client
from runner.pipeline.execution import create_execution_container, execute_program
from runner.pipeline.workspace import create_workspace, execution_mounts, remove_workspace
from runner.security.filesystem_trace import TRACE_DIRECTORY
from runner.security.filesystem_startup import (
    POLICY_PATH,
    STATUS_PATH,
    build_policy_archive,
    verify_filesystem_applied,
    wait_for_filesystem_prepared,
)
from runner.security.runtime_verification import (
    SECURITY_STATUS_PATH,
    collect_runtime_permission_failure,
)
from runner.pipeline.start_gate import (
    find_codeguard_init_tid,
    release_start_gate,
    wait_for_security_evidence,
)
from runner.tests.filesystem_helpers import (
    pin_execution_image,
    prepare_and_compile,
)


@unittest.skipUnless(
    os.environ.get("RUNNER_DOCKER_TESTS") == "1",
    "실제 Docker 권한 검증 통합 테스트: RUNNER_DOCKER_TESTS=1 필요",
)
class PermissionVerificationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = get_docker_client()
        cls.addClassCleanup(cls.client.close)
        cls.image_id = pin_execution_image(cls.client)

    def test_verified_codeguard_process_is_released(self) -> None:
        workspace = create_workspace(self.client, uuid4())
        self.addCleanup(remove_workspace, self.client, workspace)
        policy, _ = prepare_and_compile(self, workspace, r'''
            #define _GNU_SOURCE
            #include <errno.h>
            #include <fcntl.h>
            #include <linux/capability.h>
            #include <stdio.h>
            #include <sys/prctl.h>
            #include <sys/syscall.h>
            #include <unistd.h>
            int main(void) {
                if (getuid() != 10001 || geteuid() != 10001 ||
                    getgid() != 10001 || getegid() != 10001) return 1;
                struct __user_cap_header_struct header = {
                    .version = _LINUX_CAPABILITY_VERSION_3, .pid = 0
                };
                struct __user_cap_data_struct caps[2] = {{0}};
                if (syscall(SYS_capget, &header, caps)) return 2;
                for (int i = 0; i < 2; ++i)
                    if (caps[i].effective || caps[i].permitted || caps[i].inheritable) return 3;
                if (prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1) return 4;
                puts("uid_gid_dropped=1 caps_empty=1 nnp=1");
                for (int fd = 3; fd <= 5; ++fd) {
                    errno = 0;
                    int closed = fcntl(fd, F_GETFD) == -1 && errno == EBADF;
                    printf("fd%d=%s\n", fd, closed ? "closed" : "open");
                    if (!closed) return 5;
                }
                if (open("/proc/self/status", O_RDONLY) != -1) return 6;
                puts("proc_read=denied");
                printf("evidence_read=%s\n", open("/run/codeguard-trace/security.status", O_RDONLY) == -1 ? "denied" : "allowed");
                printf("evidence_write=%s\n", open("/run/codeguard-trace/security.status", O_WRONLY) == -1 ? "denied" : "allowed");
                printf("evidence_unlink=%s\n", unlink("/run/codeguard-trace/security.status") == -1 ? "denied" : "allowed");
                printf("evidence_rename=%s\n", rename("/run/codeguard-trace/security.status", "/run/codeguard-trace/security.bak") == -1 ? "denied" : "allowed");
                printf("parent_write=%s\n", access("/run/codeguard-trace", W_OK) == -1 ? "denied" : "allowed");
                printf("parent_create=%s\n", open("/run/codeguard-trace/user-created", O_WRONLY|O_CREAT, 0600) == -1 ? "denied" : "allowed");
                printf("parent_symlink=%s\n", symlink("/workspace/app/main", "/run/codeguard-trace/user-link") == -1 ? "denied" : "allowed");
                fputs("stderr-functional\n", stderr);
                puts("user-ran"); return 0;
            }
        ''')

        run_id = uuid4()
        container = create_execution_container(
            self.client,
            workspace,
            "",
            workspace.job_id,
            run_id,
            128,
            1.0,
            32,
            filesystem_policy=policy,
        )
        self.addCleanup(container.remove, force=True, v=True)
        result = execute_program(
            container,
            workspace.job_id,
            run_id,
            timeout_ms=3000,
            filesystem_policy_id=policy.policy_id,
        )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.stdout,
            "uid_gid_dropped=1 caps_empty=1 nnp=1\n"
            "fd3=closed\nfd4=closed\nfd5=closed\nproc_read=denied\n"
            "evidence_read=denied\n"
            "evidence_write=denied\nevidence_unlink=denied\n"
            "evidence_rename=denied\nparent_write=denied\n"
            "parent_create=denied\nparent_symlink=denied\nuser-ran\n",
        )
        self.assertIsNone(result.system_error)
        self.assertEqual(result.stderr, "stderr-functional\n")
        applied = verify_filesystem_applied(container, policy.policy_id)
        self.assertGreaterEqual(applied.abi, 7)

    def test_user_program_waits_for_start_file(self) -> None:
        workspace = create_workspace(self.client, uuid4())
        self.addCleanup(remove_workspace, self.client, workspace)
        policy, _ = prepare_and_compile(
            self, workspace,
            '#define _DEFAULT_SOURCE\n#include <stdio.h>\n#include <unistd.h>\n'
            'int main(void) { puts("USER_RAN"); fflush(stdout); usleep(300000); return 0; }',
        )

        container = create_execution_container(
            self.client, workspace, "", workspace.job_id, uuid4(), 128, 1.0, 32,
            filesystem_policy=policy,
        )
        self.addCleanup(container.remove, force=True, v=True)
        container.start()
        wait_for_security_evidence(container)
        prepared = wait_for_filesystem_prepared(container, policy.policy_id)
        self.assertTrue(prepared.prepared)
        self.assertFalse(prepared.applied)
        self.assertGreaterEqual(prepared.abi, 7)
        container.reload()
        tracer_tid = container.attrs["State"]["Pid"]
        host_proc_available = os.path.exists(
            f"/proc/{tracer_tid}/task/{tracer_tid}/children"
        )
        root_tid = find_codeguard_init_tid(tracer_tid) if host_proc_available else None
        time.sleep(0.05)
        container.reload()
        self.assertTrue(container.attrs["State"]["Running"])
        self.assertNotIn(b"USER_RAN", container.logs())

        release_start_gate(container)
        if root_tid is not None:
            deadline = time.monotonic() + 1
            executable = None
            while time.monotonic() < deadline:
                try:
                    executable = os.readlink(f"/proc/{root_tid}/exe")
                except PermissionError:
                    argv0 = (
                        (Path("/proc") / str(root_tid) / "cmdline")
                        .read_bytes().split(b"\0", 1)[0]
                    )
                    executable = os.fsdecode(argv0)
                except FileNotFoundError:
                    break
                if os.path.basename(executable) == "main":
                    break
                time.sleep(0.01)
            self.assertEqual(os.path.basename(executable or ""), "main")
        self.assertEqual(container.wait(timeout=3)["StatusCode"], 0)
        self.assertIn(b"USER_RAN", container.logs())
        self.assertTrue(verify_filesystem_applied(container, policy.policy_id).applied)

    def test_failed_runtime_verification_never_releases_user_code(self) -> None:
        workspace = create_workspace(self.client, uuid4())
        self.addCleanup(remove_workspace, self.client, workspace)
        policy, _ = prepare_and_compile(
            self, workspace,
            '#include <stdio.h>\nint main(void) { puts("SHOULD_NOT_RUN"); return 0; }',
        )
        command = (
            f"umask 077; set -C; exec 3>{SECURITY_STATUS_PATH}; "
            f"exec 4<{POLICY_PATH}; exec 5>{STATUS_PATH}; "
            "exec /usr/local/bin/codeguard-init --security-fd 3 "
            "--filesystem-policy-fd 4 --filesystem-status-fd 5 "
            "--stdin /workspace/input/stdin --workdir /workspace/work "
            "-- /workspace/app/main"
        )
        container = self.client.containers.create(
            image=self.image_id,
            command=["sh", "-c", command],
            mounts=execution_mounts(workspace),
            detach=True,
            read_only=True,
            network_mode="none",
            user="0:0",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges=true"],
        )
        self.addCleanup(container.remove, force=True, v=True)
        self.assertTrue(container.put_archive(TRACE_DIRECTORY, build_policy_archive(policy)))

        with self.assertRaises(SecurityVerificationError):
            execute_program(
                container,
                uuid4(),
                uuid4(),
                timeout_ms=3000,
                filesystem_policy_id=policy.policy_id,
            )

        # Runner may kill init immediately after seeing FAIL, before its natural
        # exit. The native harness tests natural exit 200 without that race.
        self.assertTrue(collect_runtime_permission_failure(container))
        output = container.logs(stdout=True, stderr=False).decode(
            "utf-8",
            errors="replace",
        )
        self.assertNotIn("SHOULD_NOT_RUN", output)


if __name__ == "__main__":
    unittest.main()
