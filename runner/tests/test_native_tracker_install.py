"""Safe, host-independent checks for the native tracker installer."""

import os
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "runner/native/task_tracker/install.sh"
README = ROOT / "README.md"


def bash_path() -> str | None:
    if os.name == "nt":
        git_bash = Path("C:/Program Files/Git/bin/bash.exe")
        if git_bash.is_file():
            return str(git_bash)
    return shutil.which("bash")


class NativeTrackerInstallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.bash = bash_path()
        if self.bash is None:
            self.skipTest("bash is unavailable")

    def test_script_has_valid_bash_syntax(self) -> None:
        self.assertTrue(INSTALLER.is_file(), "install.sh does not exist")
        result = subprocess.run(
            [self.bash, "-n"],
            input=INSTALLER.read_text(encoding="utf-8"),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_non_linux_before_sudo(self) -> None:
        self.assertTrue(INSTALLER.is_file(), "install.sh does not exist")
        result = subprocess.run(
            [self.bash, "-s"],
            input=(
                "uname() { printf 'Darwin\\n'; }\n"
                + INSTALLER.read_text(encoding="utf-8")
            ),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Linux", result.stderr)
        self.assertNotIn("sudo", result.stderr)

    def test_rejects_unsupported_architecture_before_sudo(self) -> None:
        result = subprocess.run(
            [self.bash, "-s"],
            input=(
                "uname() { case \"$1\" in -s) printf 'Linux\\n' ;; "
                "-m) printf 'aarch64\\n' ;; *) printf 'test-kernel\\n' ;; "
                "esac; }\n"
                + INSTALLER.read_text(encoding="utf-8")
            ),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("x86_64", result.stderr)
        self.assertNotIn("sudo", result.stderr)

    def test_readme_documents_one_command_installer(self) -> None:
        self.assertIn(
            "bash runner/native/task_tracker/install.sh",
            README.read_text(encoding="utf-8"),
        )

    def test_installer_checkout_uses_lf(self) -> None:
        attributes_path = ROOT / ".gitattributes"
        self.assertTrue(attributes_path.is_file(), ".gitattributes does not exist")
        attributes = attributes_path.read_text(encoding="utf-8")
        self.assertIn(
            "runner/native/task_tracker/install.sh text eol=lf",
            attributes,
        )

    def test_installer_waits_for_service_socket(self) -> None:
        installer = INSTALLER.read_text(encoding="utf-8")
        self.assertIn("for ((attempt=0; attempt<100; attempt++))", installer)
        self.assertIn("sleep 0.1", installer)
        self.assertIn("[[ -S /run/codeguard/task-tracker.sock ]]", installer)

    def _run_package_install(self, bpftool_status: int = 0):
        installer = INSTALLER.read_text(encoding="utf-8")
        package_steps = installer.split("sudo apt-get update", 1)[1].split(
            'venv_dir=', 1,
        )[0]
        # Exercise the actual package commands against Ubuntu's virtual-package
        # failure, without invoking apt, sudo or modifying the host.
        script = r'''
set -Eeuo pipefail
die() { printf '%s\n' "$*" >&2; exit 1; }
uname() { printf 'test-kernel\n'; }
sudo() { "$@"; }
apt-get() {
    printf 'APT'; printf ' <%s>' "$@"; printf '\n'
    for package in "$@"; do
        if [[ "$package" == bpftool ]]; then
            printf "bpftool is virtual and has no installation candidate\n" >&2
            return 100
        fi
    done
}
'''
        script += f"bpftool() {{ return {bpftool_status}; }}\n"
        result = subprocess.run(
            [self.bash, "-s"], input=script + package_steps, text=True,
            encoding="utf-8", errors="replace", capture_output=True,
            check=False, timeout=10,
        )
        return result

    def test_ubuntu_virtual_bpftool_uses_concrete_kernel_tools(self) -> None:
        result = self._run_package_install()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("<linux-tools-common>", result.stdout)
        self.assertIn("<linux-tools-test-kernel>", result.stdout)
        self.assertIn("<linux-headers-test-kernel>", result.stdout)

    def test_unusable_bpftool_stops_before_build(self) -> None:
        result = self._run_package_install(bpftool_status=1)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bpftool", result.stderr)
        self.assertNotIn("installation candidate", result.stderr)


if __name__ == "__main__":
    unittest.main()
