#define _GNU_SOURCE
#include "landlock_policy.h"
#include <fcntl.h>
#include <stdlib.h>
#include <stdarg.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static int failure(const char *step)
{
    const char *value = getenv("CG_NATIVE_FAILURE");
    return value && !strcmp(value, step);
}
int cg_fs_sys_create(const struct landlock_ruleset_attr *attr, size_t size, unsigned int flags)
{
    (void)attr; (void)size;
    if (flags == LANDLOCK_CREATE_RULESET_VERSION) return failure("abi") ? 6 : 7;
    if (failure("create")) { errno = EPERM; return -1; }
    return open("/dev/null", O_RDONLY | O_CLOEXEC);
}
int cg_fs_sys_add(int fd, enum landlock_rule_type type,
                 const struct landlock_path_beneath_attr *attr, unsigned int flags)
{
    (void)fd; (void)type; (void)attr; (void)flags;
    if (failure("add")) { errno = EIO; return -1; }
    return 0;
}
int cg_fs_sys_restrict(int fd, unsigned int flags)
{
    (void)fd; (void)flags;
    if (failure("enforce")) { errno = EACCES; return -1; }
    return 0;
}
int cg_fs_sys_nnp(void)
{
    return prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0);
}

int __real_fsync(int fd);
int __real_fstat(int fd, struct stat *st);
int __wrap_fstat(int fd, struct stat *st)
{
    char path[64], target[4096];
    ssize_t size;
    snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
    size = readlink(path, target, sizeof(target) - 1);
    if (size >= 6) {
        target[size] = 0;
        if (!strcmp(target + size - 6, "/stdin") && failure("stdin_stat")) {
            errno = EIO; return -1;
        }
    }
    return __real_fstat(fd, st);
}
int __wrap_fsync(int fd)
{
    char path[64], target[4096];
    ssize_t size;
    snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
    size = readlink(path, target, sizeof(target) - 1);
    if (size > 0) {
        target[size] = 0;
        if (size >= 7 && !strcmp(target + size - 7, "/status") && failure("sync")) {
            errno = EIO; return -1;
        }
    }
    return __real_fsync(fd);
}
long __real_syscall(long number, ...);
long __wrap_syscall(long number, ...)
{
    va_list args;
    unsigned int first, last, flags;
    if (number != SYS_close_range) { errno = ENOSYS; return -1; }
    va_start(args, number);
    first = va_arg(args, unsigned int);
    last = va_arg(args, unsigned int);
    flags = va_arg(args, unsigned int);
    va_end(args);
    if (failure("close_range")) { errno = ENOSYS; return -1; }
    return __real_syscall(number, first, last, flags);
}
