import io
import tarfile
from unittest.mock import MagicMock

import pytest

from runner.security.filesystem_trace import (
    TRACE_LIMIT_BYTES, TRACE_SYSCALLS, analyze_filesystem_trace, collect_filesystem_trace,
)


@pytest.mark.parametrize('trace', [
    'openat(AT_FDCWD, "/etc/hosts", O_RDONLY) = 3',
    'openat(AT_FDCWD, "/etc/hosts", O_RDONLY) = -1 EROFS (Read-only file system)',
    'openat(AT_FDCWD, "/etc/test", O_WRONLY) = -1 EPERM (Operation not permitted)',
    'openat(AT_FDCWD, "/etc/O_WRONLY", O_RDONLY) = -1 EROFS (Read-only file system)',
    'write(1, "EROFS", 5) = 5',
    'Read-only file system\nFILESYSTEM_LIMIT\nEROFS',
    'openat(AT_FDCWD, "/tmp/test", O_WRONLY) = 3',
])
def test_no_violation(trace):
    assert not analyze_filesystem_trace(trace).detected


@pytest.mark.parametrize('trace,expected', [
    ('openat(AT_FDCWD, "/etc/test", O_WRONLY|O_CREAT, 0600) = -1 EACCES', '/etc/test'),
    ('chdir("/workspace") = 0\nopenat(AT_FDCWD, "../etc/test", O_WRONLY) = -1 EACCES', '/etc/test'),
    ('openat(AT_FDCWD</workspace>, "../etc/test", O_WRONLY) = -1 EACCES', '/etc/test'),
    ('openat(3</etc>, "test", O_RDWR) = -1 EACCES', '/etc/test'),
    ('rename("/workspace/a", "/etc/test") = -1 EACCES', '/etc/test'),
    ('symlink("/etc", "/workspace/link") = 0\n'
     'openat(AT_FDCWD, "/workspace/link/test", O_WRONLY|O_CREAT, 0600) = -1 EACCES', '/etc/test'),
    ('bind(3<UNIX-STREAM:[1]>, {sa_family=AF_UNIX, sun_path="/tmp/socket"}, 110) '
     '= -1 EACCES', '/tmp/socket'),
    ('clone(child_stack=NULL, flags=SIGCHLD) = 42\n'
     '42 mkdir("../etc/test", 0700) = -1 EACCES', '/etc/test'),
])
def test_landlock_denied_path_mutation(trace, expected):
    violation = analyze_filesystem_trace(trace)
    assert violation.detected
    assert violation.errno == 'EACCES'
    assert violation.path == expected


@pytest.mark.parametrize('call', [
    'openat(AT_FDCWD, "/workspace/no_permission", O_WRONLY)',
    'openat(AT_FDCWD, "/workspace/../workspace/no_permission", O_RDWR)',
    'chmod("/etc/no_permission", 0600)',
    'chown("/etc/no_permission", 1, 1)',
    'setxattr("/etc/no_permission", "user.test", "x", 1, 0)',
    'utimensat(AT_FDCWD, "/etc/no_permission", NULL, 0)',
    'write(3</etc/no_permission>, 0x1234, 1)',
    'openat(AT_FDCWD, "unknown", O_WRONLY)',
])
def test_unix_eacces_or_ambiguous_path_is_not_landlock_evidence(call):
    assert not analyze_filesystem_trace(f'{call} = -1 EACCES').detected


def test_selected_write_device_eacces_is_not_policy_evidence():
    trace = 'openat(AT_FDCWD, "/dev/null", O_WRONLY) = -1 EACCES'
    assert not analyze_filesystem_trace(
        trace, allowed_write_paths=frozenset({'/workspace', '/dev/null'}),
    ).detected


def test_removed_symlink_does_not_turn_unix_eacces_into_policy_evidence():
    trace = ('symlink("/etc", "/workspace/link") = 0\n'
             'unlink("/workspace/link") = 0\n'
             'openat(AT_FDCWD, "/workspace/link/test", O_WRONLY) = -1 EACCES')
    assert not analyze_filesystem_trace(trace).detected


@pytest.mark.parametrize('trace', [
    'bind(3, {sa_family=AF_INET, sin_port=htons(80)}, 16) = -1 EACCES',
    'bind(3, {sa_family=AF_UNIX, sun_path=@"abstract"}, 110) = -1 EACCES',
])
def test_non_pathname_bind_is_not_filesystem_evidence(trace):
    assert not analyze_filesystem_trace(trace).detected


def test_pathname_socket_bind_is_traced():
    assert 'bind' in TRACE_SYSCALLS.split(',')


