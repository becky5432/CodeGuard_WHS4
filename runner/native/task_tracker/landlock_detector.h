#ifndef CODEGUARD_LANDLOCK_DETECTOR_H
#define CODEGUARD_LANDLOCK_DETECTOR_H

#include <stdbool.h>
#include <stdio.h>
#include "task_tracker_shared.h"

struct landlock_detector_bpf;
struct cg_fs_detector {
    struct landlock_detector_bpf *skel;
    int tracked_fd;
    int events_fd;
    bool active;
};

int cg_fs_parse_format(FILE *format, unsigned int *domain, unsigned int *blockers);
int cg_fs_validate_btf(unsigned int domain, unsigned int blockers);
void cg_fs_open(struct cg_fs_detector *detector, int tracked_fd);
void cg_fs_close(struct cg_fs_detector *detector);
void cg_fs_register(struct cg_fs_detector *detector, __u64 cgroup_id);
int cg_fs_remove(struct cg_fs_detector *detector, __u64 cgroup_id);
int cg_fs_snapshot(struct cg_fs_detector *detector,
                   const struct cg_run_id *run_id, struct cg_fs_response *response);
#endif
