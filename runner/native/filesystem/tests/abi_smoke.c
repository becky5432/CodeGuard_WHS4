#define _GNU_SOURCE
#include "landlock_policy.h"
#include <assert.h>
#include <fcntl.h>
#include <stdlib.h>
#include <sys/prctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <unistd.h>

static void makefile(const char *path)
{
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0700);
    assert(fd >= 0 && write(fd, "test", 4) == 4 && close(fd) == 0);
}
static void add_rule(int rules, const char *path, uint64_t access)
{
    struct landlock_path_beneath_attr a = {.allowed_access = access};
    a.parent_fd = open(path, O_PATH | O_CLOEXEC);
    assert(a.parent_fd >= 0);
    assert(cg_fs_sys_add(rules, LANDLOCK_RULE_PATH_BENEATH, &a, 0) == 0);
    assert(close(a.parent_fd) == 0);
}
int main(void)
{
    int abi = cg_fs_sys_create(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
    char root[] = "/tmp/cg-landlock-XXXXXX", work[4096], first[4096], second[4096];
    char allowed[4096], denied[4096], file[4096], renamed[4096], linked[4096];
    char fifo[4096], sockpath[4096], executable[4096], charpath[4096];
    struct landlock_ruleset_attr attr = {.handled_access_fs = CG_FS_HANDLED};
    int rules, status;
    pid_t child;
    if (abi < CG_FS_MIN_ABI) {
        printf("SKIP actual Landlock: ABI=%d errno=%d; requires ABI>=7\n", abi, abi < 0 ? errno : 0);
        return 77;
    }
    assert(mkdtemp(root));
    snprintf(work, sizeof(work), "%s/work", root);
    snprintf(first, sizeof(first), "%s/work/a", root);
    snprintf(second, sizeof(second), "%s/work/b", root);
    snprintf(file, sizeof(file), "%s/work/a/file", root);
    snprintf(renamed, sizeof(renamed), "%s/work/b/renamed", root);
    snprintf(linked, sizeof(linked), "%s/work/b/link", root);
    snprintf(fifo, sizeof(fifo), "%s/work/fifo", root);
    snprintf(sockpath, sizeof(sockpath), "%s/work/socket", root);
    snprintf(charpath, sizeof(charpath), "%s/work/device", root);
    snprintf(executable, sizeof(executable), "%s/work/exe", root);
    snprintf(allowed, sizeof(allowed), "%s/allowed", root);
    snprintf(denied, sizeof(denied), "%s/denied", root);
    assert(mkdir(work, 0700) == 0 && mkdir(first, 0700) == 0 && mkdir(second, 0700) == 0);
    makefile(allowed); makefile(denied); makefile(executable);
    rules = cg_fs_sys_create(&attr, sizeof(attr), 0);
    assert(rules >= 0);
    add_rule(rules, work, CG_FS_WORK_ACCESS);
    add_rule(rules, allowed, LANDLOCK_ACCESS_FS_READ_FILE);
    child = fork();
    assert(child >= 0);
    if (!child) {
        struct cg_fs_error error;
        int fd, sock, nested_status;
        pid_t nested;
        struct sockaddr_un address = {.sun_family = AF_UNIX};
        assert(prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) == 0);
        assert(cg_fs_enforce(rules, &error) == 0 && close(rules) == 0);
        fd = open(allowed, O_RDONLY);
        assert(fd >= 0 && close(fd) == 0);
        assert(open(allowed, O_WRONLY) == -1);
        assert(open(denied, O_RDONLY) == -1);
        assert(open("/etc/passwd", O_RDONLY) == -1);
        assert(open("/dev/null", O_RDONLY) == -1);
        fd = open(file, O_CREAT | O_RDWR, 0600);
        assert(fd >= 0 && write(fd, "abc", 3) == 3 && ftruncate(fd, 1) == 0 && close(fd) == 0);
        assert(rename(file, renamed) == 0 && link(renamed, linked) == 0);
        assert(rename(renamed, denied) == -1 && link(allowed, file) == -1);
        assert(symlink(allowed, file) == -1 && mkfifo(fifo, 0600) == -1);
        assert(mknod(charpath, S_IFCHR | 0600, 0) == -1);
        sock = socket(AF_UNIX, SOCK_STREAM, 0);
        assert(sock >= 0 && strlen(sockpath) < sizeof(address.sun_path));
        strcpy(address.sun_path, sockpath);
        assert(bind(sock, (struct sockaddr *)&address, sizeof(address)) == -1);
        assert(close(sock) == 0);
        {
            char *args[] = {executable, NULL};
            execv(executable, args);
            assert(errno == EACCES);
        }
        nested = fork();
        assert(nested >= 0);
        if (!nested) _exit(open(denied, O_RDONLY) == -1 && errno == EACCES ? 0 : 1);
        assert(waitpid(nested, &nested_status, 0) == nested);
        assert(WIFEXITED(nested_status) && WEXITSTATUS(nested_status) == 0);
        assert(unlink(linked) == 0 && unlink(renamed) == 0);
        _exit(0);
    }
    assert(close(rules) == 0 && waitpid(child, &status, 0) == child);
    /* Parent remains unrestricted and cleans only exact test-owned paths. */
    (void)unlink(file); (void)unlink(renamed); (void)unlink(linked);
    (void)unlink(fifo); (void)unlink(sockpath); (void)unlink(charpath);
    assert(unlink(executable) == 0 && unlink(allowed) == 0 && unlink(denied) == 0);
    assert(rmdir(first) == 0 && rmdir(second) == 0 && rmdir(work) == 0 && rmdir(root) == 0);
    assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
    printf("PASS actual Landlock ABI=%d: reads/work/special/exec/link/rename/inheritance\n", abi);
    return 0;
}