@pytest.mark.parametrize('flag', ['O_WRONLY', 'O_RDWR', 'O_CREAT', 'O_TRUNC', 'O_APPEND', 'O_TMPFILE'])
@pytest.mark.parametrize('syscall,args', [
    ('open', '"/etc/test", FLAG'),
    ('openat', 'AT_FDCWD, "/workspace/test.txt", FLAG|O_CLOEXEC, 0666'),
    ('openat2', 'AT_FDCWD, "/etc/test", {flags=FLAG|O_CLOEXEC, mode=0666, resolve=0}, 24'),
])
def test_write_open(syscall, args, flag):
    violation = analyze_filesystem_trace(f'12 {syscall}({args.replace("FLAG", flag)}) = -1 EROFS (Read-only file system)')
    assert violation.detected
    assert violation.syscall == syscall
    assert violation.errno == 'EROFS'
    assert violation.path in ('/etc/test', '/workspace/test.txt')


@pytest.mark.parametrize('syscall,args', [
    ('creat', '"/etc/test", 0666'),
    ('unlink', '"/workspace/main"'),
    ('unlinkat', 'AT_FDCWD, "/workspace/main", 0'),
    ('rename', '"/workspace/a", "/workspace/b"'),
    ('renameat', 'AT_FDCWD, "/workspace/a", AT_FDCWD, "/workspace/b"'),
    ('renameat2', 'AT_FDCWD, "/workspace/a", AT_FDCWD, "/workspace/b", 0'),
    ('mkdir', '"/workspace/test", 0777'),
    ('mkdirat', 'AT_FDCWD, "/workspace/test", 0777'),
    ('rmdir', '"/workspace/test"'),
    ('link', '"/workspace/a", "/workspace/b"'),
    ('linkat', 'AT_FDCWD, "/workspace/a", AT_FDCWD, "/workspace/b", 0'),
    ('symlink', '"a", "/workspace/b"'),
    ('symlinkat', '"a", AT_FDCWD, "/workspace/b"'),
    ('truncate', '"/workspace/main", 0'),
    ('ftruncate', '3</workspace/main>, 0'),
    ('mknod', '"/workspace/test", S_IFREG|0666, 0'),
    ('mknodat', 'AT_FDCWD, "/workspace/test", S_IFREG|0666, 0'),
])
def test_mutations(syscall, args):
    assert analyze_filesystem_trace(f'{syscall}({args}) = -1 EROFS (Read-only file system)').detected


def test_interleaved_resumed_syscalls():
    trace = '''[pid 12] openat(AT_FDCWD, "/workspace/test", O_WRONLY <unfinished ...>
13 openat(AT_FDCWD, "/etc/hosts", O_RDONLY) = 3
[pid 12] <... openat resumed>) = -1 EROFS (Read-only file system)'''
    assert analyze_filesystem_trace(trace).path == '/workspace/test'


def archive_container(trace, size=None):
    data = trace.encode()
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        info = tarfile.TarInfo('trace.log')
        info.mode = 0o600
        info.size = len(data) if size is None else size
        tar.addfile(info, io.BytesIO(data + b' ' * max(0, info.size - len(data))))
    container = MagicMock()
    container.get_archive.return_value = ([archive.getvalue()], {})
    return container


@pytest.mark.parametrize('trace', ['', '12 execve("/workspace/main", [], 0x0) = -1 ENOENT', '12 execve("/workspace/main", [], 0x0) = 0\n'])
def test_failed_or_incomplete_trace_is_internal_error(trace):
    with pytest.raises(ValueError):
        collect_filesystem_trace(archive_container(trace))


def test_collect_valid_evidence_and_policy_interruption():
    trace = '12 execve("/workspace/main", [], 0x0) = 0\n12 unlink("/workspace/main") = -1 EROFS\n'
    assert collect_filesystem_trace(archive_container(trace), interrupted=True).detected
    assert collect_filesystem_trace(archive_container(trace + '12 +++ exited with 0 +++\n')).detected


def test_trace_size_limit_is_internal_error():
    with pytest.raises(ValueError):
        collect_filesystem_trace(archive_container('', TRACE_LIMIT_BYTES))


def test_child_exit_does_not_hide_incomplete_parent_trace():
    trace = '12 execve("/workspace/main", [], 0x0) = 0\n13 +++ exited with 0 +++\n'
    with pytest.raises(ValueError):
        collect_filesystem_trace(archive_container(trace))


@pytest.mark.parametrize('errno', ['ENOENT', 'EACCES', 'EPERM', 'EINVAL', 'EBADF'])
@pytest.mark.parametrize('call', [
    'openat(AT_FDCWD, "/workspace/test", O_WRONLY|O_CREAT, 0666)',
    'write(0x3, 0x1234, 0x1)',
    'chmod("/workspace/main", 0600)',
])
def test_other_errors_are_not_filesystem_limits(call, errno):
    assert not analyze_filesystem_trace(f'{call} = -1 {errno}').detected


@pytest.mark.parametrize('call', [
    'write(0x3, 0x1234, 0x1)',
    'pwrite64(0x3, 0x1234, 0x1, 0)',
    'writev(0x3, 0x1234, 0x1)',
    'pwritev(0x3, 0x1234, 0x1, 0)',
    'pwritev2(0x3, 0x1234, 0x1, 0, 0)',
    'fallocate(3</workspace/main>, 0, 0, 1)',
    'chmod("/workspace/main", 0600)',
    'fchmod(3</workspace/main>, 0600)',
    'chown("/workspace/main", 10001, 10001)',
    'fchown(3</workspace/main>, 10001, 10001)',
])
def test_write_and_metadata_require_erofs(call):
    assert analyze_filesystem_trace(f'{call} = -1 EROFS').detected
    assert not analyze_filesystem_trace(f'{call} = 0').detected


