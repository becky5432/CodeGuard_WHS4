#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <unistd.h>

#include <bpf/bpf.h>
#include <bpf/libbpf.h>

#include "task_tracker.skel.h"
#include "task_tracker_shared.h"

#define DEFAULT_SOCKET_PATH "/run/codeguard/task-tracker.sock"

static volatile sig_atomic_t stop_requested;
static int signal_pipe_fds[2] = {-1, -1};

static void handle_signal(int signal_number)
{
    int saved_errno = errno;
    char notification = 1;

    (void)signal_number;
    stop_requested = 1;
    if (signal_pipe_fds[1] >= 0)
        (void)write(signal_pipe_fds[1], &notification, sizeof(notification));
    errno = saved_errno;
}

static int install_signal_handlers(void)
{
    struct sigaction action = {
        .sa_handler = handle_signal,
        .sa_flags = 0,
    };

    if (sigemptyset(&action.sa_mask) < 0)
        return -1;
    if (sigaction(SIGINT, &action, NULL) < 0)
        return -1;
    if (sigaction(SIGTERM, &action, NULL) < 0)
        return -1;
    return 0;
}

static bool same_run_id(
    const struct cg_run_id *left,
    const struct cg_run_id *right
)
{
    return memcmp(left->bytes, right->bytes, sizeof(left->bytes)) == 0;
}

static void remember_errno(int *first_error)
{
    int error_number = errno;

    if (error_number != ENOENT && *first_error == 0)
        *first_error = -error_number;
}

static void remember_result(int *first_error, int result)
{
    if (result < 0 && *first_error == 0)
        *first_error = result;
}

static int register_cgroup(
    struct task_tracker_bpf *skel,
    const struct cg_request *request
)
{
    struct cg_cgroup_key cgroup_key = {
        .cgroup_id = request->cgroup_id,
    };
    struct cg_cgroup_value cgroup_value = {
        .run_id = request->run_id,
    };
    struct cg_run_metrics metrics = {};
    int metrics_fd = bpf_map__fd(skel->maps.run_metrics);
    int cgroups_fd = bpf_map__fd(skel->maps.tracked_cgroups);
    int result;

    if (request->cgroup_id == 0)
        return -EINVAL;

    result = bpf_map_update_elem(
        metrics_fd,
        &request->run_id,
        &metrics,
        BPF_NOEXIST
    );
    if (result)
        return -errno;

    result = bpf_map_update_elem(
        cgroups_fd,
        &cgroup_key,
        &cgroup_value,
        BPF_NOEXIST
    );
    if (result) {
        int saved_errno = errno;
        bpf_map_delete_elem(metrics_fd, &request->run_id);
        return -saved_errno;
    }
    return 0;
}

static int register_root(
    struct task_tracker_bpf *skel,
    const struct cg_request *request
)
{
    struct cg_task_key task_key = {
        .tid = request->root_tid,
    };
    struct cg_task_value task_value = {
        .run_id = request->run_id,
        .tgid = request->root_tid,
    };
    struct cg_process_key process_key = {
        .run_id = request->run_id,
        .tgid = request->root_tid,
    };
    struct cg_process_value process_value = {
        .live_tasks = 1,
    };
    struct cg_run_metrics metrics = {};
    int tasks_fd = bpf_map__fd(skel->maps.tracked_tasks);
    int processes_fd = bpf_map__fd(skel->maps.process_tasks);
    int metrics_fd = bpf_map__fd(skel->maps.run_metrics);
    int result;

    if (request->root_tid == 0)
        return -EINVAL;
    if (bpf_map_lookup_elem(metrics_fd, &request->run_id, &metrics))
        return -errno;

    result = bpf_map_update_elem(
        tasks_fd,
        &task_key,
        &task_value,
        BPF_NOEXIST
    );
    if (result)
        return -errno;

    result = bpf_map_update_elem(
        processes_fd,
        &process_key,
        &process_value,
        BPF_NOEXIST
    );
    if (result) {
        int saved_errno = errno;
        bpf_map_delete_elem(tasks_fd, &task_key);
        return -saved_errno;
    }

    metrics.process_current = 1;
    metrics.thread_current = 0;
    metrics.user_task_current = 1;
    metrics.user_task_peak = 1;
    metrics.process_at_user_task_peak = 1;
    metrics.thread_at_user_task_peak = 0;
    result = bpf_map_update_elem(
        metrics_fd,
        &request->run_id,
        &metrics,
        BPF_F_LOCK
    );
    if (result) {
        int saved_errno = errno;
        bpf_map_delete_elem(processes_fd, &process_key);
        bpf_map_delete_elem(tasks_fd, &task_key);
        return -saved_errno;
    }
    return 0;
}

