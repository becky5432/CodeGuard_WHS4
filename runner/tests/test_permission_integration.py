"""Docker integration tests for the minimal pre-exec permission check."""

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

import docker

from runner.config import settings
from runner.exceptions import SecurityVerificationError
from runner.pipeline.compiler import (
    compile_source,
    create_compile_container,
    get_docker_client,
)
from runner.pipeline.execution import create_execution_container, execute_program
from runner.pipeline.workspace import create_workspace, remove_workspace
from runner.security.filesystem_trace import FilesystemViolation, TRACE_DIRECTORY
from runner.security.runtime_verification import SECURITY_STATUS_PATH


@unittest.skipUnless(
    os.environ.get("RUNNER_DOCKER_TESTS") == "1",
    "실제 Docker 권한 검증 통합 테스트",
)
class PermissionVerificationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = get_docker_client()
        cls.addClassCleanup(cls.client.close)
        cls.client.images.get(settings.cpp_image)

    def test_verified_codeguard_process_is_released(self) -> None:
        workspace = create_workspace(self.client, uuid4())
        self.addCleanup(remove_workspace, self.client, workspace)
        compile_container = create_compile_container(
            self.client,
            workspace,
            "C",
        )
        self.addCleanup(compile_container.remove, force=True)
        compiled = compile_source(
            compile_container,
            workspace,
            "C",
            ("#define _POSIX_C_SOURCE 200809L\n#include <errno.h>\n#include <fcntl.h>\n#include <stdio.h>\n"
             "#include <unistd.h>\nint main(void) {\n"
             "  printf(\"fd3=%s\\n\", fcntl(3, F_GETFD) == -1 && errno == EBADF ? \"closed\" : \"open\");\n"
             "  printf(\"evidence_read=%s\\n\", access(\"/run/codeguard-trace/security.status\", R_OK) == -1 ? \"denied\" : \"allowed\");\n"
             "  printf(\"evidence_write=%s\\n\", access(\"/run/codeguard-trace/security.status\", W_OK) == -1 ? \"denied\" : \"allowed\");\n"
             "  printf(\"evidence_unlink=%s\\n\", unlink(\"/run/codeguard-trace/security.status\") == -1 ? \"denied\" : \"allowed\");\n"
             "  printf(\"evidence_rename=%s\\n\", rename(\"/run/codeguard-trace/security.status\", \"/run/codeguard-trace/security.bak\") == -1 ? \"denied\" : \"allowed\");\n"
             "  printf(\"parent_write=%s\\n\", access(\"/run/codeguard-trace\", W_OK) == -1 ? \"denied\" : \"allowed\");\n"
             "  printf(\"parent_create=%s\\n\", open(\"/run/codeguard-trace/user-created\", O_WRONLY|O_CREAT, 0600) == -1 ? \"denied\" : \"allowed\");\n"
             "  printf(\"parent_symlink=%s\\n\", symlink(\"/workspace/main\", \"/run/codeguard-trace/user-link\") == -1 ? \"denied\" : \"allowed\");\n"
             "  puts(\"user-ran\"); return 0; }"),
        )
        self.assertTrue(compiled.success, compiled.stderr)

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
        )
        self.addCleanup(container.remove, force=True, v=True)
        with patch(
            "runner.pipeline.execution.collect_filesystem_trace",
            return_value=FilesystemViolation(),
        ):
            result = execute_program(
                container,
                workspace.job_id,
                run_id,
                timeout_ms=3000,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.stdout,
            "fd3=closed\nevidence_read=denied\n"
            "evidence_write=denied\nevidence_unlink=denied\n"
            "evidence_rename=denied\nparent_write=denied\n"
            "parent_create=denied\nparent_symlink=denied\nuser-ran\n",
        )
        self.assertIsNone(result.system_error)

    def test_failed_runtime_verification_never_releases_user_code(self) -> None:
        command = (
            f"umask 077; set -C; exec 3>{SECURITY_STATUS_PATH}; "
            "exec /usr/local/bin/codeguard-init --security-fd 3 -- "
            "/bin/echo SHOULD_NOT_RUN"
        )
        container = self.client.containers.create(
            image=settings.cpp_image,
            command=["sh", "-c", command],
            mounts=[
                docker.types.Mount(
                    target=TRACE_DIRECTORY,
                    source="",
                    type="volume",
                )
            ],
            detach=True,
            read_only=True,
            network_mode="none",
            user="0:0",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges=true"],
        )
        self.addCleanup(container.remove, force=True, v=True)

        with self.assertRaises(SecurityVerificationError):
            execute_program(
                container,
                uuid4(),
                uuid4(),
                timeout_ms=3000,
            )

        output = container.logs(stdout=True, stderr=False).decode(
            "utf-8",
            errors="replace",
        )
        self.assertNotIn("SHOULD_NOT_RUN", output)


if __name__ == "__main__":
    unittest.main()
