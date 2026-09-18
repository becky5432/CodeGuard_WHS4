#include "vmlinux.h"

#include <bpf/bpf_core_read.h>
#include <bpf/bpf_helpers.h>
#include <bpf/bpf_tracing.h>
#include <stdbool.h>

#include "task_tracker_shared.h"

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 32768);
    __type(key, struct cg_task_key);
    __type(value, struct cg_task_value);
} tracked_tasks SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, struct cg_cgroup_key);
    __type(value, struct cg_cgroup_value);
} tracked_cgroups SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 8192);
    __type(key, struct cg_process_key);
    __type(value, struct cg_process_value);
} process_tasks SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, struct cg_run_id);
    __type(value, struct cg_run_metrics);
} run_metrics SEC(".maps");

static __always_inline void set_error(
    struct cg_run_metrics *metrics,
    __u32 error
)
{
    if (!metrics)
        return;

    bpf_spin_lock(&metrics->lock);
    metrics->error_flags |= error;
    bpf_spin_unlock(&metrics->lock);
}

static __always_inline void update_peak_locked(
    struct cg_run_metrics *metrics
)
{
    if (metrics->container_task_current > metrics->container_task_peak) {
        metrics->container_task_peak = metrics->container_task_current;
        metrics->process_at_pids_peak = metrics->process_current;
        metrics->thread_at_pids_peak = metrics->thread_current;
    }
}

SEC("tp_btf/sched_process_fork")
int BPF_PROG(
    handle_sched_process_fork,
    struct task_struct *parent,
    struct task_struct *child
)
{
    struct cg_cgroup_key cgroup_key = {
        .cgroup_id = bpf_get_current_cgroup_id(),
    };
    struct cg_cgroup_value *cgroup_value;
    struct cg_run_metrics *metrics;
    struct cg_task_key parent_key = {};
    struct cg_task_key child_key = {};
    struct cg_task_value *parent_value;
    struct cg_task_value child_value = {};
    struct cg_process_key process_key = {};
    struct cg_process_value initial_process = {};
    struct cg_process_value *process_value;
    bool user_child = false;
    bool new_process = false;
    bool additional_thread = false;
    long result;

    cgroup_value = bpf_map_lookup_elem(&tracked_cgroups, &cgroup_key);
    if (!cgroup_value)
        return 0;

    metrics = bpf_map_lookup_elem(&run_metrics, &cgroup_value->run_id);
    if (!metrics)
        return 0;

    parent_key.tid = BPF_CORE_READ(parent, pid);
    child_key.tid = BPF_CORE_READ(child, pid);
    parent_value = bpf_map_lookup_elem(&tracked_tasks, &parent_key);

    if (parent_value) {
        user_child = true;
        child_value.run_id = parent_value->run_id;
        child_value.tgid = BPF_CORE_READ(child, tgid);
        additional_thread = child_key.tid != child_value.tgid;

        result = bpf_map_update_elem(
            &tracked_tasks,
            &child_key,
            &child_value,
            BPF_NOEXIST
        );
        if (result) {
            user_child = false;
            set_error(metrics, CG_ERR_TASK_MAP);
        }
    }

    if (user_child) {
        process_key.run_id = child_value.run_id;
        process_key.tgid = child_value.tgid;
        process_value = bpf_map_lookup_elem(&process_tasks, &process_key);

        if (!process_value) {
            initial_process.live_tasks = 1;
            result = bpf_map_update_elem(
                &process_tasks,
                &process_key,
                &initial_process,
                BPF_NOEXIST
            );
            if (result == 0) {
                new_process = true;
            } else {
                process_value = bpf_map_lookup_elem(
                    &process_tasks,
                    &process_key
                );
                if (!process_value) {
                    bpf_map_delete_elem(&tracked_tasks, &child_key);
                    user_child = false;
                    set_error(metrics, CG_ERR_PROCESS_MAP);
                }
            }
        }

        if (user_child && process_value) {
            bpf_spin_lock(&process_value->lock);
            process_value->live_tasks += 1;
            bpf_spin_unlock(&process_value->lock);
        }
    }

    bpf_spin_lock(&metrics->lock);
    metrics->container_task_current += 1;
    if (user_child) {
        if (new_process)
            metrics->process_current += 1;
        if (additional_thread)
            metrics->thread_current += 1;
    }
    update_peak_locked(metrics);
    bpf_spin_unlock(&metrics->lock);
    return 0;
}

SEC("tp_btf/sched_process_exit")
int BPF_PROG(handle_sched_process_exit, struct task_struct *task)
{
    struct cg_cgroup_key cgroup_key = {
        .cgroup_id = bpf_get_current_cgroup_id(),
    };
    struct cg_cgroup_value *cgroup_value;
    struct cg_run_metrics *metrics;
    struct cg_task_key task_key = {};
    struct cg_task_value *task_value;
    struct cg_task_value task_copy = {};
    struct cg_process_key process_key = {};
    struct cg_process_value *process_value;
    bool tracked_user_task = false;
    bool additional_thread = false;
    bool last_process_task = false;
    bool process_counter_error = false;
    __u32 previous_live_tasks = 0;

    cgroup_value = bpf_map_lookup_elem(&tracked_cgroups, &cgroup_key);
    if (!cgroup_value)
        return 0;

    metrics = bpf_map_lookup_elem(&run_metrics, &cgroup_value->run_id);
    if (!metrics)
        return 0;

    task_key.tid = BPF_CORE_READ(task, pid);
    task_value = bpf_map_lookup_elem(&tracked_tasks, &task_key);
    if (task_value) {
        tracked_user_task = true;
        task_copy = *task_value;
        additional_thread = task_key.tid != task_copy.tgid;
        process_key.run_id = task_copy.run_id;
        process_key.tgid = task_copy.tgid;
        process_value = bpf_map_lookup_elem(&process_tasks, &process_key);

        if (!process_value) {
            set_error(metrics, CG_ERR_PROCESS_MAP);
        } else {
            bpf_spin_lock(&process_value->lock);
            previous_live_tasks = process_value->live_tasks;
            if (previous_live_tasks == 0) {
                process_counter_error = true;
            } else {
                process_value->live_tasks = previous_live_tasks - 1;
                last_process_task = previous_live_tasks == 1;
            }
            bpf_spin_unlock(&process_value->lock);
        }

        bpf_map_delete_elem(&tracked_tasks, &task_key);
        if (last_process_task)
            bpf_map_delete_elem(&process_tasks, &process_key);
    }

    bpf_spin_lock(&metrics->lock);
    if (process_counter_error)
        metrics->error_flags |= CG_ERR_COUNTER;
    if (metrics->container_task_current > 0) {
        metrics->container_task_current -= 1;
    } else {
        metrics->error_flags |= CG_ERR_COUNTER;
    }

    if (tracked_user_task) {
        if (additional_thread) {
            if (metrics->thread_current > 0)
                metrics->thread_current -= 1;
            else
                metrics->error_flags |= CG_ERR_COUNTER;
        }
        if (last_process_task) {
            if (metrics->process_current > 0)
                metrics->process_current -= 1;
            else
                metrics->error_flags |= CG_ERR_COUNTER;
        }
    }
    bpf_spin_unlock(&metrics->lock);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
