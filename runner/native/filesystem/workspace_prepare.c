#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

/* Only the mounted root is modified; uploaded user files are untouched. */
int main(int argc, char **argv)
{
    int fd = -1;
    int status = 1;
    struct stat st;
    (void)argv;

    if (argc != 1 || geteuid() != 0) {
        fputs("workspace preparation requires root and no arguments\n", stderr);
        return 1;
    }
    /* O_NOFOLLOW rejects the final-component symlink; O_DIRECTORY rejects
     * non-directories. Check the fd before changing root ownership. */
    fd = open("/workspace", O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (fd < 0 || fstat(fd, &st) < 0 || !S_ISDIR(st.st_mode)) {
        perror("workspace root verification");
        goto cleanup;
    }
    /* CHOWN permits changing owner; FOWNER permits chmod after chown;
     * DAC_OVERRIDE permits reopening a previously prepared 0700 root. */
    if (fchown(fd, 10001, 10001) < 0 || fchmod(fd, 0700) < 0 ||
        fstat(fd, &st) < 0 || !S_ISDIR(st.st_mode) ||
        st.st_uid != 10001 || st.st_gid != 10001 ||
        (st.st_mode & 07777) != 0700) {
        perror("workspace root preparation");
        goto cleanup;
    }
    status = 0;
cleanup:
    if (fd >= 0 && close(fd) < 0)
        status = 1;
    return status;
}