def test_program_without_mutations_has_valid_nonempty_trace():
    trace = '12 execve("/workspace/main", [], 0x0) = 0\n12 +++ exited with 0 +++\n'
    assert not collect_filesystem_trace(archive_container(trace)).detected


def test_legacy_split_workspace_exec_cannot_establish_trusted_program_trace():
    trace = '12 execve("/workspace/app/main", [], 0x0) = 0\n12 +++ exited with 0 +++\n'
    with pytest.raises(ValueError, match="exec"):
        collect_filesystem_trace(archive_container(trace))


@pytest.mark.parametrize('uid,gid,mode', [(10001, 10001, 0o600), (0, 0, 0o644), (0, 10001, 0o600)])
def test_unprotected_trace_is_rejected(uid, gid, mode):
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode='w') as tar:
        info = tarfile.TarInfo('trace.log')
        info.uid, info.gid, info.mode = uid, gid, mode
        tar.addfile(info, io.BytesIO())
    container = MagicMock()
    container.get_archive.return_value = ([archive.getvalue()], {})
    with pytest.raises(ValueError, match='root-owned and private'):
        collect_filesystem_trace(container)


@pytest.mark.parametrize('call,result', [
    ('seccomp(SECCOMP_SET_MODE_FILTER, 0, 0x1234)', 0),
    ('prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, 0x1234)', 0),
    ('io_uring_setup(8, 0x1234)', 3),
])
def test_successful_bypass_facilities_invalidate_evidence(call, result):
    trace = f'{call} = {result}\nopenat(AT_FDCWD, "/dev/shm/allowed", O_WRONLY) = -1 EROFS'
    with pytest.raises(ValueError, match='invalidates filesystem evidence'):
        analyze_filesystem_trace(trace)
    assert not analyze_filesystem_trace(f'{call} = -1 EPERM').detected


def test_benign_prctl_and_seccomp_queries_remain_valid():
    assert not analyze_filesystem_trace(
        'prctl(PR_SET_NAME, "worker") = 0\nseccomp(SECCOMP_GET_ACTION_AVAIL, 0, 0x1234) = 0',
    ).detected


def test_only_known_mutation_ioctls_are_filesystem_evidence():
    assert analyze_filesystem_trace('ioctl(3</workspace/main>, FS_IOC_SETFLAGS, [0]) = -1 EROFS').detected
    assert not analyze_filesystem_trace('ioctl(3</workspace/main>, FS_IOC_GETFLAGS, [0]) = -1 EROFS').detected
    assert not analyze_filesystem_trace('ioctl(3</workspace/main>, FS_IOC_SETFLAGS, [0]) = -1 EPERM').detected


@pytest.mark.parametrize('resumed', ['', '12 <... seccomp resumed>) = ?\n'])
def test_unfinished_seccomp_install_cannot_supply_trusted_errno(resumed):
    trace = '''12 seccomp(SECCOMP_SET_MODE_FILTER, SECCOMP_FILTER_FLAG_TSYNC, 0x1234 <unfinished ...>
13 openat(AT_FDCWD, "/dev/shm/allowed", O_WRONLY) = -1 EROFS
12 +++ killed by SIGKILL +++
13 +++ exited with 0 +++'''
    trace = trace.replace('12 +++ killed', resumed + '12 +++ killed')
    with pytest.raises(ValueError, match='invalidates filesystem evidence'):
        analyze_filesystem_trace(trace)


@pytest.mark.parametrize('call', [
    'clone(child_stack=0x1234, flags=CLONE_UNTRACED|SIGCHLD)',
    'clone(child_stack=0x1234, flags=0x800011)',
    'clone3({flags=CLONE_UNTRACED, exit_signal=SIGCHLD}, 88)',
    'clone3({flags=0x800000, exit_signal=SIGCHLD}, 88)',
])
def test_untraced_clone_invalidates_evidence(call):
    with pytest.raises(ValueError, match='invalidates filesystem evidence'):
        analyze_filesystem_trace(f'{call} = 42')
    assert not analyze_filesystem_trace(f'{call} = -1 EPERM').detected


def test_traced_clone_remains_valid():
    assert not analyze_filesystem_trace(
        'clone(child_stack=NULL, flags=CLONE_CHILD_CLEARTID|CLONE_CHILD_SETTID|SIGCHLD, child_tidptr=0x1234) = 42',
    ).detected


def test_unfinished_untraced_clone_invalidates_evidence():
    with pytest.raises(ValueError, match='invalidates filesystem evidence'):
        analyze_filesystem_trace('12 clone(child_stack=0x1234, flags=CLONE_UNTRACED|SIGCHLD <unfinished ...>')
