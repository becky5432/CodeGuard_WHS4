#define _GNU_SOURCE
#include "landlock_policy.h"
#include <fcntl.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <sys/syscall.h>
#include <unistd.h>

__attribute__((weak)) int cg_fs_sys_create(const struct landlock_ruleset_attr *a,
                                          size_t size, unsigned int flags)
{
    return (int)syscall(SYS_landlock_create_ruleset, a, size, flags);
}
__attribute__((weak)) int cg_fs_sys_add(int fd, enum landlock_rule_type type,
    const struct landlock_path_beneath_attr *a, unsigned int flags)
{
    return (int)syscall(SYS_landlock_add_rule, fd, type, a, flags);
}
__attribute__((weak)) int cg_fs_sys_restrict(int fd, unsigned int flags)
{
    return (int)syscall(SYS_landlock_restrict_self, fd, flags);
}
__attribute__((weak)) int cg_fs_sys_nnp(void)
{
    return prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0);
}

static int under(const char *path, const char *prefix)
{
    size_t length = strlen(prefix);
    return !strncmp(path, prefix, length) && path[length] == '/';
}
static int numeric_version(const char *value, size_t length)
{
    size_t i;
    if (!length || value[0] == '.' || value[length - 1] == '.') return 0;
    for (i = 0; i < length; i++) {
        if (value[i] >= '0' && value[i] <= '9') continue;
        if (value[i] != '.' || (i && value[i - 1] == '.')) return 0;
    }
    return 1;
}
static int named_version(const char *name, const char *base)
{
    size_t length = strlen(base);
    if (!strcmp(name, base)) return 1;
    return !strncmp(name, base, length) && name[length] == '.' &&
        numeric_version(name + length + 1, strlen(name + length + 1));
}
static int legacy_library(const char *name, const char *prefix)
{
    size_t length = strlen(name), prefix_length = strlen(prefix);
    return length > prefix_length + 3 && !strncmp(name, prefix, prefix_length) &&
        !strcmp(name + length - 3, ".so") &&
        numeric_version(name + prefix_length, length - prefix_length - 3);
}
static int runtime_library(const char *path)
{
    const char *name = strrchr(path, '/');
    if (!name || !(under(path, "/usr/lib/x86_64-linux-gnu") ||
                   under(path, "/usr/local/lib64"))) return 0;
    /* Exact parent directories, not recursive basename-based authorization. */
    if (under(path, "/usr/lib/x86_64-linux-gnu") &&
        name != path + strlen("/usr/lib/x86_64-linux-gnu")) return 0;
    if (under(path, "/usr/local/lib64") &&
        name != path + strlen("/usr/local/lib64")) return 0;
    name++;
    return !strcmp(name, "libc.so.6") || !strcmp(name, "libm.so.6") ||
        named_version(name, "libgcc_s.so.1") || named_version(name, "libstdc++.so.6") ||
        legacy_library(name, "libc-") || legacy_library(name, "libm-");
}
static int loader(const char *path)
{
    return !strcmp(path, "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2");
}
static int device_minor(const char *path)
{
    if (!strcmp(path, "/dev/null")) return 3;
    if (!strcmp(path, "/dev/zero")) return 5;
    if (!strcmp(path, "/dev/random")) return 8;
    if (!strcmp(path, "/dev/urandom")) return 9;
    return -1;
}
static int trusted(const struct cg_fs_rule *rule, const char *path)
{
    switch (rule->profile) {
    case CG_WORK: return !strcmp(path, "/workspace");
    case CG_DEVICE_RW: return !strcmp(path, "/dev/null");
    case CG_DEVICE_READ: return device_minor(path) > 3;
    case CG_FILE_EXEC: return loader(path);
    case CG_FILE_READ:
        return loader(path) || !strcmp(path, "/etc/ld.so.cache") || runtime_library(path);
    }
    return 0;
}

/* Walk every workspace component with O_NOFOLLOW, not just the leaf. */
static int open_workspace(const char *path)
{
    char copy[CG_FS_PATH_SIZE], *part, *next;
    int fd = open("/", O_PATH | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) return -1;
    snprintf(copy, sizeof(copy), "%s", path + 1);
    part = copy;
    while (part && *part) {
        int child, code;
        next = strchr(part, '/');
        if (next) *next++ = 0;
        child = openat(fd, part, O_PATH | O_CLOEXEC | O_NOFOLLOW |
                       (next ? O_DIRECTORY : 0));
        code = errno;
        close(fd);
        if (child < 0) { errno = code; return -1; }
        fd = child;
        part = next;
    }
    return fd;
}

