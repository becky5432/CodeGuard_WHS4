#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <time.h>
#include <unistd.h>

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

static int drain_pending_start_signals(const sigset_t *start_signal)
{
    const struct timespec no_wait = {0, 0};

    for (;;) {
        int signal_number = sigtimedwait(start_signal, NULL, &no_wait);

        if (signal_number == SIGUSR1) {
            continue;
        }
        if (signal_number < 0 && errno == EINTR) {
            continue;
        }
        if (signal_number < 0 && errno == EAGAIN) {
            return 0;
        }
        return -1;
    }
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

static int verify_runtime_security(
    int security_fd, const sigset_t *start_signal
)
{
    static const char zero_capability[] = "0000000000000000";
    char cap_inh[32] = "";
    char cap_prm[32] = "";
    char cap_eff[32] = "";
    char cap_bnd[32] = "";
    char cap_amb[32] = "";
    char evidence[1024];
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
    int evidence_length;
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

    evidence_length = snprintf(
            evidence, sizeof(evidence),
            "status=%s ruid=%lu euid=%lu suid=%lu fsuid=%lu "
            "rgid=%lu egid=%lu sgid=%lu fsgid=%lu "
            "supplementary_group_count=%d supplementary_groups_allowed=%d "
            "cap_inh=%s cap_prm=%s cap_eff=%s cap_bnd=%s cap_amb=%s "
            "no_new_privileges=%d no_new_privileges_prctl=%d "
            "setuid_root_denied=%d setgid_root_denied=%d\n",
            verified ? "verified" : "failed",
            (unsigned long)ruid, (unsigned long)euid, (unsigned long)suid,
            fsuid,
            (unsigned long)rgid, (unsigned long)egid, (unsigned long)sgid,
            fsgid, supplementary_group_count, supplementary_groups_allowed,
            cap_inh, cap_prm, cap_eff, cap_bnd, cap_amb,
            no_new_privileges, no_new_privileges_prctl,
            setuid_root_denied, setgid_root_denied
        );
    if (evidence_length <= 1 || (size_t)evidence_length >= sizeof(evidence) ||
        write_all(security_fd, evidence, (size_t)evidence_length - 1) != 0 ||
        fsync(security_fd) != 0 ||
        drain_pending_start_signals(start_signal) != 0 ||
        write_all(security_fd, "\n", 1) != 0 ||
        fsync(security_fd) != 0 || close(security_fd) != 0) {
        perror("codeguard-init security evidence");
        return -1;
    }
    return verified ? 0 : -1;
}

int main(int argc, char **argv)
{
    const char *stdin_path = NULL;
    char *end = NULL;
    sigset_t start_signal;
    long parsed_security_fd;
    int security_fd = -1;
    int stdin_fd = -1;
    int received_signal = 0;
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

    if (verify_runtime_security(security_fd, &start_signal) != 0) {
        fprintf(stderr, "codeguard-init security verification failed\n");
        return 200;
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
