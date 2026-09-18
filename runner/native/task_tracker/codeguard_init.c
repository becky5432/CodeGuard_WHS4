#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void usage(const char *program)
{
    fprintf(
        stderr,
        "usage: %s [--stdin PATH] -- PROGRAM [ARG ...]\n",
        program
    );
}

int main(int argc, char **argv)
{
    const char *stdin_path = NULL;
    sigset_t start_signal;
    int stdin_fd = -1;
    int received_signal = 0;
    int index = 1;

    if (sigemptyset(&start_signal) != 0 ||
        sigaddset(&start_signal, SIGUSR1) != 0 ||
        sigprocmask(SIG_BLOCK, &start_signal, NULL) != 0) {
        perror("codeguard-init signal setup");
        return 126;
    }

    if (index < argc && strcmp(argv[index], "--stdin") == 0) {
        if (index + 1 >= argc) {
            usage(argv[0]);
            return 126;
        }
        stdin_path = argv[index + 1];
        index += 2;
    }

    if (index >= argc || strcmp(argv[index], "--") != 0 ||
        index + 1 >= argc) {
        usage(argv[0]);
        return 126;
    }
    index += 1;

    if (stdin_path) {
        stdin_fd = open(stdin_path, O_RDONLY | O_CLOEXEC);
        if (stdin_fd < 0) {
            perror("codeguard-init stdin open");
            return 126;
        }
        if (dup2(stdin_fd, STDIN_FILENO) < 0) {
            perror("codeguard-init stdin dup2");
            close(stdin_fd);
            return 126;
        }
        close(stdin_fd);
    }

    if (sigwait(&start_signal, &received_signal) != 0 ||
        received_signal != SIGUSR1) {
        fprintf(stderr, "codeguard-init start signal wait failed\n");
        return 126;
    }
    if (sigprocmask(SIG_UNBLOCK, &start_signal, NULL) != 0) {
        perror("codeguard-init signal unblock");
        return 126;
    }

    execv(argv[index], &argv[index]);
    fprintf(
        stderr,
        "codeguard-init exec failed: %s\n",
        strerror(errno)
    );
    _exit(127);
}
