#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#include "../filesystem/landlock_policy.h"

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
            if (S_ISREG(marker.st_mode)) return 0;
            errno = EINVAL;
            return -1;
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
        "usage: %s --security-fd FD --filesystem-policy-fd FD "
        "--filesystem-status-fd FD --stdin PATH --workdir PATH -- PROGRAM [ARG ...]\n",
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

static int parse_fd(const char *value)
{
    char *end;
    long fd;
    if (!value || !*value || strspn(value, "0123456789") != strlen(value)) return -1;
    errno = 0;
    fd = strtol(value, &end, 10);
    return errno || *end || fd < 3 || fd > 1024 ? -1 : (int)fd;
}

/* Early failures precede policy parsing by design. Recover only a well-formed
 * header ID for FAILED evidence, without granting authority from any rule. */
static void recover_policy_id(int fd, struct cg_fs_policy *policy)
{
    char header[73];
    ssize_t count;
    if (fd < 0 || cg_fs_valid_id(policy->policy_id)) return;
    do { count = pread(fd, header, 72, 0); } while (count < 0 && errno == EINTR);
    if (count != 72 || memcmp(header, "CGFS\t2\t", 7) || header[71] != '\n') return;
    header[71] = 0;
    if (cg_fs_valid_id(header + 7)) memcpy(policy->policy_id, header + 7, 65);
}

