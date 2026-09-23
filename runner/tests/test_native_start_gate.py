"""Linux-only checks for codeguard-init's file gate without Docker."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


@unittest.skipUnless(os.name == "posix" and shutil.which("gcc"), "requires Linux gcc")
class NativeStartGateTests(unittest.TestCase):
    def test_waits_for_regular_file_and_times_out_without_it(self) -> None:
        source = Path(__file__).resolve().parents[1] / "native" / "task_tracker" / "codeguard_init.c"
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            marker = directory / "start.ready"
            harness = directory / "gate_test.c"
            harness.write_text(
                f'#define main codeguard_original_main\n#include "{source}"\n'
                '#undef main\nint main(void) { return wait_for_start_file() == 0 ? 0 : 1; }\n',
                encoding="utf-8",
            )
            executable = directory / "gate_test"
            subprocess.run(
                [
                    "gcc", "-O2", "-Wall", "-Wextra", "-Werror",
                    f'-DSTART_READY_PATH="{marker}"',
                    "-DSTART_WAIT_SECONDS=1",
                    str(harness), "-o", str(executable),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            waiting = subprocess.Popen([str(executable)])
            try:
                time.sleep(0.05)
                self.assertIsNone(waiting.poll())
                marker.touch()
                self.assertEqual(waiting.wait(timeout=3), 0)
            finally:
                if waiting.poll() is None:
                    waiting.kill()
                    waiting.wait()

            marker.unlink()
            self.assertEqual(subprocess.run([str(executable)], timeout=3).returncode, 1)

            marker.symlink_to(directory / "missing")
            self.assertEqual(subprocess.run([str(executable)], timeout=3).returncode, 1)
