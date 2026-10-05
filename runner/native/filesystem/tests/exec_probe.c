#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <unistd.h>

int main(void)
{
    int fd;
    char input[16] = {0}, cwd[4096];
    for (fd = 3; fd < 8192; fd++) {
        errno = 0;
        if (fcntl(fd, F_GETFD) != -1 || errno != EBADF) return 91;
    }
    if (read(0, input, sizeof(input) - 1) < 0 || !getcwd(cwd, sizeof(cwd))) return 92;
    printf("EXECUTED stdin=%s cwd=%s\n", input, cwd);
    return 0;
}
