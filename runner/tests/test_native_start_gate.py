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
    def test_wait_does_not_timeout_before_full_second_elapsed(self) -> None:
        source = Path(__file__).resolve().parents[1] / "native" / "task_tracker" / "codeguard_init.c"
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            harness = directory / "gate_boundary_test.c"
            harness.write_text(
                '#define _GNU_SOURCE\n'
                '#include <errno.h>\n#include <sys/stat.h>\n#include <time.h>\n'
                'static int clock_reads;\n'
                'static int fake_clock_gettime(clockid_t clock, struct timespec *value) {\n'
                '    static const struct timespec times[] = {\n'
                '        {10, 950000000}, {11, 10000000}, {11, 960000000}\n'
                '    };\n'
                '    (void)clock;\n'
                '    if (clock_reads >= 3) { errno = EINVAL; return -1; }\n'
                '    *value = times[clock_reads++];\n'
                '    return 0;\n}\n'
                'static int fake_lstat(const char *path, struct stat *marker) {\n'
                '    (void)path; (void)marker; errno = ENOENT; return -1;\n}\n'
                'static int fake_nanosleep(const struct timespec *requested, struct timespec *remaining) {\n'
                '    (void)requested; (void)remaining; return 0;\n}\n'
                '#define clock_gettime fake_clock_gettime\n'
                '#define lstat fake_lstat\n'
                '#define nanosleep fake_nanosleep\n'
                '#define main codeguard_original_main\n'
                f'#include "{source}"\n'
                '#undef main\n'
                'int main(void) {\n'
                '    int result = wait_for_start_file();\n'
                '    return result == -1 && errno == ETIMEDOUT && clock_reads == 3 ? 0 : 1;\n'
                '}\n',
                encoding="utf-8",
            )
            executable = directory / "gate_boundary_test"
            subprocess.run(
                [
                    "gcc", "-O2", "-Wall", "-Wextra", "-Werror",
                    "-DSTART_WAIT_SECONDS=1",
                    str(harness), "-o", str(executable),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(subprocess.run([str(executable)], timeout=3).returncode, 0)

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
