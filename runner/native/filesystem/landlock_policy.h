#ifndef CODEGUARD_LANDLOCK_POLICY_H
#define CODEGUARD_LANDLOCK_POLICY_H

#include <errno.h>
#include <linux/landlock.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define CG_FS_MAX_BYTES 65536
#define CG_FS_MAX_RULES 128
#define CG_FS_PATH_SIZE 4096
#define CG_FS_STATUS_MAX_BYTES 16384
#define CG_FS_MIN_ABI 7

/* Linux UAPI: IOCTL_DEV is bit 15, introduced in Landlock ABI 5.
 * Ubuntu 24.04's older headers omit it; syscall numbers always come from
 * the platform's sys/syscall.h, never from numeric architecture constants. */
#ifdef LANDLOCK_ACCESS_FS_IOCTL_DEV
#define CG_FS_IOCTL_DEV LANDLOCK_ACCESS_FS_IOCTL_DEV
#else
#define CG_FS_IOCTL_DEV (UINT64_C(1) << 15)
#endif

#define CG_FS_WORK_ACCESS (LANDLOCK_ACCESS_FS_READ_FILE | \
    LANDLOCK_ACCESS_FS_READ_DIR | LANDLOCK_ACCESS_FS_WRITE_FILE | \
    LANDLOCK_ACCESS_FS_TRUNCATE | LANDLOCK_ACCESS_FS_MAKE_REG | \
    LANDLOCK_ACCESS_FS_MAKE_DIR | LANDLOCK_ACCESS_FS_REMOVE_FILE | \
    LANDLOCK_ACCESS_FS_REMOVE_DIR | LANDLOCK_ACCESS_FS_REFER)
#define CG_FS_HANDLED (CG_FS_WORK_ACCESS | LANDLOCK_ACCESS_FS_EXECUTE | \
    LANDLOCK_ACCESS_FS_MAKE_SYM | LANDLOCK_ACCESS_FS_MAKE_FIFO | \
    LANDLOCK_ACCESS_FS_MAKE_SOCK | LANDLOCK_ACCESS_FS_MAKE_CHAR | \
    LANDLOCK_ACCESS_FS_MAKE_BLOCK | CG_FS_IOCTL_DEV)

enum cg_fs_profile { CG_FILE_READ, CG_FILE_EXEC, CG_DIR_LIST, CG_WORK };
struct cg_fs_rule { enum cg_fs_profile profile; char path[CG_FS_PATH_SIZE]; };
struct cg_fs_policy {
    unsigned int version;
    char policy_id[65];
    size_t count;
    struct cg_fs_rule rules[CG_FS_MAX_RULES];
};
struct cg_fs_error { int saved_errno; char step[32]; char path[CG_FS_PATH_SIZE]; };

static inline int cg_fs_fail(struct cg_fs_error *error, int code,
                             const char *step, const char *path)
{
    if (!code) code = EIO;
    if (error) {
        error->saved_errno = code;
        snprintf(error->step, sizeof(error->step), "%s", step ? step : "unknown");
        snprintf(error->path, sizeof(error->path), "%s", path ? path : "");
    }
    errno = code;
    return -1;
}
static inline int cg_fs_valid_id(const char *id)
{
    size_t i;
    if (!id || strnlen(id, 65) != 64) return 0;
    for (i = 0; i < 64; i++)
        if (!((id[i] >= '0' && id[i] <= '9') || (id[i] >= 'a' && id[i] <= 'f')))
            return 0;
    return 1;
}

int cg_fs_read_policy(int fd, struct cg_fs_policy *out, struct cg_fs_error *error);
int cg_fs_prepare_ruleset(const struct cg_fs_policy *policy, int *rulesetfd,
                          int *abi, struct cg_fs_error *error);
int cg_fs_enforce(int fd, struct cg_fs_error *error);
int cg_write_fs_status(int fd, const char *policyid, int abi, const char *status,
                       struct cg_fs_error *error);

/* Weak syscall wrappers provide link-time injection without a runtime bypass. */
int cg_fs_sys_create(const struct landlock_ruleset_attr *, size_t, unsigned int);
int cg_fs_sys_add(int, enum landlock_rule_type,
                  const struct landlock_path_beneath_attr *, unsigned int);
int cg_fs_sys_restrict(int, unsigned int);
int cg_fs_sys_nnp(void);

#endif
