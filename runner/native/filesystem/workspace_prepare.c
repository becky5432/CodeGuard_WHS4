#define _GNU_SOURCE
#include <fcntl.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>

/* Only mounted volume roots are modified; uploaded user files are untouched. */
int main(int argc, char **argv)
{
    static const char *const roots[] = {
        "/workspace/app", "/workspace/input", "/workspace/work"
    };
    static const mode_t modes[] = {0755, 0755, 0700};
    int fds[] = {-1, -1, -1};
    int status = 1;
    struct stat st;
    (void)argv;

    if (argc != 1 || geteuid() != 0) {
        fputs("workspace preparation requires root and no arguments\n", stderr);
        return 1;
    }
    /* Acquire and verify all roots before changing any ownership. O_NOFOLLOW
     * rejects a final-component symlink; O_DIRECTORY rejects non-directories. */
    for (unsigned i = 0; i < 3; ++i) {
        fds[i] = open(roots[i], O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
        if (fds[i] < 0 || fstat(fds[i], &st) < 0 || !S_ISDIR(st.st_mode)) {
            perror("workspace root verification");
            goto cleanup;
        }
    }
    for (unsigned i = 0; i < 3; ++i) {
        /* CHOWN permits changing owner; FOWNER permits chmod after chown;
         * DAC_OVERRIDE permits reopening a previously prepared 0700 root. */
        if (fchown(fds[i], 10001, 10001) < 0 || fchmod(fds[i], modes[i]) < 0 ||
            fstat(fds[i], &st) < 0 || !S_ISDIR(st.st_mode) ||
            st.st_uid != 10001 || st.st_gid != 10001 ||
            (st.st_mode & 07777) != modes[i]) {
            perror("workspace root preparation");
            goto cleanup;
        }
    }
    status = 0;
cleanup:
    for (unsigned i = 0; i < 3; ++i) {
        if (fds[i] >= 0 && close(fds[i]) < 0)
            status = 1;
    }
    return status;
}
