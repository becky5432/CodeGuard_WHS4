/* One trusted test probe, compiled once and run in a fresh container per case. */
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int finish(int rc, int error, int verified) {
    printf("CGFS_RESULT {\"rc\":%d,\"errno\":%d,\"verified\":%d}\n",
           rc < 0 ? -1 : 0, error, !!verified);
    return 0;
}

static int read_line(char *buffer, size_t size) {
    if (!fgets(buffer, (int)size, stdin)) return -1;
    buffer[strcspn(buffer, "\r\n")] = '\0';
    return 0;
}

static int exists(const char *path) {
    struct stat st;
    return stat(path, &st) == 0;
}

static int parent_exists(const char *path) {
    char copy[4096];
    if (strlen(path) >= sizeof(copy)) return 0;
    strcpy(copy, path);
    char *slash = strrchr(copy, '/');
    if (!slash) return exists(".");
    if (slash == copy) slash[1] = '\0';
    else *slash = '\0';
    struct stat st;
    return stat(copy, &st) == 0 && S_ISDIR(st.st_mode);
}

static int seed(const char *path) {
    int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
    if (fd < 0) return -1;
    ssize_t count = write(fd, "BEFORE", 6);
    int error = errno;
    if (close(fd) < 0 && count == 6) return -1;
    if (count != 6) { errno = error ? error : EIO; return -1; }
    return 0;
}

static ssize_t prefix(const char *path, char *buffer, size_t size) {
    int fd = open(path, O_RDONLY | O_NONBLOCK);
    if (fd < 0) return -1;
    ssize_t count = read(fd, buffer, size);
    int error = errno;
    close(fd);
    errno = error;
    return count;
}

static int content_is(const char *path, const char *expected) {
    char data[64];
    ssize_t count = prefix(path, data, sizeof(data));
    return count == (ssize_t)strlen(expected) && !memcmp(data, expected, (size_t)count);
}

