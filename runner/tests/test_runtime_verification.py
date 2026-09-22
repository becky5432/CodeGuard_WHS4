import io
import tarfile
import unittest
from unittest.mock import MagicMock

import docker

from runner.exceptions import SecurityVerificationError
from runner.security.runtime_verification import (
    SECURITY_FAILURE_TOKEN,
    SECURITY_SUCCESS_TOKEN,
    SECURITY_STATUS_PATH,
    collect_runtime_permission_failure,
)


def status_archive(
    content: bytes,
    *,
    uid: int = 0,
    gid: int = 0,
    mode: int = 0o600,
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("security.status")
        member.size = len(content)
        member.uid = uid
        member.gid = gid
        member.mode = mode
        archive.addfile(member, io.BytesIO(content))
    return buffer.getvalue()


class RuntimePermissionVerificationTests(unittest.TestCase):
    def container_with(self, content: bytes, **metadata):
        container = MagicMock()
        container.get_archive.return_value = (
            [status_archive(content, **metadata)],
            {},
        )
        return container

    def test_success_token_means_codeguard_init_exec_continued(self) -> None:
        container = self.container_with(SECURITY_SUCCESS_TOKEN)

        self.assertFalse(collect_runtime_permission_failure(container))
        container.get_archive.assert_called_once_with(SECURITY_STATUS_PATH)

    def test_failure_token_is_reported(self) -> None:
        self.assertTrue(
            collect_runtime_permission_failure(
                self.container_with(SECURITY_FAILURE_TOKEN)
            )
        )

    def test_untrusted_or_malformed_status_fails_closed(self) -> None:
        cases = (
            self.container_with(b""),
            self.container_with(b"unknown\n"),
            self.container_with(SECURITY_FAILURE_TOKEN, uid=10001),
            self.container_with(SECURITY_FAILURE_TOKEN, mode=0o644),
            self.container_with(b"x" * 65),
        )
        for container in cases:
            with self.subTest(container=container):
                with self.assertRaises(SecurityVerificationError):
                    collect_runtime_permission_failure(container)

    def test_missing_status_fails_closed(self) -> None:
        container = MagicMock()
        container.get_archive.side_effect = docker.errors.NotFound("missing")

        with self.assertRaises(SecurityVerificationError):
            collect_runtime_permission_failure(container)


if __name__ == "__main__":
    unittest.main()