int cg_fs_prepare_ruleset(const struct cg_fs_policy *policy, int *rulesetfd,
                          int *abi, struct cg_fs_error *error)
{
    struct landlock_ruleset_attr attr = {.handled_access_fs = CG_FS_HANDLED};
    int fd, version;
    size_t i;
    if (rulesetfd) *rulesetfd = -1;
    if (abi) *abi = 0;
    if (!policy || !rulesetfd || !abi || policy->version != 2 ||
        !policy->count || policy->count > CG_FS_MAX_RULES || !cg_fs_valid_id(policy->policy_id))
        return cg_fs_fail(error, EINVAL, "policy_validate", NULL);
    version = cg_fs_sys_create(NULL, 0, LANDLOCK_CREATE_RULESET_VERSION);
    if (version < 0) return cg_fs_fail(error, errno, "abi", NULL);
    *abi = version;
    if (version < CG_FS_MIN_ABI)
        return cg_fs_fail(error, EOPNOTSUPP, "abi", NULL);
    fd = cg_fs_sys_create(&attr, sizeof(attr), 0);
    if (fd < 0) return cg_fs_fail(error, errno, "create_ruleset", NULL);
    for (i = 0; i < policy->count; i++) {
        const struct cg_fs_rule *rule = &policy->rules[i];
        struct landlock_path_beneath_attr beneath = {0};
        struct stat st;
        char procpath[64], canonical[CG_FS_PATH_SIZE];
        int pathfd = -1, code = EINVAL;
        const char *step = "rule_profile";
        ssize_t length;
        if (strnlen(rule->path, sizeof(rule->path)) == sizeof(rule->path) ||
            !trusted(rule, rule->path)) goto rule_failed;
        pathfd = (!strcmp(rule->path, "/workspace") || under(rule->path, "/workspace")) ?
            open_workspace(rule->path) : open(rule->path, O_PATH | O_CLOEXEC |
                ((rule->profile == CG_DEVICE_READ || rule->profile == CG_DEVICE_RW) ? O_NOFOLLOW : 0));
        step = "rule_open";
        if (pathfd < 0) { code = errno; goto rule_failed; }
        step = "rule_type";
        if (fstat(pathfd, &st) < 0) { code = errno; goto rule_failed; }
        if (rule->profile == CG_DEVICE_READ || rule->profile == CG_DEVICE_RW) {
            if (!S_ISCHR(st.st_mode) || major(st.st_rdev) != 1 ||
                minor(st.st_rdev) != (unsigned int)device_minor(rule->path)) goto rule_failed;
        } else if (rule->profile == CG_WORK ? !S_ISDIR(st.st_mode) : !S_ISREG(st.st_mode))
            goto rule_failed;
        /* Resolve THIS descriptor after opening; realpath-before-open races. */
        snprintf(procpath, sizeof(procpath), "/proc/self/fd/%d", pathfd);
        length = readlink(procpath, canonical, sizeof(canonical) - 1);
        step = "rule_target";
        if (length < 0) { code = errno; goto rule_failed; }
        if ((size_t)length >= sizeof(canonical) - 1) goto rule_failed;
        canonical[length] = 0;
        if (!trusted(rule, canonical) ||
            ((rule->profile == CG_DEVICE_READ || rule->profile == CG_DEVICE_RW) &&
             strcmp(rule->path, canonical))) goto rule_failed;
        switch (rule->profile) {
        case CG_FILE_READ: beneath.allowed_access = LANDLOCK_ACCESS_FS_READ_FILE; break;
        case CG_FILE_EXEC:
            beneath.allowed_access = LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_EXECUTE;
            break;
        case CG_DEVICE_READ: beneath.allowed_access = LANDLOCK_ACCESS_FS_READ_FILE; break;
        case CG_DEVICE_RW:
            beneath.allowed_access = LANDLOCK_ACCESS_FS_READ_FILE | LANDLOCK_ACCESS_FS_WRITE_FILE;
            break;
        case CG_WORK: beneath.allowed_access = CG_FS_WORK_ACCESS; break;
        }
        beneath.parent_fd = pathfd;
        step = "add_rule";
        if (cg_fs_sys_add(fd, LANDLOCK_RULE_PATH_BENEATH, &beneath, 0) < 0) {
            code = errno; goto rule_failed;
        }
        close(pathfd);
        continue;
rule_failed:
        if (pathfd >= 0) close(pathfd);
        close(fd);
        return cg_fs_fail(error, code, step, rule->path);
    }
    *rulesetfd = fd;
    return 0;
}

int cg_fs_enforce(int fd, struct cg_fs_error *error)
{
    int nnp = cg_fs_sys_nnp();
    if (nnp != 1)
        return cg_fs_fail(error, nnp < 0 ? errno : EPERM, "no_new_privs", NULL);
    if (cg_fs_sys_restrict(fd, 0) < 0)
        return cg_fs_fail(error, errno, "enforce", NULL);
    return 0;
}
