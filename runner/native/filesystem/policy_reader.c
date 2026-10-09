#define _GNU_SOURCE
#include "landlock_policy.h"
#include <stdlib.h>
#include <unistd.h>

static int valid_path(const char *path)
{
    const char *component;
    if (path[0] != '/' || path[1] == '\0' || strlen(path) >= CG_FS_PATH_SIZE)
        return 0;
    component = path + 1;
    while (*component) {
        const char *end = strchr(component, '/');
        size_t length = end ? (size_t)(end - component) : strlen(component);
        if (!length || (length == 1 && component[0] == '.') ||
            (length == 2 && component[0] == '.' && component[1] == '.')) return 0;
        if (!end) break;
        component = end + 1;
        if (!*component) return 0;
    }
    return !strpbrk(path, "\t\r\n");
}

int cg_fs_read_policy(int fd, struct cg_fs_policy *out, struct cg_fs_error *error)
{
    char *data = malloc(CG_FS_MAX_BYTES + 2), *line, *end;
    size_t used = 0;
    int result = -1;
    if (!out || fd < 0) {
        free(data);
        return cg_fs_fail(error, EINVAL, "policy_read", NULL);
    }
    memset(out, 0, sizeof(*out));
    if (!data) return cg_fs_fail(error, ENOMEM, "policy_read", NULL);
    for (;;) {
        ssize_t count = read(fd, data + used, CG_FS_MAX_BYTES + 1 - used);
        if (count < 0) {
            if (errno == EINTR) continue;
            cg_fs_fail(error, errno, "policy_read", NULL);
            goto done;
        }
        if (!count) break;
        used += (size_t)count;
        if (used > CG_FS_MAX_BYTES) {
            cg_fs_fail(error, E2BIG, "policy_size", NULL);
            goto done;
        }
    }
    if (!used || data[used - 1] != '\n' || memchr(data, 0, used)) goto invalid;
    data[used] = 0;
    end = strchr(data, '\n');
    if (!end || end - data != 71 || memcmp(data, "CGFS\t2\t", 7)) goto invalid;
    *end = 0;
    if (!cg_fs_valid_id(data + 7)) goto invalid;
    memcpy(out->policy_id, data + 7, 65);
    out->version = 2;
    line = end + 1;
    while (*line) {
        char *tab;
        struct cg_fs_rule *rule;
        size_t i;
        end = strchr(line, '\n');
        if (!end || out->count == CG_FS_MAX_RULES) goto invalid;
        *end = 0;
        tab = strchr(line, '\t');
        if (!tab || strchr(tab + 1, '\t')) goto invalid;
        *tab++ = 0;
        if (!valid_path(tab)) goto invalid;
        for (i = 0; i < out->count; i++)
            if (!strcmp(out->rules[i].path, tab)) goto invalid;
        rule = &out->rules[out->count];
        if (!strcmp(line, "FILE_READ")) rule->profile = CG_FILE_READ;
        else if (!strcmp(line, "FILE_EXEC")) rule->profile = CG_FILE_EXEC;
        else if (!strcmp(line, "DEVICE_READ")) rule->profile = CG_DEVICE_READ;
        else if (!strcmp(line, "DEVICE_RW")) rule->profile = CG_DEVICE_RW;
        else if (!strcmp(line, "WORK")) rule->profile = CG_WORK;
        else goto invalid;
        strcpy(rule->path, tab);
        out->count++;
        line = end + 1;
    }
    if (!out->count) goto invalid;
    result = 0;
    goto done;
invalid:
    cg_fs_fail(error, EINVAL, "policy_parse", NULL);
done:
    if (result) memset(out, 0, sizeof(*out));
    free(data);
    return result;
}
