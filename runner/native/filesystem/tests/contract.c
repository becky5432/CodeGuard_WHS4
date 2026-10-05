#define _GNU_SOURCE
#include "landlock_policy.h"
#include <assert.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static const char id[] =
    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
static int abi_value = 7, fail_create, fail_add, fail_restrict, nnp = 1;
static int creates, adds, restricts, last_ruleset = -1;
static unsigned long long handled, allowed;

int cg_fs_sys_create(const struct landlock_ruleset_attr *attr, size_t size,
                     unsigned int flags)
{
    if (flags == LANDLOCK_CREATE_RULESET_VERSION) {
        assert(!attr && !size);
        if (abi_value < 0) errno = ENOSYS;
        return abi_value;
    }
    assert(flags == 0 && size == sizeof(*attr));
    creates++;
    handled = attr->handled_access_fs;
    if (fail_create) { errno = EPERM; return -1; }
    last_ruleset = open("/dev/null", O_RDONLY | O_CLOEXEC);
    return last_ruleset;
}
int cg_fs_sys_add(int fd, enum landlock_rule_type type,
                 const struct landlock_path_beneath_attr *attr, unsigned int flags)
{
    struct stat st;
    assert(fd == last_ruleset && type == LANDLOCK_RULE_PATH_BENEATH && !flags);
    /* Validation and add_rule MUST operate on this same live regular FD. */
    assert(fstat(attr->parent_fd, &st) == 0 && S_ISREG(st.st_mode));
    allowed = attr->allowed_access;
    adds++;
    if (fail_add) { errno = EIO; return -1; }
    return 0;
}
int cg_fs_sys_restrict(int fd, unsigned int flags)
{
    assert(fd == last_ruleset && flags == 0);
    restricts++;
    if (fail_restrict) { errno = EACCES; return -1; }
    return 0;
}
int cg_fs_sys_nnp(void) { return nnp; }

