#ifndef CODEGUARD_TASK_TRACKER_SHARED_H
#define CODEGUARD_TASK_TRACKER_SHARED_H

#ifndef __BPF__
#include <linux/bpf.h>
#include <linux/types.h>
#endif

#define CG_TRACKER_MAGIC 0x43475452
#define CG_TRACKER_VERSION 2

enum cg_tracker_op {
    CG_OP_HEALTH = 1,
    CG_OP_REGISTER_CGROUP = 2,
    CG_OP_REGISTER_ROOT = 3,
    CG_OP_SNAPSHOT = 4,
    CG_OP_REMOVE = 5,
};

enum cg_measurement_error {
    CG_ERR_NONE = 0,
    CG_ERR_TASK_MAP = 1 << 0,
    CG_ERR_PROCESS_MAP = 1 << 1,
    CG_ERR_COUNTER = 1 << 2,
    CG_ERR_CAPACITY = 1 << 3,
};

struct cg_run_id {
    __u8 bytes[16];
};

struct cg_task_key {
    __u32 tid;
};

struct cg_task_value {
    struct cg_run_id run_id;
    __u32 tgid;
};

struct cg_cgroup_key {
    __u64 cgroup_id;
};

struct cg_cgroup_value {
    struct cg_run_id run_id;
};

struct cg_process_key {
    struct cg_run_id run_id;
    __u32 tgid;
};

struct cg_process_value {
    struct bpf_spin_lock lock;
    __u32 live_tasks;
};

struct cg_run_metrics {
    struct bpf_spin_lock lock;
    __u32 container_task_current;
    __u32 container_task_peak;
    __u32 user_task_current;
    __u32 user_task_peak;
    __u32 process_current;
    __u32 thread_current;
    __u32 process_at_user_task_peak;
    __u32 thread_at_user_task_peak;
    __u32 error_flags;
};

struct cg_peak_snapshot {
    __u32 user_task_peak;
    __u32 process_at_user_task_peak;
    __u32 thread_at_user_task_peak;
    __u32 error_flags;
};

struct cg_request {
    __u32 magic;
    __u16 version;
    __u16 op;
    struct cg_run_id run_id;
    __u64 cgroup_id;
    __u32 root_tid;
    __u32 initial_task_count;
};

struct cg_response {
    __u32 magic;
    __u16 version;
    __u16 reserved;
    __s32 status;
    struct cg_peak_snapshot metrics;
};

_Static_assert(sizeof(struct cg_request) == 40, "cg_request size mismatch");
_Static_assert(sizeof(struct cg_response) == 28, "cg_response size mismatch");

#endif
