#include <errno.h>
#include <string.h>
#include <bpf/bpf.h>
#include <bpf/btf.h>
#include <bpf/libbpf.h>

#include "landlock_detector.h"
#include "landlock_detector.skel.h"

int cg_fs_parse_format(FILE *format, unsigned int *domain, unsigned int *blockers)
{
    char line[1024];
    unsigned int seen = 0, offsets[2] = {};
    bool named = false;

    while (fgets(line, sizeof(line), format)) {
        char declaration[256];
        unsigned int offset, size, sign;
        int index;
        if (!strcmp(line, "name: landlock_deny_access_fs\n"))
            named = true;
        if (sscanf(line, " field:%255[^;]; offset:%u; size:%u; signed:%u;",
                   declaration, &offset, &size, &sign) != 4)
            continue;
        char *name = strrchr(declaration, ' ');
        if (!name)
            continue;
        if (!strcmp(name + 1, "domain_id"))
            index = 0;
        else if (!strcmp(name + 1, "blockers"))
            index = 1;
        else
            continue;
        if ((seen & (1U << index)) || strchr(declaration, '*') ||
            size != (index == 0 ? 8U : 4U) || sign ||
            offset < 8 || offset > 256 - size)
            return -EINVAL;
        offsets[index] = offset;
        seen |= 1U << index;
    }
    if (ferror(format) || !named || seen != 3 ||
        (offsets[0] < offsets[1] + 4 && offsets[1] < offsets[0] + 8))
        return -EINVAL;
    *domain = offsets[0];
    *blockers = offsets[1];
    return 0;
}

static bool scalar_matches(const struct btf *btf, const struct btf_type *record,
                           const char *name, unsigned int offset, unsigned int size)
{
    const struct btf_member *members = btf_members(record);
    for (unsigned int i = 0; i < btf_vlen(record); i++) {
        const char *member_name = btf__name_by_offset(btf, members[i].name_off);
        const struct btf_type *type;
        int id;
        if (!member_name || strcmp(name, member_name))
            continue;
        id = btf__resolve_type(btf, members[i].type);
        type = id > 0 ? btf__type_by_id(btf, id) : NULL;
        return type && btf_is_int(type) &&
            !(btf_int_encoding(type) & BTF_INT_SIGNED) &&
            btf__resolve_size(btf, members[i].type) == size &&
            btf_member_bit_offset(record, i) == offset * 8 &&
            btf_member_bitfield_size(record, i) == 0;
    }
    return false;
}

int cg_fs_validate_btf(unsigned int domain, unsigned int blockers)
{
    struct btf *btf = btf__load_vmlinux_btf();
    const struct btf_type *record;
    int id, result;
    if (!btf || libbpf_get_error(btf))
        return -EINVAL;
    id = btf__find_by_name_kind(btf, "trace_event_raw_landlock_deny_access_fs",
                               BTF_KIND_STRUCT);
    record = id > 0 ? btf__type_by_id(btf, id) : NULL;
    result = record && scalar_matches(btf, record, "domain_id", domain, 8) &&
        scalar_matches(btf, record, "blockers", blockers, 4) ? 0 : -EINVAL;
    btf__free(btf);
    return result;
}

void cg_fs_close(struct cg_fs_detector *detector)
{
    landlock_detector_bpf__destroy(detector->skel);
    detector->skel = NULL;
    detector->active = false;
    detector->events_fd = -1;
}

static void use_fallback(struct cg_fs_detector *detector, const char *reason)
{
    cg_fs_close(detector);
    fprintf(stderr, "event=landlock_ebpf_unavailable reason=%s "
            "filesystem_detection=strace_fallback\n", reason);
}

