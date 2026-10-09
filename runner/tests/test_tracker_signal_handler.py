"""Compile the real signal handler with write's warn_unused_result contract."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "native/task_tracker/task_trackerd.c"


class TrackerSignalHandlerTests(unittest.TestCase):
    def test_checked_write_compiles_and_handler_preserves_errno(self):
        compiler = shutil.which("gcc")
        if compiler is None:
            self.skipTest("gcc is unavailable")
        source = SOURCE.read_text(encoding="utf-8")
        handler = source.split("static void handle_signal", 1)[1].split(
            "static int install_signal_handlers", 1,
        )[0]
        probe = r'''
#include <assert.h>
#include <errno.h>
#include <signal.h>
#include <stddef.h>
#include <sys/types.h>
static volatile sig_atomic_t stop_requested;
static int signal_pipe_fds[2] = {-1, -1};
static int write_calls;
static int fail_write;
static ssize_t write(int fd, const void *buf, size_t count)
    __attribute__((warn_unused_result));
static ssize_t write(int fd, const void *buf, size_t count)
{
    (void)fd; (void)buf;
    ++write_calls;
    if (fail_write) { errno = EAGAIN; return -1; }
    return (ssize_t)count;
}
'''
        probe += "static void handle_signal" + handler
        probe += r'''
int main(void)
{
    errno = EDOM;
    handle_signal(SIGTERM);
    assert(stop_requested == 1 && write_calls == 0 && errno == EDOM);
    signal_pipe_fds[1] = 4;
    stop_requested = 0;
    handle_signal(SIGTERM);
    assert(stop_requested == 1 && write_calls == 1 && errno == EDOM);
    fail_write = 1;
    stop_requested = 0;
    handle_signal(SIGTERM);
    assert(stop_requested == 1 && write_calls == 2 && errno == EDOM);
    return 0;
}
'''
        with tempfile.TemporaryDirectory() as directory:
            test_source = Path(directory) / "signal_handler.c"
            executable = Path(directory) / "signal_handler.exe"
            test_source.write_text(probe, encoding="utf-8")
            build = subprocess.run(
                [compiler, "-O2", "-Wall", "-Wextra", "-Werror",
                 str(test_source), "-o", str(executable)],
                capture_output=True, text=True, timeout=20,
            )
            self.assertEqual(build.returncode, 0, build.stderr)
            run = subprocess.run(
                [str(executable)], capture_output=True, text=True, timeout=10,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
