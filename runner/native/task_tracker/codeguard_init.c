#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#ifndef START_READY_PATH
#define START_READY_PATH "/run/codeguard-trace/start.ready"
#endif
#ifndef START_WAIT_SECONDS
#define START_WAIT_SECONDS 15
#endif

static int wait_for_start_file(void)
{
    struct timespec start;
    struct timespec now;
    struct timespec deadline;
    struct timespec pause_time = { .tv_sec = 0, .tv_nsec = 10000000 };
    struct stat marker;

    if (clock_gettime(CLOCK_MONOTONIC, &start) != 0) {
        return -1;
    }
    deadline = start;
    deadline.tv_sec += START_WAIT_SECONDS;
    for (;;) {
        if (lstat(START_READY_PATH, &marker) == 0) {
            return S_ISREG(marker.st_mode) ? 0 : -1;
        }
        if (errno != ENOENT || clock_gettime(CLOCK_MONOTONIC, &now) != 0) {
            return -1;
        }
        if (now.tv_sec > deadline.tv_sec ||
            (now.tv_sec == deadline.tv_sec && now.tv_nsec >= deadline.tv_nsec)) {
            errno = ETIMEDOUT;
            return -1;
        }
        while (nanosleep(&pause_time, &pause_time) != 0) {
            if (errno != EINTR) {
                return -1;
            }
        }
        pause_time.tv_sec = 0;
        pause_time.tv_nsec = 10000000;
    }
}

static void usage(const char *program)
{
    fprintf(
        stderr,
        "usage: %s --security-fd FD [--stdin PATH] -- PROGRAM [ARG ...]\n",
        program
    );
}

static int read_process_security(
    char *cap_inh, char *cap_prm, char *cap_eff, char *cap_bnd,
    char *cap_amb, unsigned long *fsuid, unsigned long *fsgid,
    int *no_new_privileges
)
{
    FILE *status = fopen("/proc/self/status", "r");
    char *line = NULL;
    size_t capacity = 0;

    if (!status) {
        return -1;
    }
    while (getline(&line, &capacity, status) >= 0) {
        unsigned long real_id;
        unsigned long effective_id;
        unsigned long saved_id;

        (void)sscanf(
            line, "Uid:%lu%lu%lu%lu",
            &real_id, &effective_id, &saved_id, fsuid
        );
        (void)sscanf(
            line, "Gid:%lu%lu%lu%lu",
            &real_id, &effective_id, &saved_id, fsgid
        );
        (void)sscanf(line, "CapInh:%31s", cap_inh);
        (void)sscanf(line, "CapPrm:%31s", cap_prm);
        (void)sscanf(line, "CapEff:%31s", cap_eff);
        (void)sscanf(line, "CapBnd:%31s", cap_bnd);
        (void)sscanf(line, "CapAmb:%31s", cap_amb);
        (void)sscanf(line, "NoNewPrivs:%d", no_new_privileges);
    }
    free(line);
    if (fclose(status) != 0) {
        return -1;
    }
    return 0;
}

static int write_all(int fd, const char *buffer, size_t length)
{
    size_t written = 0;

    while (written < length) {
        ssize_t result = write(fd, buffer + written, length - written);

        if (result < 0 && errno == EINTR) {
            continue;
        }
        if (result <= 0) {
            return -1;
        }
        written += (size_t)result;
    }
    return 0;
}

static int verify_runtime_security(void)
{
    static const char zero_capability[] = "0000000000000000";
    char cap_inh[32] = "";
    char cap_prm[32] = "";
    char cap_eff[32] = "";
    char cap_bnd[32] = "";
    char cap_amb[32] = "";
    gid_t supplementary_groups[32];
    uid_t ruid = (uid_t)-1;
    uid_t euid = (uid_t)-1;
    uid_t suid = (uid_t)-1;
    gid_t rgid = (gid_t)-1;
    gid_t egid = (gid_t)-1;
    gid_t sgid = (gid_t)-1;
    unsigned long fsuid = ULONG_MAX;
    unsigned long fsgid = ULONG_MAX;
    int supplementary_group_count = -1;
    int supplementary_groups_allowed = 0;
    int no_new_privileges = -1;
    int no_new_privileges_prctl = -1;
    int setuid_root_denied;
    int setgid_root_denied;
    int verified;

    if (getresuid(&ruid, &euid, &suid) != 0 ||
        getresgid(&rgid, &egid, &sgid) != 0) {
        perror("codeguard-init resuid/resgid");
    }
    supplementary_group_count = getgroups(
        (int)(sizeof(supplementary_groups) / sizeof(supplementary_groups[0])),
        supplementary_groups
    );
    supplementary_groups_allowed = supplementary_group_count == 0 ||
        (supplementary_group_count == 1 &&
         supplementary_groups[0] == 10001);


    if (read_process_security(
            cap_inh, cap_prm, cap_eff, cap_bnd, cap_amb,
            &fsuid, &fsgid, &no_new_privileges
        ) != 0) {
        perror("codeguard-init security status");
    }
    no_new_privileges_prctl = prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0);

    errno = 0;
    setuid_root_denied = setuid(0) == -1 && errno == EPERM;
    errno = 0;
    setgid_root_denied = setgid(0) == -1 && errno == EPERM;

    verified = ruid == 10001 && euid == 10001 && suid == 10001 &&
        fsuid == 10001 &&
        rgid == 10001 && egid == 10001 && sgid == 10001 &&
        fsgid == 10001 && supplementary_groups_allowed &&
        strcmp(cap_inh, zero_capability) == 0 &&
        strcmp(cap_prm, zero_capability) == 0 &&
        strcmp(cap_eff, zero_capability) == 0 &&
        strcmp(cap_amb, zero_capability) == 0 &&
        no_new_privileges == 1 && no_new_privileges_prctl == 1 &&
        setuid_root_denied && setgid_root_denied;

    return verified ? 0 : -1;
}

int main(int argc, char **argv)
{
    const char *stdin_path = NULL;
    char *end = NULL;
    long parsed_security_fd;
    int security_fd = -1;
    int stdin_fd = -1;
    int index = 1;
    if (index + 1 >= argc || strcmp(argv[index], "--security-fd") != 0) {
        usage(argv[0]);
        return 126;
    }
    errno = 0;
    parsed_security_fd = strtol(argv[index + 1], &end, 10);
    if (errno != 0 || !end || *end != '\0' || parsed_security_fd < 3 ||
        parsed_security_fd > 1024) {
        usage(argv[0]);
        return 126;
    }
    security_fd = (int)parsed_security_fd;
    index += 2;


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

    if (verify_runtime_security() != 0) {
        static const char failure[] = "SECURITY_VERIFICATION_FAILED\n";

        if (write_all(security_fd, failure, sizeof(failure) - 1) != 0 ||
            fsync(security_fd) != 0 || close(security_fd) != 0) {
            perror("codeguard-init security failure status");
            return 126;
        }
        fprintf(stderr, "codeguard-init security verification failed\n");
        return 200;
    }
    {
        static const char success[] = "SECURITY_VERIFICATION_PASSED\n";

        if (write_all(security_fd, success, sizeof(success) - 1) != 0 ||
            fsync(security_fd) != 0 || close(security_fd) != 0) {
            perror("codeguard-init security success status");
            return 126;
        }
    }

    if (wait_for_start_file() != 0) {
        perror("codeguard-init start gate");
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
