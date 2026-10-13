#include "vmlinux.h"
#include <bpf/bpf_helpers.h>
#include "task_tracker_shared.h"

/* libbpf reuses the existing tracker map FD; no new cgroup/run mapping. */
struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, struct cg_cgroup_key);
    __type(value, struct cg_cgroup_value);
} tracked_cgroups SEC(".maps");

struct {
    __uint(type, BPF_MAP_TYPE_HASH);
    __uint(max_entries, 1024);
    __type(key, struct cg_cgroup_key);
    __type(value, struct cg_fs_state);
} filesystem_events SEC(".maps");

/* Validated against both the running tracefs format and kernel BTF. */
const volatile __u32 domain_offset;
const volatile __u32 blockers_offset;

SEC("tracepoint/landlock/landlock_deny_access_fs")
int handle_landlock_deny_access_fs(void *ctx)
{
    struct cg_cgroup_key key = {.cgroup_id = bpf_get_current_cgroup_id()};
    struct cg_fs_event event = {.cgroup_id = key.cgroup_id};
    struct cg_fs_state *slot;
    __u64 pid_tgid;
    int error;

    if (!bpf_map_lookup_elem(&tracked_cgroups, &key))
        return 0;
    slot = bpf_map_lookup_elem(&filesystem_events, &key);
    if (!slot || domain_offset < 8 || domain_offset > 248 ||
        blockers_offset < 8 || blockers_offset > 252)
        return 0;
    if (__sync_val_compare_and_swap(&slot->state, CG_FS_EMPTY, CG_FS_WRITING)
        != CG_FS_EMPTY)
        return 0;

    pid_tgid = bpf_get_current_pid_tgid();
    event.pid = pid_tgid >> 32;
    event.tid = (__u32)pid_tgid;
    event.timestamp_ns = bpf_ktime_get_ns();
    /* Scalar bytes in the trace record only, never an embedded pointer. */
    error = bpf_probe_read_kernel(&event.domain_id, sizeof(event.domain_id),
                                  (char *)ctx + domain_offset);
    error |= bpf_probe_read_kernel(&event.blockers, sizeof(event.blockers),
                                   (char *)ctx + blockers_offset);
    if (error) {
        __sync_val_compare_and_swap(&slot->state, CG_FS_WRITING, CG_FS_READ_ERROR);
        return 0;
    }
    slot->first = event;
    __sync_val_compare_and_swap(&slot->state, CG_FS_WRITING, CG_FS_READY);
    return 0;
}

char LICENSE[] SEC("license") = "GPL";