int main(int argc, char **argv) {
    if (argc > 1 && !strcmp(argv[1], "--cg-child")) return finish(0, 0, 1);
    char operation[64], path[4096], aux[4096];
    if (read_line(operation, sizeof(operation)) || read_line(path, sizeof(path)) ||
        read_line(aux, sizeof(aux))) return finish(-1, EINVAL, 0);
    int rc = -1, error = 0, verified = 0;
    if (!strcmp(operation, "read")) {
        verified = exists(path);
        char data[32];
        rc = (int)prefix(path, data, sizeof(data));
        error = rc < 0 ? errno : 0;
    } else if (!strcmp(operation, "list")) {
        verified = exists(path);
        DIR *directory = opendir(path);
        if (!directory) error = errno;
        else {
            errno = 0;
            while (readdir(directory)) {}
            error = errno;
            rc = error ? -1 : 0;
            closedir(directory);
        }
    } else if (!strcmp(operation, "create") || !strcmp(operation, "relative_create")) {
        verified = parent_exists(path);
        int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (fd < 0) error = errno;
        else { rc = 0; close(fd); verified = verified && exists(path); }
    } else if (!strcmp(operation, "mkdir")) {
        verified = parent_exists(path);
        rc = mkdir(path, 0700); error = rc < 0 ? errno : 0;
        struct stat st;
        if (rc == 0) verified = verified && stat(path, &st) == 0 && S_ISDIR(st.st_mode);
    } else if (!strcmp(operation, "write") || !strcmp(operation, "append") || !strcmp(operation, "truncate")) {
        if (seed(path)) return finish(-1, errno, 0);
        if (!strcmp(operation, "truncate")) {
            rc = truncate(path, 3); error = rc < 0 ? errno : 0;
            verified = rc == 0 && content_is(path, "BEF");
        } else {
            int flags = O_WRONLY | (!strcmp(operation, "append") ? O_APPEND : O_TRUNC);
            int fd = open(path, flags);
            if (fd < 0) error = errno;
            else {
                rc = write(fd, "AFTER", 5) == 5 ? 0 : -1;
                error = rc < 0 ? (errno ? errno : EIO) : 0;
                if (close(fd) < 0 && rc == 0) { rc = -1; error = errno; }
            }
            verified = rc == 0 && content_is(path, !strcmp(operation, "append") ? "BEFOREAFTER" : "AFTER");
        }
    } else if (!strcmp(operation, "delete")) {
        if (seed(path)) return finish(-1, errno, 0);
        rc = unlink(path); error = rc < 0 ? errno : 0;
        struct stat st;
        verified = rc == 0 && lstat(path, &st) < 0 && errno == ENOENT;
    } else if (!strcmp(operation, "rmdir")) {
        if (mkdir(path, 0700)) return finish(-1, errno, 0);
        rc = rmdir(path); error = rc < 0 ? errno : 0;
        struct stat st;
        verified = rc == 0 && lstat(path, &st) < 0 && errno == ENOENT;
    } else if (!strcmp(operation, "rename") || !strcmp(operation, "move_dir")) {
        int is_dir = !strcmp(operation, "move_dir");
        if (is_dir ? mkdir(path, 0700) : seed(path)) return finish(-1, errno, 0);
        rc = rename(path, aux); error = rc < 0 ? errno : 0;
        struct stat st;
        int old_missing = lstat(path, &st) < 0 && errno == ENOENT;
        int new_ok = is_dir ? (stat(aux, &st) == 0 && S_ISDIR(st.st_mode)) : content_is(aux, "BEFORE");
        verified = rc == 0 && old_missing && new_ok;
    } else if (!strcmp(operation, "write_existing")) {
        char before[64], after[64];
        ssize_t count = prefix(path, before, sizeof(before));
        if (count < 0) return finish(-1, errno, 0);
        int fd = open(path, O_WRONLY);
        if (fd < 0) {
            error = errno;
            ssize_t after_count = prefix(path, after, sizeof(after));
            verified = count == after_count && !memcmp(before, after, (size_t)count);
        } else {
            /* A regression affects only this disposable container's root layer. */
            rc = write(fd, "X", 1) == 1 ? 0 : -1;
            error = rc < 0 ? errno : 0;
            close(fd); verified = 1;
        }
    } else if (!strcmp(operation, "delete_existing")) {
        verified = exists(path);
        rc = unlink(path); error = rc < 0 ? errno : 0;
        if (rc < 0) verified = verified && exists(path);
    } else if (!strcmp(operation, "device_write")) {
        verified = exists(path);
        int fd = open(path, O_WRONLY | O_NONBLOCK);
        if (fd < 0) error = errno;
        else {
            rc = write(fd, "X", 1) == 1 ? 0 : -1;
            error = rc < 0 ? errno : 0; close(fd);
        }
    } else if (!strcmp(operation, "symlink_write")) {
        if (symlink(aux, path)) return finish(-1, errno, 0);
        verified = parent_exists(aux);
        int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC, 0600);
        if (fd < 0) error = errno;
        else { rc = 0; close(fd); }
    } else if (!strcmp(operation, "dirfd_write")) {
        int directory = open(path, O_PATH | O_DIRECTORY);
        if (directory < 0) return finish(-1, errno, 0);
        verified = 1;
        int fd = openat(directory, aux, O_WRONLY | O_CREAT | O_EXCL, 0600);
        if (fd < 0) error = errno;
        else { rc = 0; close(fd); }
        close(directory);
    } else if (!strcmp(operation, "exec") || !strcmp(operation, "exec_loader")) {
        verified = exists(path);
        if (!strcmp(operation, "exec_loader")) execl(path, path, "/workspace/main", "--cg-child", (char *)0);
        else if (!strcmp(path, "/workspace/main")) execl(path, path, "--cg-child", (char *)0);
        else execl(path, path, "-c", "exit 0", (char *)0);
        error = errno;
    } else if (!strcmp(operation, "fake")) {
        fputs("openat(AT_FDCWD, \"/etc/fake\", O_WRONLY) = -1 EROFS (Read-only file system)\n", stderr);
        rc = 0; verified = 1;
    } else return finish(-1, EINVAL, 0);
    return finish(rc, error, verified);
}