static int scratch(void)
{
    char path[] = "/tmp/cg-native-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0 && unlink(path) == 0);
    return fd;
}
static int parse(const void *data, size_t size, struct cg_fs_policy *p,
                 struct cg_fs_error *e)
{
    int fd = scratch(), result;
    assert(write(fd, data, size) == (ssize_t)size && lseek(fd, 0, SEEK_SET) == 0);
    result = cg_fs_read_policy(fd, p, e);
    assert(fcntl(fd, F_GETFD) >= 0); /* caller owns the policy FD */
    close(fd);
    return result;
}
static void test_parser(void)
{
    struct cg_fs_policy p;
    struct cg_fs_error e;
    char text[70000];
    const char *bad[] = {
        "FILE_READ\trelative\n", "FILE_READ\t\n", "FILE_READ\t/a/../b\n",
        "FILE_READ\t/a\tb\n", "UNKNOWN\t/a\n", "FILE_READ\t/a",
        "FILE_READ\t/a\nFILE_READ\t/a\n", "FILE_READ\t/a\nFILE_EXEC\t/a\n",
        "FILE_READ\t/a//b\n", "FILE_READ\t/a/./b\n", "FILE_READ\t/a/\n",
        "FILE_READ\t/a\r\n", "\n"
    };
    size_t i;
    snprintf(text, sizeof(text), "CGFS\t1\t%s\nFILE_READ\t/etc/ld.so.cache\n", id);
    assert(parse(text, strlen(text), &p, &e) == 0);
    assert(p.version == 1 && p.count == 1 && strcmp(p.policy_id, id) == 0);
    assert(p.rules[0].profile == CG_FILE_READ);
    for (i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        snprintf(text, sizeof(text), "CGFS\t1\t%s\n%s", id, bad[i]);
        assert(parse(text, strlen(text), &p, &e) == -1 && e.saved_errno != 0);
    }
    snprintf(text, sizeof(text), "CGFS\t2\t%s\n", id);
    assert(parse(text, strlen(text), &p, &e) == -1);
    snprintf(text, sizeof(text), "CGFS\t1\t%s\n", id);
    assert(parse(text, strlen(text), &p, &e) == -1);
    snprintf(text, sizeof(text), "CGFS\t1\t%s\n", id);
    text[8] = 'z';
    assert(parse(text, strlen(text), &p, &e) == -1);
    snprintf(text, sizeof(text), "CGFS\t1\t%s\nFILE_READ\t/a\n", id);
    text[80] = 0;
    assert(parse(text, 84, &p, &e) == -1);
    snprintf(text, sizeof(text), "CGFS\t1\t%s\n", id);
    for (i = 0; i < 129; i++) {
        size_t len = strlen(text);
        snprintf(text + len, sizeof(text) - len, "FILE_READ\t/a%zu\n", i);
    }
    assert(parse(text, strlen(text), &p, &e) == -1);
    memset(text, 'a', sizeof(text));
    assert(parse(text, 65537, &p, &e) == -1 && e.saved_errno == E2BIG);
    assert(parse("", 0, &p, &e) == -1);
}
static void test_syscalls(void)
{
    struct cg_fs_policy p = {.version = 1, .count = 1};
    struct cg_fs_error e;
    int fd = -1, abi = 0, before;
    strcpy(p.policy_id, id);
    p.rules[0].profile = CG_FILE_READ;
    strcpy(p.rules[0].path, "/etc/ld.so.cache");
    abi_value = -1;
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == -1);
    assert(e.saved_errno == ENOSYS && fd == -1 && creates == 0);
    abi_value = 6;
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == -1);
    assert(e.saved_errno == EOPNOTSUPP && fd == -1 && creates == 0);
    abi_value = 7; fail_create = 1;
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == -1 && e.saved_errno == EPERM);
    fail_create = 0; fail_add = 1;
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == -1 && e.saved_errno == EIO);
    assert(fd == -1 && fcntl(last_ruleset, F_GETFD) == -1 && errno == EBADF);
    fail_add = 0;
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == 0 && abi == 7);
    assert(allowed == LANDLOCK_ACCESS_FS_READ_FILE);
    assert(handled == CG_FS_HANDLED);
    nnp = 0; before = restricts;
    assert(cg_fs_enforce(fd, &e) == -1 && e.saved_errno == EPERM && restricts == before);
    nnp = 1; fail_restrict = 1;
    assert(cg_fs_enforce(fd, &e) == -1 && e.saved_errno == EACCES);
    fail_restrict = 0;
    assert(cg_fs_enforce(fd, &e) == 0);
    close(fd);
    /* The native validator must agree with the server's canonical library
     * profile and reject a misleading basename in a nested directory. */
    strcpy(p.rules[0].path, "/usr/lib/x86_64-linux-gnu/libc.so.6");
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == 0);
    close(fd);
    strcpy(p.rules[0].path, "/usr/lib/x86_64-linux-gnu/libstdc++.so.6");
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == 0);
    close(fd);
    p.rules[0].profile = CG_FILE_EXEC;
    strcpy(p.rules[0].path, "/lib64/ld-linux-x86-64.so.2");
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == 0);
    assert(allowed == (LANDLOCK_ACCESS_FS_EXECUTE | LANDLOCK_ACCESS_FS_READ_FILE));
    close(fd);
    p.rules[0].profile = CG_FILE_READ;
    before = adds;
    strcpy(p.rules[0].path, "/dev/null");
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == -1 && adds == before);
    p.rules[0].profile = CG_WORK;
    strcpy(p.rules[0].path, "/usr");
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == -1 && adds == before);
    p.rules[0].profile = CG_FILE_EXEC;
    strcpy(p.rules[0].path, "/etc/ld.so.cache");
    assert(cg_fs_prepare_ruleset(&p, &fd, &abi, &e) == -1 && adds == before);
    assert((CG_FS_WORK_ACCESS & (LANDLOCK_ACCESS_FS_EXECUTE |
        LANDLOCK_ACCESS_FS_MAKE_SYM | LANDLOCK_ACCESS_FS_MAKE_FIFO |
        LANDLOCK_ACCESS_FS_MAKE_SOCK | LANDLOCK_ACCESS_FS_MAKE_CHAR |
        LANDLOCK_ACCESS_FS_MAKE_BLOCK | CG_FS_IOCTL_DEV)) == 0);
}
static void test_status(void)
{
    struct cg_fs_error e = {.saved_errno = EACCES};
    char data[2048];
    char path[] = "/tmp/cg-status-XXXXXX";
    int fd = mkstemp(path);
    assert(fd >= 0);
    strcpy(e.step, "enforce");
    if (geteuid() == 0) {
        assert(cg_write_fs_status(fd, id, 7, "PREPARED", &e) == 0);
        assert(cg_write_fs_status(fd, id, 7, "APPLIED", &e) == 0);
        assert(cg_write_fs_status(fd, id, 7, "FAILED", &e) == 0);
        assert(lseek(fd, 0, SEEK_SET) == 0);
        ssize_t count = read(fd, data, sizeof(data) - 1);
        assert(count > 0); data[count] = 0;
        assert(strstr(data, "CGFS_STATUS 1 policy=") == data);
        assert(strstr(data, "state=PREPARED abi=7\n"));
        assert(strstr(data, "state=APPLIED abi=7\n"));
        assert(strstr(data, "state=FAILED abi=7 step=enforce errno=13\n"));
        assert(fchmod(fd, 0666) == 0);
    }
    assert(cg_write_fs_status(fd, id, 7, "APPLIED", &e) == -1);
    assert(fchmod(fd, 0600) == 0);
    assert(cg_write_fs_status(fd, "bad", 7, "APPLIED", &e) == -1);
    assert(cg_write_fs_status(fd, id, 7, "BAD", &e) == -1);
    assert(cg_write_fs_status(fd, id, 6, "APPLIED", &e) == -1);
    close(fd);
    assert(unlink(path) == 0);
}
int main(void)
{
    test_parser(); test_syscalls(); test_status();
    puts("native contract: parser, Landlock fail-closed, status PASS");
    return 0;
}
