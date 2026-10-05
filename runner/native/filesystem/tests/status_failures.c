#define _GNU_SOURCE
#include "landlock_policy.h"
#include <assert.h>
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>

static int fail_stat, fail_seek, fail_sync, fail_write, interrupted, zero_write;
static mode_t mode = S_IFREG | 0600;
static uid_t uid;
static off_t size;
static char output[4096];
static size_t used;
static int fake_stat(int fd, struct stat *st)
{
    (void)fd;
    if (fail_stat) { errno = EBADF; return -1; }
    memset(st, 0, sizeof(*st));
    st->st_mode = mode; st->st_uid = uid; st->st_size = size; st->st_nlink = 1;
    return 0;
}
static off_t fake_seek(int fd, off_t offset, int origin)
{
    (void)fd; assert(offset == 0 && origin == SEEK_END);
    if (fail_seek) { errno = ESPIPE; return -1; }
    return size;
}
static ssize_t fake_write(int fd, const void *data, size_t length)
{
    (void)fd;
    if (interrupted) { interrupted = 0; errno = EINTR; return -1; }
    if (fail_write) { errno = ENOSPC; return -1; }
    if (zero_write) return 0;
    if (length > 3) length = 3; /* Exercise short writes. */
    assert(used + length < sizeof(output));
    memcpy(output + used, data, length); used += length; output[used] = 0;
    return (ssize_t)length;
}
static int fake_sync(int fd)
{
    (void)fd;
    if (fail_sync) { errno = EIO; return -1; }
    return 0;
}
#define fstat fake_stat
#define lseek fake_seek
#define write fake_write
#define fsync fake_sync
#include "../startup_status.c"

int main(void)
{
    const char *id = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    struct cg_fs_error e = {0};
    interrupted = 1;
    assert(cg_write_fs_status(5, id, 7, "PREPARED", &e) == 0);
    assert(strstr(output, "state=PREPARED abi=7\n"));
    fail_sync = 1;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1 && e.saved_errno == EIO);
    fail_sync = 0; fail_write = 1;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1 && e.saved_errno == ENOSPC);
    fail_write = 0; zero_write = 1;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1 && e.saved_errno == EIO);
    zero_write = 0; fail_stat = 1;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1 && e.saved_errno == EBADF);
    fail_stat = 0; fail_seek = 1;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1 && e.saved_errno == ESPIPE);
    fail_seek = 0; size = CG_FS_STATUS_MAX_BYTES - 1;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1 && e.saved_errno == E2BIG);
    size = 0; mode = S_IFIFO | 0600;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1);
    mode = S_IFREG | 0600; uid = 10001;
    assert(cg_write_fs_status(5, id, 7, "APPLIED", &e) == -1);
    uid = 0; e.saved_errno = EACCES; strcpy(e.step, "enforce"); fail_write = 1;
    assert(cg_write_fs_status(5, id, 7, "FAILED", &e) == -1);
    assert(e.saved_errno == EACCES && !strcmp(e.step, "enforce"));
    fail_write = 0;
    assert(cg_write_fs_status(5, id, 0, "FAILED", &e) == 0);
    assert(strstr(output, "state=FAILED abi=0 step=enforce errno=13\n"));
    return 0;
}
