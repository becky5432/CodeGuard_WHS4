#define _GNU_SOURCE
#include "landlock_policy.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

int cg_write_fs_status(int fd, const char *policyid, int abi, const char *status,
                       struct cg_fs_error *error)
{
    struct stat st;
    char line[4096];
    int length, code, failed = status && !strcmp(status, "FAILED");
    size_t written = 0;
    /* On status errors, preserve the original causal FAILED evidence. */
    if (!cg_fs_valid_id(policyid) || !status ||
        (!failed && strcmp(status, "PREPARED") && strcmp(status, "APPLIED")) ||
        (!failed && abi < CG_FS_MIN_ABI)) {
        code = EINVAL; goto failure;
    }
    if (fstat(fd, &st) < 0) { code = errno; goto failure; }
    if (!S_ISREG(st.st_mode) || st.st_uid || st.st_gid ||
        (st.st_mode & 07777) != 0600 || st.st_nlink != 1 || st.st_size < 0 ||
        st.st_size > CG_FS_STATUS_MAX_BYTES) { code = EPERM; goto failure; }
    if (failed) {
        const char *step = error && error->step[0] ? error->step : "unknown";
        if (strnlen(step, 32) >= 32 || strspn(step, "abcdefghijklmnopqrstuvwxyz_0123456789") != strlen(step)) {
            code = EINVAL; goto failure;
        }
        length = snprintf(line, sizeof(line),
            "CGFS_STATUS 1 policy=%s state=FAILED abi=%d step=%s errno=%d\n",
            policyid, abi < 0 ? 0 : abi, step,
            error && error->saved_errno > 0 ? error->saved_errno : EIO);
    } else {
        length = snprintf(line, sizeof(line), "CGFS_STATUS 1 policy=%s state=%s abi=%d\n",
                          policyid, status, abi);
    }
    if (length <= 0 || (size_t)length >= sizeof(line) ||
        st.st_size + length > CG_FS_STATUS_MAX_BYTES) { code = E2BIG; goto failure; }
    if (lseek(fd, 0, SEEK_END) < 0) { code = errno; goto failure; }
    while (written < (size_t)length) {
        ssize_t count = write(fd, line + written, (size_t)length - written);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) { code = count < 0 ? errno : EIO; goto failure; }
        written += (size_t)count;
    }
    if (fsync(fd) < 0) { code = errno; goto failure; }
    return 0;
failure:
    if (failed) { errno = code; return -1; }
    return cg_fs_fail(error, code, "status", NULL);
}
