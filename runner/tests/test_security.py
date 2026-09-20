import copy
import unittest
from unittest.mock import MagicMock

from runner.exceptions import SecurityVerificationError
from runner.security import (
    parse_security_preflight,
    verify_container_security_config,
)


VERIFIED_MARKER = (
    "CODEGUARD_SECURITY_CHECK status=verified uid=10001 gid=10001 "
    "cap_inh=0000000000000000 cap_prm=0000000000000000 "
    "cap_eff=0000000000000000 cap_bnd=0000000000000000 "
    "cap_amb=0000000000000000 no_new_privileges=1\n"
)


class SecurityConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.container = MagicMock()
        self.attrs = {
            "Config": {"User": "10001:10001"},
            "HostConfig": {
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges=true"],
            },
            "Mounts": [{"Destination": "/workspace", "RW": True}],
        }
        self.container.attrs = self.attrs

    def test_compile_inspect_security_is_verified_and_logged(self) -> None:
        with self.assertLogs("runner", level="INFO") as logs:
            verify_container_security_config(
                self.container,
                stage="compile",
                workspace_mode="rw",
            )

        self.assertTrue(
            any("container_security_config_verified" in line for line in logs.output)
        )
        self.container.start.assert_not_called()

    def test_inspect_mismatch_fails_closed_before_start(self) -> None:
        cases = {
            "root_user": ("Config", "User", "0:0"),
            "missing_cap_drop": ("HostConfig", "CapDrop", []),
            "missing_no_new_privileges": ("HostConfig", "SecurityOpt", []),
        }
        for name, (section, key, value) in cases.items():
            with self.subTest(name=name):
                self.container.reset_mock()
                self.container.attrs = copy.deepcopy(self.attrs)
                self.container.attrs[section][key] = value
                with self.assertLogs("runner", level="ERROR") as logs:
                    with self.assertRaises(SecurityVerificationError):
                        verify_container_security_config(
                            self.container,
                            stage="compile",
                            workspace_mode="rw",
                        )
                self.assertTrue(
                    any(
                        "container_security_verification_failed" in line
                        for line in logs.output
                    )
                )
                self.container.start.assert_not_called()

    def test_compile_read_only_workspace_fails_closed(self) -> None:
        self.container.attrs["Mounts"][0]["RW"] = False
        with self.assertRaises(SecurityVerificationError):
            verify_container_security_config(
                self.container,
                stage="compile",
                workspace_mode="rw",
            )
        self.container.start.assert_not_called()

    def test_execution_read_write_workspace_fails_closed(self) -> None:
        with self.assertRaises(SecurityVerificationError):
            verify_container_security_config(
                self.container,
                stage="execute",
                workspace_mode="ro",
            )
        self.container.start.assert_not_called()


class RuntimeSecurityPreflightTests(unittest.TestCase):
    def test_verified_marker_is_logged_and_removed_from_stderr(self) -> None:
        with self.assertLogs("runner", level="INFO") as logs:
            stderr = parse_security_preflight(
                VERIFIED_MARKER + "user stderr\n",
                stage="execute",
            )

        self.assertEqual(stderr, "user stderr\n")
        log = "\n".join(logs.output)
        self.assertIn("container_security_verified", log)
        self.assertIn("uid=10001", log)
        self.assertIn("cap_eff=0000000000000000", log)
        self.assertIn("no_new_privileges=1", log)

    def test_missing_marker_fails_closed(self) -> None:
        with self.assertRaises(SecurityVerificationError):
            parse_security_preflight("user stderr\n", stage="compile")

    def test_runtime_uid_mismatch_fails_closed(self) -> None:
        marker = VERIFIED_MARKER.replace(
            "status=verified uid=10001",
            "status=failed uid=0",
        )
        with self.assertLogs("runner", level="ERROR") as logs:
            with self.assertRaises(SecurityVerificationError):
                parse_security_preflight(marker, stage="execute")
        log = "\n".join(logs.output)
        self.assertIn("container_security_verification_failed", log)
        self.assertIn("uid=0", log)

    def test_runtime_capability_mismatch_fails_closed(self) -> None:
        marker = VERIFIED_MARKER.replace(
            "status=verified",
            "status=failed",
        ).replace(
            "cap_eff=0000000000000000",
            "cap_eff=00000000a80425fb",
        )
        with self.assertRaises(SecurityVerificationError):
            parse_security_preflight(marker, stage="compile")

    def test_runtime_no_new_privileges_mismatch_fails_closed(self) -> None:
        marker = VERIFIED_MARKER.replace(
            "status=verified",
            "status=failed",
        ).replace(
            "no_new_privileges=1",
            "no_new_privileges=0",
        )
        with self.assertRaises(SecurityVerificationError):
            parse_security_preflight(marker, stage="execute")


if __name__ == "__main__":
    unittest.main()