int main(int argc, char **argv)
{
    const char *stdin_path = NULL, *workdir = NULL;
    struct cg_fs_policy *policy = calloc(1, sizeof(*policy));
    struct cg_fs_error error = {0};
    int security_fd = -1, policy_fd = -1, status_fd = -1;
    int stdin_fd = -1, ruleset_fd = -1, abi = 0;
    int index = 1, exit_code = 126, result, code;
    struct stat stdin_stat;
    if (!policy) {
        perror("codeguard-init allocation");
        return 126;
    }
    while (index < argc && strcmp(argv[index], "--")) {
        const char *option = argv[index++], *value;
        if (index >= argc) goto bad_cli;
        value = argv[index++];
        if (!strcmp(option, "--security-fd") && security_fd == -1) {
            security_fd = parse_fd(value);
            if (security_fd < 0) goto bad_cli;
        } else if (!strcmp(option, "--filesystem-policy-fd") && policy_fd == -1) {
            policy_fd = parse_fd(value);
            if (policy_fd < 0) goto bad_cli;
        } else if (!strcmp(option, "--filesystem-status-fd") && status_fd == -1) {
            status_fd = parse_fd(value);
            if (status_fd < 0) goto bad_cli;
        } else if (!strcmp(option, "--stdin") && !stdin_path) stdin_path = value;
        else if (!strcmp(option, "--workdir") && !workdir) workdir = value;
        else goto bad_cli;
    }
    if (index >= argc || index + 1 >= argc || security_fd < 0 || policy_fd < 0 ||
        status_fd < 0 || !stdin_path || stdin_path[0] != '/' ||
        !workdir || workdir[0] != '/' || security_fd == policy_fd ||
        security_fd == status_fd || policy_fd == status_fd) goto bad_cli;
    index++;
    /* Reject invalid input FDs before open() could accidentally reuse them. */
    if (fcntl(security_fd, F_GETFD) < 0 || fcntl(policy_fd, F_GETFD) < 0 ||
        fcntl(status_fd, F_GETFD) < 0) {
        cg_fs_fail(&error, errno, "input_fds", NULL); goto failed;
    }
    /* Record the identity verdict before touching user input/work paths.
     * Invalid identities may not traverse a correctly protected 0700 work
     * directory; that must not hide the trusted security failure token. */
    result = verify_runtime_security();
    {
        const char *message = result ? "SECURITY_VERIFICATION_FAILED\n" :
            "SECURITY_VERIFICATION_PASSED\n";
        if (write_all(security_fd, message, strlen(message)) < 0 || fsync(security_fd) < 0) {
            cg_fs_fail(&error, errno, "security_status", NULL); goto failed;
        }
        code = close(security_fd);
        security_fd = -1;
        if (code < 0) {
            cg_fs_fail(&error, errno, "security_close", NULL); goto failed;
        }
    }
    if (result) {
        exit_code = 200;
        cg_fs_fail(&error, EPERM, "security", NULL); goto failed;
    }
    stdin_fd = open(stdin_path, O_RDONLY | O_CLOEXEC | O_NOFOLLOW);
    if (stdin_fd < 0) {
        cg_fs_fail(&error, errno, "stdin_open", stdin_path); goto failed;
    }
    if (fstat(stdin_fd, &stdin_stat) < 0) {
        cg_fs_fail(&error, errno, "stdin_type", stdin_path); goto failed;
    }
    if (!S_ISREG(stdin_stat.st_mode)) {
        cg_fs_fail(&error, EINVAL, "stdin_type", stdin_path); goto failed;
    }
    if (dup2(stdin_fd, STDIN_FILENO) < 0) {
        cg_fs_fail(&error, errno, "stdin_dup2", stdin_path); goto failed;
    }
    /* dup2(x, x) leaves CLOEXEC set when stdin was initially closed. */
    if (fcntl(STDIN_FILENO, F_SETFD, 0) < 0) {
        cg_fs_fail(&error, errno, "stdin_flags", stdin_path); goto failed;
    }
    if (stdin_fd != STDIN_FILENO) close(stdin_fd);
    stdin_fd = -1;
    if (chdir(workdir) < 0) {
        cg_fs_fail(&error, errno, "workdir", workdir); goto failed;
    }
    if (cg_fs_read_policy(policy_fd, policy, &error) < 0 ||
        cg_fs_prepare_ruleset(policy, &ruleset_fd, &abi, &error) < 0) goto failed;
    code = close(policy_fd);
    policy_fd = -1;
    if (code < 0) {
        cg_fs_fail(&error, errno, "policy_close", NULL); goto failed;
    }
    if (cg_write_fs_status(status_fd, policy->policy_id, abi, "PREPARED", &error) < 0)
        goto failed;
    if (wait_for_start_file() < 0) {
        cg_fs_fail(&error, errno, "start_gate", START_READY_PATH); goto failed;
    }
    if (cg_fs_enforce(ruleset_fd, &error) < 0) goto failed;
    code = close(ruleset_fd);
    ruleset_fd = -1;
    if (code < 0) {
        cg_fs_fail(&error, errno, "ruleset_close", NULL); goto failed;
    }
    if (cg_write_fs_status(status_fd, policy->policy_id, abi, "APPLIED", &error) < 0)
        goto failed;
    code = close(status_fd);
    status_fd = -1;
    if (code < 0) {
        cg_fs_fail(&error, errno, "status_close", NULL); goto failed;
    }
    /* Landlock does not revoke already-open FD authority. Do not use a bounded
     * rlimit loop: inherited FDs may sit above a subsequently lowered limit.
     * Unsupported/blocked close_range fails closed instead of leaking FDs. */
    if (syscall(SYS_close_range, 3U, ~0U, 0U) < 0) {
        cg_fs_fail(&error, errno, "close_range", NULL); goto failed;
    }
    free(policy);
    execv(argv[index], &argv[index]);
    fprintf(stderr, "codeguard-init exec failed: %s\n", strerror(errno));
    return 127;
bad_cli:
    usage(argv[0]);
    cg_fs_fail(&error, EINVAL, "cli", NULL);
failed:
    code = error.saved_errno;
    recover_policy_id(policy_fd, policy);
    if (status_fd >= 0 && cg_fs_valid_id(policy->policy_id))
        (void)cg_write_fs_status(status_fd, policy->policy_id, abi, "FAILED", &error);
    if (stdin_fd >= 0) close(stdin_fd);
    if (security_fd >= 0) close(security_fd);
    if (policy_fd >= 0) close(policy_fd);
    if (ruleset_fd >= 0) close(ruleset_fd);
    if (status_fd >= 0) close(status_fd);
    /* Process exits immediately, but also revoke unknown inherited FDs. */
    (void)syscall(SYS_close_range, 3U, ~0U, 0U);
    free(policy);
    errno = code;
    fprintf(stderr, "codeguard-init filesystem failed at %s: %s\n",
            error.step, strerror(code));
    return exit_code;
}
