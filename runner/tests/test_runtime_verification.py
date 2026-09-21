import io
import tarfile
import unittest
from unittest.mock import MagicMock, patch

import docker

from runner.exceptions import SecurityVerificationError
from runner.security.runtime_verification import (
    SECURITY_EVIDENCE_PATH,
    verify_runtime_permission_restrictions,
)


VERIFIED_EVIDENCE = (
    "status=verified ruid=10001 euid=10001 suid=10001 fsuid=10001 "
    "rgid=10001 egid=10001 sgid=10001 fsgid=10001 "
    "supplementary_group_count=1 supplementary_groups_allowed=1 "
    "cap_inh=0000000000000000 cap_prm=0000000000000000 "
    "cap_eff=0000000000000000 cap_bnd=00000000a80425fb "
    "cap_amb=0000000000000000 no_new_privileges=1 no_new_privileges_prctl=1 "
    "setuid_root_denied=1 setgid_root_denied=1\n"
)


def evidence_archive(
    content: str,
    *,
    uid: int = 0,
    gid: int = 0,
    mode: int = 0o600,
) -> bytes:
    payload = content.encode("ascii")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("security.status")
        member.size = len(payload)
        member.uid = uid
        member.gid = gid
        member.mode = mode
        archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


class RuntimePermissionVerificationTests(unittest.TestCase):
    def container_with(self, content: str, **metadata):
        container = MagicMock()
        container.get_archive.return_value = (
            [evidence_archive(content, **metadata)],
            {},
        )
        return container

    def test_verified_evidence_is_accepted_and_logged(self) -> None:
        container = self.container_with(VERIFIED_EVIDENCE)

        with self.assertLogs("runner", level="INFO") as logs:
            verify_runtime_permission_restrictions(container)

        container.get_archive.assert_called_once_with(SECURITY_EVIDENCE_PATH)
        self.assertIn(
            "runtime_permission_restriction_verified",
            "\n".join(logs.output),
        )

    def test_identity_mismatch_fails_closed(self) -> None:
        for field, replacement in (
            ("ruid", "ruid=0"),
            ("euid", "euid=0"),
            ("suid", "suid=0"),
            ("fsuid", "fsuid=0"),
            ("rgid", "rgid=0"),
            ("egid", "egid=0"),
            ("sgid", "sgid=0"),
            ("fsgid", "fsgid=0"),
        ):
            with self.subTest(field=field):
                evidence = VERIFIED_EVIDENCE.replace(
                    f"{field}=10001",
                    replacement,
                )
                with self.assertRaises(SecurityVerificationError):
                    verify_runtime_permission_restrictions(
                        self.container_with(evidence)
                    )

    def test_unexpected_supplementary_groups_fail_closed(self) -> None:
        for original, replacement in (
            ("supplementary_group_count=1", "supplementary_group_count=2"),
            (
                "supplementary_groups_allowed=1",
                "supplementary_groups_allowed=0",
            ),
        ):
            with self.subTest(replacement=replacement):
                evidence = VERIFIED_EVIDENCE.replace(original, replacement)
                with self.assertRaises(SecurityVerificationError):
                    verify_runtime_permission_restrictions(
                        self.container_with(evidence)
                    )

    def test_active_capability_fails_closed(self) -> None:
        for field in ("cap_inh", "cap_prm", "cap_eff", "cap_amb"):
            with self.subTest(field=field):
                evidence = VERIFIED_EVIDENCE.replace(
                    f"{field}=0000000000000000",
                    f"{field}=0000000000000001",
                )
                with self.assertRaises(SecurityVerificationError):
                    verify_runtime_permission_restrictions(
                        self.container_with(evidence)
                    )

    def test_failed_runtime_controls_fail_closed(self) -> None:
        for original, replacement in (
            ("no_new_privileges=1", "no_new_privileges=0"),
            ("no_new_privileges_prctl=1", "no_new_privileges_prctl=0"),
            ("setuid_root_denied=1", "setuid_root_denied=0"),
            ("setgid_root_denied=1", "setgid_root_denied=0"),
            ("status=verified", "status=failed"),
        ):
            with self.subTest(replacement=replacement):
                evidence = VERIFIED_EVIDENCE.replace(original, replacement)
                with self.assertRaises(SecurityVerificationError):
                    verify_runtime_permission_restrictions(
                        self.container_with(evidence)
                    )

    def test_malformed_or_untrusted_evidence_fails_closed(self) -> None:
        cases = (
            self.container_with("status=verified\n"),
            self.container_with(VERIFIED_EVIDENCE + VERIFIED_EVIDENCE),
            self.container_with(VERIFIED_EVIDENCE, uid=10001),
            self.container_with(VERIFIED_EVIDENCE, mode=0o644),
        )
        for container in cases:
            with self.subTest(container=container):
                with self.assertRaises(SecurityVerificationError):
                    verify_runtime_permission_restrictions(container)

    @patch(
        "runner.security.runtime_verification.SECURITY_EVIDENCE_TIMEOUT_SECONDS",
        0.0,
    )
    def test_missing_evidence_fails_closed(self) -> None:
        container = MagicMock()
        container.get_archive.side_effect = docker.errors.NotFound(
            "missing evidence"
        )

        with self.assertRaises(SecurityVerificationError):
            verify_runtime_permission_restrictions(container)


if __name__ == "__main__":
    unittest.main()