static int snapshot(
    struct task_tracker_bpf *skel,
    const struct cg_request *request,
    struct cg_peak_snapshot *result
)
{
    struct cg_run_metrics metrics = {};
    int metrics_fd = bpf_map__fd(skel->maps.run_metrics);

    if (bpf_map_lookup_elem_flags(
            metrics_fd,
            &request->run_id,
            &metrics,
            BPF_F_LOCK
        ))
        return -errno;

    result->user_task_peak = metrics.user_task_peak;
    result->process_at_user_task_peak = metrics.process_at_user_task_peak;
    result->thread_at_user_task_peak = metrics.thread_at_user_task_peak;
    result->error_flags = metrics.error_flags;
    return 0;
}

static int remove_cgroups(
    int map_fd,
    const struct cg_run_id *run_id
)
{
    struct cg_cgroup_key key = {};
    struct cg_cgroup_key next = {};
    int first_error = 0;

    if (bpf_map_get_next_key(map_fd, NULL, &key) < 0) {
        remember_errno(&first_error);
        return first_error;
    }

    for (;;) {
        struct cg_cgroup_value value = {};
        int next_result = bpf_map_get_next_key(map_fd, &key, &next);

        if (next_result < 0)
            remember_errno(&first_error);

        if (bpf_map_lookup_elem(map_fd, &key, &value) < 0) {
            remember_errno(&first_error);
        } else if (same_run_id(&value.run_id, run_id) &&
                   bpf_map_delete_elem(map_fd, &key) < 0) {
            remember_errno(&first_error);
        }
        if (next_result < 0)
            break;
        key = next;
    }
    return first_error;
}

static int remove_tasks(int map_fd, const struct cg_run_id *run_id)
{
    struct cg_task_key key = {};
    struct cg_task_key next = {};
    int first_error = 0;

    if (bpf_map_get_next_key(map_fd, NULL, &key) < 0) {
        remember_errno(&first_error);
        return first_error;
    }

    for (;;) {
        struct cg_task_value value = {};
        int next_result = bpf_map_get_next_key(map_fd, &key, &next);

        if (next_result < 0)
            remember_errno(&first_error);

        if (bpf_map_lookup_elem(map_fd, &key, &value) < 0) {
            remember_errno(&first_error);
        } else if (same_run_id(&value.run_id, run_id) &&
                   bpf_map_delete_elem(map_fd, &key) < 0) {
            remember_errno(&first_error);
        }
        if (next_result < 0)
            break;
        key = next;
    }
    return first_error;
}

static int remove_processes(int map_fd, const struct cg_run_id *run_id)
{
    struct cg_process_key key = {};
    struct cg_process_key next = {};
    int first_error = 0;

    if (bpf_map_get_next_key(map_fd, NULL, &key) < 0) {
        remember_errno(&first_error);
        return first_error;
    }

    for (;;) {
        int next_result = bpf_map_get_next_key(map_fd, &key, &next);

        if (next_result < 0)
            remember_errno(&first_error);
        if (same_run_id(&key.run_id, run_id) &&
            bpf_map_delete_elem(map_fd, &key) < 0)
            remember_errno(&first_error);
        if (next_result < 0)
            break;
        key = next;
    }
    return first_error;
}

static int remove_run(
    struct task_tracker_bpf *skel,
    const struct cg_run_id *run_id
)
{
    int first_error = 0;
    int result;

    result = remove_cgroups(
        bpf_map__fd(skel->maps.tracked_cgroups),
        run_id
    );
    remember_result(&first_error, result);
    result = remove_tasks(bpf_map__fd(skel->maps.tracked_tasks), run_id);
    remember_result(&first_error, result);
    result = remove_processes(
        bpf_map__fd(skel->maps.process_tasks),
        run_id
    );
    remember_result(&first_error, result);
    if (bpf_map_delete_elem(
            bpf_map__fd(skel->maps.run_metrics),
            run_id
        ) < 0)
        remember_errno(&first_error);
    return first_error;
}

static int dispatch(
    struct task_tracker_bpf *skel,
    const struct cg_request *request,
    struct cg_response *response
)
{
    if (request->magic != CG_TRACKER_MAGIC ||
        request->version != CG_TRACKER_VERSION)
        return -EPROTO;

    switch (request->op) {
    case CG_OP_HEALTH:
        return 0;
    case CG_OP_REGISTER_CGROUP:
        return register_cgroup(skel, request);
    case CG_OP_REGISTER_ROOT:
        return register_root(skel, request);
    case CG_OP_SNAPSHOT:
        return snapshot(skel, request, &response->metrics);
    case CG_OP_REMOVE:
        return remove_run(skel, &request->run_id);
    default:
        return -EINVAL;
    }
}

