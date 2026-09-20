# Filesystem detection trust boundary

Execute keeps the root filesystem and `/workspace` read-only. Compile mounts
`/workspace` read-write. `/tmp` remains read-only; Docker's existing `/dev/shm`
mount is writable. Detection does not change these policies.

The trusted tracer runs as UID/GID 0, with only SYS_PTRACE, SETUID and SETGID
added after dropping ALL capabilities. `strace -u codeguard` runs user code as
UID/GID 10001, without effective/permitted/ambient capabilities. Both processes
inherit no-new-privileges. The API's `security_context` describes the user
program, not the trusted tracer. No unconfined seccomp setting is used.

`/run/codeguard-trace` is root-owned, mode 700, on a fresh anonymous volume.
The root tracer creates `trace.log` with mode 600. The user can see the path
name but cannot traverse the directory, read/write the file, or replace it.
User code also cannot signal/ptrace the root tracer or access its `/proc` fds.
Runner reads the stopped container's evidence through Docker `get_archive`,
checks file ownership/permissions, and removes the volume with the container.
Use the rebuilt C/C++ image; older images do not satisfy this boundary.

Only a tracked mutation/write open returning `-1 EROFS` is a filesystem
violation. ENOENT, EACCES, EPERM, EINVAL and EBADF are not filesystem evidence.
stdout, stderr and exit codes never supply filesystem evidence. Write buffers
are printed as raw pointers, preventing user output from filling the trace.

Successful user seccomp installation can manufacture EROFS. Successful
io_uring setup can perform filesystem operations outside ordinary ptrace syscall
stops. Successful `clone`/`clone3` with CLONE_UNTRACED can hide child syscalls.
These facilities invalidate detection and return INTERNAL_ERROR, as do missing,
oversized, incorrectly protected or incomplete traces. Benign prctl operations,
seccomp queries, and denied setup calls remain supported. Even a program with
no mutations must have a successful main exec and a termination record.
Policy termination may leave an incomplete tail; startup evidence is still
required. Existing classifier precedence is unchanged.

The trace is bounded to less than 1 MiB for collection. Trace-heavy workloads
may return INTERNAL_ERROR instead of an application result. Tracing adds a PID,
CPU time and memory inside the existing execution limits. Text parsing depends
on the shipped strace version and supported syscall names; new ABIs/syscalls and
device-specific mutation ioctls need separate review. Known filesystem mutation
ioctls (including FS_IOC_SETFLAGS) are checked; generic/read ioctls are not
filesystem evidence. This is not a kernel-wide
filesystem audit facility.

## Verification

Build an isolated image and enable the opt-in tests:

```sh
docker build -t codeguard-cpp:fs-detection-audit runner/container/cpp
CPP_IMAGE=codeguard-cpp:fs-detection-audit RUNNER_DOCKER_TESTS=1 \
  python -m pytest -q runner/tests/test_filesystem_integration.py
python -m pytest -q
```

PID detection reads the Docker host's `/proc` and cgroup v2 filesystem. A Runner
in another VM/namespace cannot obtain these metrics from its own `/proc`. Run
the integration test process on the Docker host, or in a trusted test Runner
with `--pid=host --cgroupns=host`, `/sys/fs/cgroup` mounted read-only, the Docker
socket, and a read-only source mount. These options apply to the test Runner;
the user execution container keeps its isolated namespaces.