void cg_fs_open(struct cg_fs_detector *detector, int tracked_fd)
{
    FILE *format;
    unsigned int domain, blockers;
    int result;
    const char *reason = "tracepoint_format";
    *detector = (struct cg_fs_detector){.tracked_fd = tracked_fd, .events_fd = -1};
    format = fopen("/sys/kernel/tracing/events/landlock/landlock_deny_access_fs/format", "r");
    if (!format)
        format = fopen("/sys/kernel/debug/tracing/events/landlock/landlock_deny_access_fs/format", "r");
    if (!format)
        goto fallback;
    result = cg_fs_parse_format(format, &domain, &blockers);
    fclose(format);
    if (result)
        goto fallback;
    reason = "btf_layout";
    if (cg_fs_validate_btf(domain, blockers))
        goto fallback;
    reason = "bpf_load";
    detector->skel = landlock_detector_bpf__open();
    if (!detector->skel)
        goto fallback;
    detector->skel->rodata->domain_offset = domain;
    detector->skel->rodata->blockers_offset = blockers;
    if (bpf_map__reuse_fd(detector->skel->maps.tracked_cgroups, tracked_fd) ||
        landlock_detector_bpf__load(detector->skel))
        goto fallback;
    reason = "tracepoint_attach";
    detector->skel->links.handle_landlock_deny_access_fs =
        bpf_program__attach_tracepoint(detector->skel->progs.handle_landlock_deny_access_fs,
                                      "landlock", "landlock_deny_access_fs");
    if (!detector->skel->links.handle_landlock_deny_access_fs ||
        libbpf_get_error(detector->skel->links.handle_landlock_deny_access_fs)) {
        detector->skel->links.handle_landlock_deny_access_fs = NULL;
        goto fallback;
    }
    detector->events_fd = bpf_map__fd(detector->skel->maps.filesystem_events);
    detector->active = true;
    fprintf(stderr, "event=landlock_ebpf_active domain_offset=%u blockers_offset=%u\n",
            domain, blockers);
    return;
fallback:
    use_fallback(detector, reason);
}

void cg_fs_register(struct cg_fs_detector *detector, __u64 cgroup_id)
{
    struct cg_cgroup_key key = {.cgroup_id = cgroup_id};
    struct cg_fs_state empty = {};
    if (!detector->active)
        return;
    /* Never reuse evidence left by a failed cleanup of an older run. */
    if ((bpf_map_delete_elem(detector->events_fd, &key) && errno != ENOENT) ||
        bpf_map_update_elem(detector->events_fd, &key, &empty, BPF_NOEXIST))
        use_fallback(detector, "event_registration");
}

int cg_fs_remove(struct cg_fs_detector *detector, __u64 cgroup_id)
{
    struct cg_cgroup_key key = {.cgroup_id = cgroup_id};
    if (detector->active && bpf_map_delete_elem(detector->events_fd, &key) && errno != ENOENT)
        return -errno;
    return 0;
}

int cg_fs_snapshot(struct cg_fs_detector *detector, const struct cg_run_id *run_id,
                   struct cg_fs_response *response)
{
    struct cg_cgroup_key key = {}, next = {};
    response->run_id = *run_id;
    if (!detector->active)
        return 0;
    int result = bpf_map_get_next_key(detector->tracked_fd, NULL, &key);
    while (!result) {
        struct cg_cgroup_value value = {};
        struct cg_fs_state slot = {};
        if (bpf_map_lookup_elem(detector->tracked_fd, &key, &value))
            return -errno;
        if (!memcmp(value.run_id.bytes, run_id->bytes, sizeof(run_id->bytes))) {
            if (bpf_map_lookup_elem(detector->events_fd, &key, &slot))
                return -errno;
            if (slot.state == CG_FS_WRITING || slot.state == CG_FS_READ_ERROR)
                return -EAGAIN; /* Partial evidence never disables strace fallback. */
            if (slot.state != CG_FS_EMPTY && slot.state != CG_FS_READY)
                return -EIO;
            response->flags = CG_FS_ACTIVE;
            if (slot.state == CG_FS_READY) {
                response->flags |= CG_FS_HAS_EVENT;
                response->first = slot.first;
            }
            return 0;
        }
        result = bpf_map_get_next_key(detector->tracked_fd, &key, &next);
        if (!result)
            key = next;
    }
    return errno == ENOENT ? 0 : -errno;
}
