#define _GNU_SOURCE
#include "landlock_policy.h"
#include <assert.h>
#include <fcntl.h>
#include <stdarg.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>

static int nextfd = 50, opened, adds, fail_open, fail_stat, fail_link, fail_add;
static int currentfd, nofollow;
static mode_t mode = S_IFDIR;
static const char *target = "/workspace/work";
static uint64_t allowed;
static int fake_open(const char *path, int flags, ...)
{
    if (fail_open) { errno = ENOENT; return -1; }
    assert(flags & O_PATH); assert(flags & O_CLOEXEC);
    if (!strcmp(path, "/")) nofollow++;
    opened++; currentfd = ++nextfd; return currentfd;
}
static int fake_openat(int fd, const char *path, int flags, ...)
{
    (void)fd; (void)path;
    assert(flags & O_PATH); assert(flags & O_NOFOLLOW); assert(flags & O_CLOEXEC);
    nofollow++; opened++; currentfd = ++nextfd; return currentfd;
}
static int fake_close(int fd) { (void)fd; opened--; assert(opened >= 0); return 0; }
static int fake_stat(int fd, struct stat *st)
{
    assert(fd == currentfd);
    if (fail_stat) { errno = EIO; return -1; }
    memset(st, 0, sizeof(*st)); st->st_mode = mode;
    return 0;
}
static ssize_t fake_link(const char *path, char *buffer, size_t size)
{
    char expected[64];
    snprintf(expected, sizeof(expected), "/proc/self/fd/%d", currentfd);
    assert(!strcmp(path, expected));
    if (fail_link) { errno = EIO; return -1; }
    assert(strlen(target) < size); memcpy(buffer, target, strlen(target));
    return (ssize_t)strlen(target);
}
static long fake_syscall(long number, ...)
{
    va_list args;
    va_start(args, number);
    if (number == SYS_landlock_create_ruleset) {
        const struct landlock_ruleset_attr *a = va_arg(args, const struct landlock_ruleset_attr *);
        size_t size = va_arg(args, size_t);
        unsigned int flags = va_arg(args, unsigned int);
        va_end(args);
        if (flags) { assert(!a && !size); return 7; }
        assert(a->handled_access_fs == CG_FS_HANDLED);
        opened++; return ++nextfd;
    }
    if (number == SYS_landlock_add_rule) {
        int fd = va_arg(args, int);
        int type = va_arg(args, int);
        const struct landlock_path_beneath_attr *a = va_arg(args, const struct landlock_path_beneath_attr *);
        unsigned int flags = va_arg(args, unsigned int);
        va_end(args);
        assert(fd > 0 && type == LANDLOCK_RULE_PATH_BENEATH && !flags);
        assert(a->parent_fd == currentfd); allowed = a->allowed_access; adds++;
        if (fail_add) { errno = EPERM; return -1; }
        return 0;
    }
    va_end(args); return 0;
}
static int fake_prctl(int option, ...)
{
    assert(option == PR_GET_NO_NEW_PRIVS); return 1;
}
#define open fake_open
#define openat fake_openat
#define close fake_close
#define fstat fake_stat
#define readlink fake_link
#define syscall fake_syscall
#define prctl fake_prctl
#include "../landlock_policy.c"

static void check(enum cg_fs_profile profile, const char *path, int success, uint64_t mask)
{
    struct cg_fs_policy p = {.version = 1, .count = 1};
    struct cg_fs_error e;
    int fd, abi;
    strcpy(p.policy_id, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa");
    p.rules[0].profile = profile; strcpy(p.rules[0].path, path);
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == (success ? 0 : -1));
    if (success) { assert(allowed == mask); fake_close(fd); }
    else assert(fd == -1);
    assert(opened == 0);
}
int main(void)
{
    check(CG_WORK, "/workspace/work", 1, CG_FS_WORK_ACCESS);
    assert(nofollow == 3);
    target = "/workspace/app";
    check(CG_DIR_LIST, target, 1, LANDLOCK_ACCESS_FS_READ_DIR);
    target = "/workspace/input";
    check(CG_DIR_LIST, target, 1, LANDLOCK_ACCESS_FS_READ_DIR);
    mode = S_IFREG; target = "/workspace/app/main";
    check(CG_FILE_EXEC, target, 1, LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_EXECUTE);
    mode = S_IFLNK;
    check(CG_FILE_EXEC, target, 0, 0); /* O_PATH|NOFOLLOW symlink is not regular. */
    mode = S_IFCHR;
    check(CG_FILE_EXEC, target, 0, 0);
    mode = S_IFREG; target = "/etc/passwd";
    check(CG_FILE_READ, "/usr/lib/x86_64-linux-gnu/libc.so.6", 0, 0);
    target = "/usr/lib/x86_64-linux-gnu/libstdc++.so.6.0.33";
    check(CG_FILE_READ, "/usr/lib/x86_64-linux-gnu/libstdc++.so.6", 1, LANDLOCK_ACCESS_FS_READ_FILE);
    target = "/workspace/input/stdin";
    check(CG_FILE_READ, target, 1, LANDLOCK_ACCESS_FS_READ_FILE);
    fail_stat = 1; check(CG_FILE_READ, target, 0, 0); fail_stat = 0;
    fail_link = 1; check(CG_FILE_READ, target, 0, 0); fail_link = 0;
    fail_open = 1; check(CG_FILE_READ, target, 0, 0); fail_open = 0;
    fail_add = 1; check(CG_FILE_READ, target, 0, 0); fail_add = 0;
    target = "/usr/lib/x86_64-linux-gnu/subdir/libc.so.6";
    check(CG_FILE_READ, target, 0, 0);
    target = "/usr/lib/x86_64-linux-gnu/libc-2.31.so";
    check(CG_FILE_READ, target, 1, LANDLOCK_ACCESS_FS_READ_FILE);
    target = "/usr/lib/x86_64-linux-gnu/libm-2.31.so";
    check(CG_FILE_READ, target, 1, LANDLOCK_ACCESS_FS_READ_FILE);
    target = "/usr/local/lib64/libgcc_s.so.1.2";
    check(CG_FILE_READ, target, 1, LANDLOCK_ACCESS_FS_READ_FILE);
    target = "/workspace/app/main";
    check(CG_FILE_READ, target, 1, LANDLOCK_ACCESS_FS_READ_FILE);
    return 0;
}