static int configure_socket(const char *socket_path)
{
    struct sockaddr_un address = {
        .sun_family = AF_UNIX,
    };
    struct group *group;
    int server_fd;

    if (strlen(socket_path) >= sizeof(address.sun_path)) {
        errno = ENAMETOOLONG;
        return -1;
    }
    strcpy(address.sun_path, socket_path);
    unlink(socket_path);

    server_fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
    if (server_fd < 0)
        return -1;
    if (bind(server_fd, (struct sockaddr *)&address, sizeof(address)) < 0)
        goto error;

    group = getgrnam("codeguard");
    if (!group) {
        errno = ENOENT;
        goto error;
    }
    if (chown(socket_path, 0, group->gr_gid) < 0 ||
        chmod(socket_path, 0660) < 0 ||
        listen(server_fd, 64) < 0)
        goto error;
    return server_fd;

error:
    close(server_fd);
    unlink(socket_path);
    return -1;
}

static void serve_client(
    struct task_tracker_bpf *skel,
    int client_fd
)
{
    struct pollfd client_poll_fds[2] = {
        {
            .fd = client_fd,
            .events = POLLIN,
        },
        {
            .fd = signal_pipe_fds[0],
            .events = POLLIN,
        },
    };
    struct cg_request request = {};
    struct cg_response response = {
        .magic = CG_TRACKER_MAGIC,
        .version = CG_TRACKER_VERSION,
    };
    ssize_t received;

    for (;;) {
        int poll_result = poll(client_poll_fds, 2, -1);

        if (poll_result < 0) {
            if (errno == EINTR) {
                if (stop_requested)
                    return;
                continue;
            }
            perror("task tracker client poll");
            return;
        }
        if ((client_poll_fds[1].revents & POLLIN) || stop_requested)
            return;
        if (client_poll_fds[0].revents & POLLIN)
            break;
        return;
    }

    received = recv(client_fd, &request, sizeof(request), 0);
    if (stop_requested)
        return;

    if (received != (ssize_t)sizeof(request))
        response.status = -EMSGSIZE;
    else
        response.status = dispatch(skel, &request, &response);

    if (send(client_fd, &response, sizeof(response), MSG_NOSIGNAL) < 0)
        perror("task tracker send");
}

int main(int argc, char **argv)
{
    const char *socket_path = DEFAULT_SOCKET_PATH;
    struct task_tracker_bpf *skel = NULL;
    struct pollfd poll_fds[2] = {};
    int server_fd = -1;
    int exit_code = EXIT_FAILURE;

    if (argc == 3 && strcmp(argv[1], "--socket") == 0)
        socket_path = argv[2];
    else if (argc != 1) {
        fprintf(stderr, "usage: %s [--socket PATH]\n", argv[0]);
        return EXIT_FAILURE;
    }

    if (pipe2(signal_pipe_fds, O_NONBLOCK | O_CLOEXEC) < 0) {
        perror("task tracker signal pipe");
        goto cleanup;
    }
    if (install_signal_handlers() < 0) {
        perror("task tracker signal handler");
        goto cleanup;
    }
    skel = task_tracker_bpf__open_and_load();
    if (!skel) {
        fprintf(stderr, "failed to load task tracker BPF program\n");
        goto cleanup;
    }
    if (task_tracker_bpf__attach(skel)) {
        fprintf(stderr, "failed to attach task tracker BPF program\n");
        goto cleanup;
    }

    server_fd = configure_socket(socket_path);
    if (server_fd < 0) {
        perror("task tracker socket");
        goto cleanup;
    }

    poll_fds[0].fd = server_fd;
    poll_fds[0].events = POLLIN;
    poll_fds[1].fd = signal_pipe_fds[0];
    poll_fds[1].events = POLLIN;

    while (!stop_requested) {
        int poll_result = poll(poll_fds, 2, -1);
        int client_fd;

        if (poll_result < 0) {
            if (errno == EINTR)
                continue;
            perror("task tracker poll");
            goto cleanup;
        }
        if (poll_fds[1].revents & POLLIN)
            break;
        if (stop_requested)
            break;
        if (!(poll_fds[0].revents & POLLIN)) {
            errno = EIO;
            perror("task tracker socket event");
            goto cleanup;
        }

        client_fd = accept4(server_fd, NULL, NULL, SOCK_CLOEXEC);

        if (client_fd < 0) {
            if (errno == EINTR)
                continue;
            perror("task tracker accept");
            goto cleanup;
        }
        serve_client(skel, client_fd);
        close(client_fd);
    }
    exit_code = EXIT_SUCCESS;

cleanup:
    if (server_fd >= 0)
        close(server_fd);
    if (signal_pipe_fds[0] >= 0)
        close(signal_pipe_fds[0]);
    signal_pipe_fds[0] = -1;
    if (signal_pipe_fds[1] >= 0)
        close(signal_pipe_fds[1]);
    signal_pipe_fds[1] = -1;
    unlink(socket_path);
    task_tracker_bpf__destroy(skel);
    return exit_code;
}
