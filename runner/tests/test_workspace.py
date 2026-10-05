import copy
import io
import os
import shutil
import subprocess
import tarfile
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import docker
from requests.exceptions import ReadTimeout

from runner.config import settings
from runner.exceptions import CleanupError, SecurityVerificationError, WorkspaceError
from runner.pipeline import workspace as ws


def tar_bytes(name, content, *, mode=0o444, uid=0, gid=0, kind=tarfile.REGTYPE):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        member = tarfile.TarInfo(name)
        member.size = len(content) if kind == tarfile.REGTYPE else 0
        member.mode, member.uid, member.gid, member.type = mode, uid, gid, kind
        archive.addfile(member, io.BytesIO(content) if member.isreg() else None)
    return buf.getvalue()


class WorkspaceTests(unittest.TestCase):
    def workspace(self):
        return ws.VolumeWorkspace(uuid4(), "job-app", "job-input", "job-work")

    def test_workspace_is_frozen_and_alias_is_app_only(self):
        self.assertIn("app_volume", ws.VolumeWorkspace.__dataclass_fields__)
        workspace = self.workspace()
        self.assertEqual(workspace.volume_name, "job-app")
        with self.assertRaises(FrozenInstanceError): workspace.work_volume = "other"

    def test_create_workspace_creates_three_independent_labeled_volumes(self):
        client = MagicMock()
        client.volumes.create.side_effect = lambda **kw: MagicMock()
        job_id = uuid4()
        workspace = ws.create_workspace(client, job_id)
        calls = client.volumes.create.call_args_list
        self.assertEqual(len(calls), 3)
        for purpose, call in zip(("app", "input", "work"), calls):
            name = call.kwargs["name"]
            self.assertTrue(name.startswith(f"{settings.volume_name_prefix}{job_id}-"))
            self.assertTrue(name.endswith(f"-{purpose}"))
            nonce = name[len(f"{settings.volume_name_prefix}{job_id}-"):-len(f"-{purpose}")]
            self.assertEqual(len(nonce), 32)
            self.assertTrue(all(c in "0123456789abcdef" for c in nonce))
            self.assertEqual(call.kwargs, {"name": name, "labels": {
                "codeguard.managed": "true", "codeguard.job_id": str(job_id),
                "codeguard.purpose": purpose}})
            self.assertEqual(getattr(workspace, f"{purpose}_volume"), name)
        nonces = {c.kwargs["name"].rsplit("-", 2)[1] for c in calls}
        self.assertEqual(len(nonces), 1)

    def test_same_job_id_creations_never_reuse_another_instances_volumes(self):
        client = MagicMock()
        job_id = uuid4()
        first = ws.create_workspace(client, job_id)
        second = ws.create_workspace(client, job_id)
        first_names = {first.app_volume, first.input_volume, first.work_volume}
        second_names = {second.app_volume, second.input_volume, second.work_volume}
        self.assertFalse(first_names & second_names)

    def test_partial_cleanup_never_removes_prior_instance_with_same_job_id(self):
        client, volumes = MagicMock(), {}
        count = 0
        def create(**kw):
            nonlocal count
            count += 1
            if count == 5: raise docker.errors.APIError("second instance input creation failed")
            return volumes.setdefault(kw["name"], MagicMock())
        client.volumes.create.side_effect = create
        job_id = uuid4()
        first = ws.create_workspace(client, job_id)
        with self.assertRaises(WorkspaceError): ws.create_workspace(client, job_id)
        for name in (first.app_volume, first.input_volume, first.work_volume):
            volumes[name].remove.assert_not_called()
        newly_created = [volume for name, volume in volumes.items()
                         if name not in (first.app_volume, first.input_volume, first.work_volume)]
        self.assertEqual(len(newly_created), 1)
        newly_created[0].remove.assert_called_once_with(force=True)
        client.volumes.get.assert_not_called()

    def test_create_workspace_rolls_back_only_created_volumes(self):
        client, first, second = MagicMock(), MagicMock(), MagicMock()
        first.remove.side_effect = docker.errors.APIError("remove failed")
        client.volumes.create.side_effect = [first, second, docker.errors.APIError("create failed")]
        with self.assertRaises(WorkspaceError) as caught: ws.create_workspace(client, uuid4())
        first.remove.assert_called_once_with(force=True)
        second.remove.assert_called_once_with(force=True)
        client.volumes.get.assert_not_called()
        self.assertTrue(caught.exception.details["cleanup_errors"])

    def test_create_workspace_wraps_first_create_failure(self):
        client = MagicMock()
        client.volumes.create.side_effect = docker.errors.APIError("failed")
        with self.assertRaises(WorkspaceError): ws.create_workspace(client, uuid4())
        client.volumes.get.assert_not_called()

    def test_source_archive_is_single_fixed_file_with_owner(self):
        for language, name in (("C", "main.c"), ("CPP", "main.cpp")):
            code = "// 한글\nint main() { return 0; }"
            with tarfile.open(fileobj=io.BytesIO(ws.build_source_archive(language, code))) as archive:
                self.assertEqual(archive.getnames(), [name])
                member = archive.getmember(name)
                self.assertEqual((member.uid, member.gid, member.mode), (10001, 10001, 0o600))
                self.assertTrue(member.isreg())
                self.assertEqual(archive.extractfile(member).read().decode(), code)

    def test_source_archive_rejects_unsupported_language(self):
        with self.assertRaises(WorkspaceError): ws.build_source_archive("PYTHON", "print('hello')")

    def test_stdin_archive_is_separate_even_when_empty(self):
        self.assertTrue(callable(getattr(ws, "build_stdin_archive", None)))
        for stdin in ("", "21\n", "한글"):
            with tarfile.open(fileobj=io.BytesIO(ws.build_stdin_archive(stdin))) as archive:
                self.assertEqual(archive.getnames(), ["stdin"])
                member = archive.getmember("stdin")
                self.assertEqual((member.uid, member.gid, member.mode), (10001, 10001, 0o444))
                self.assertEqual(archive.extractfile(member).read().decode(), stdin)

    def test_remove_workspace_attempts_all_volumes_on_failure(self):
        self.assertIn("app_volume", ws.VolumeWorkspace.__dataclass_fields__)
        client = MagicMock()
        volumes = [MagicMock(), MagicMock(), MagicMock()]
        volumes[0].remove.side_effect = docker.errors.APIError("remove failed")
        client.volumes.get.side_effect = volumes
        with self.assertRaises(CleanupError): ws.remove_workspace(client, self.workspace())
        self.assertEqual([c.args[0] for c in client.volumes.get.call_args_list],
                         ["job-app", "job-input", "job-work"])
        for volume in volumes: volume.remove.assert_called_once_with(force=True)

    def test_remove_workspace_is_idempotent_for_missing_volumes(self):
        self.assertIn("app_volume", ws.VolumeWorkspace.__dataclass_fields__)
        client = MagicMock()
        client.volumes.get.side_effect = docker.errors.NotFound("missing")
        ws.remove_workspace(client, self.workspace())
        self.assertEqual(client.volumes.get.call_count, 3)

    def test_execution_mounts_are_independent_and_trace_is_anonymous(self):
        self.assertTrue(callable(getattr(ws, "execution_mounts", None)))
        mounts = ws.execution_mounts(self.workspace())
        self.assertEqual(len(mounts), 4)
        for mount, source, target, ro in zip(mounts,
                ("job-app", "job-input", "job-work", None),
                ("/workspace/app", "/workspace/input", "/workspace/work", "/run/codeguard-trace"),
                (True, True, False, False)):
            self.assertIsInstance(mount, docker.types.Mount)
            self.assertEqual(mount["Type"], "volume")
            self.assertEqual(mount.get("Source"), source)
            self.assertEqual(mount["Target"], target)
            self.assertIs(mount["ReadOnly"], ro)


class WorkspacePreparationTests(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.container = self.client.containers.create.return_value
        self.container.put_archive.return_value = True
        self.container.wait.return_value = {"StatusCode": 0}
        self.manifest = b'{"profile_version": 1}'
        self.stream = MagicMock()
        self.set_archive(tar_bytes("filesystem-runtime.json", self.manifest), len(self.manifest))
        self.attrs = {
            "Config": {"User": "0:0", "Cmd": ["/usr/local/bin/codeguard-workspace-prepare"], "Entrypoint": []},
            "HostConfig": {"ReadonlyRootfs": True, "NetworkMode": "none", "Privileged": False,
                           "CapDrop": ["ALL"], "CapAdd": ["CHOWN", "FOWNER", "DAC_OVERRIDE"],
                           "SecurityOpt": ["no-new-privileges=true"]},
            "Mounts": [{"Type": "volume", "Name": f"job-{p}", "Destination": f"/workspace/{p}", "RW": True}
                       for p in ("app", "input", "work")]}
        self.container.attrs = copy.deepcopy(self.attrs)

    def set_archive(self, payload, size=2, mode=0o444, link=""):
        self.stream.__iter__.side_effect = lambda: iter([payload])
        self.container.get_archive.return_value = (self.stream, {
            "name": "filesystem-runtime.json", "size": size, "mode": mode, "linkTarget": link})

    def prepare(self, language="CPP", **kw):
        self.assertTrue(callable(getattr(ws, "prepare_workspace", None)))
        return ws.prepare_workspace(self.client, ws.VolumeWorkspace(uuid4(), "job-app", "job-input", "job-work"),
                                    language, "int main() { return 0; }", "", **kw)

    def test_prepare_uploads_before_start_and_returns_trusted_manifest(self):
        self.assertEqual(self.prepare(), self.manifest)
        kw = self.client.containers.create.call_args.kwargs
        expected = {"image": settings.cpp_image, "command": ["/usr/local/bin/codeguard-workspace-prepare"],
                    "entrypoint": [], "user": "0:0", "read_only": True, "network_mode": "none",
                    "cap_drop": ["ALL"], "cap_add": ["CHOWN", "FOWNER", "DAC_OVERRIDE"],
                    "security_opt": ["no-new-privileges=true"]}
        for key, value in expected.items(): self.assertEqual(kw[key], value)
        self.assertEqual([(m["Source"], m["Target"], m["ReadOnly"]) for m in kw["mounts"]],
                         [(f"job-{p}", f"/workspace/{p}", False) for p in ("app", "input", "work")])
        names = [c[0] for c in self.container.method_calls]
        self.assertLess(names.index("reload"), names.index("put_archive"))
        self.assertLess(max(i for i,n in enumerate(names) if n == "put_archive"), names.index("start"))
        self.assertEqual(self.container.put_archive.call_count, 2)
        for call, path, name in zip(self.container.put_archive.call_args_list,
                                  ("/workspace/app", "/workspace/input"), ("main.cpp", "stdin")):
            self.assertEqual(call.kwargs["path"], path)
            with tarfile.open(fileobj=io.BytesIO(call.kwargs["data"])) as archive:
                self.assertEqual(archive.getnames(), [name])
        self.container.wait.assert_called_once_with(timeout=5)
        self.container.get_archive.assert_called_once_with("/usr/local/share/codeguard/filesystem-runtime.json", chunk_size=512)
        self.stream.close.assert_called_once_with()
        self.container.remove.assert_called_once_with(force=True, v=True)
        self.client.volumes.get.assert_not_called()

    def test_prepare_uses_pinned_image_id(self):
        self.container.attrs["Image"] = "sha256:pinned"
        self.prepare(image_id="sha256:pinned")
        self.assertEqual(self.client.containers.create.call_args.kwargs["image"], "sha256:pinned")

    def test_prepare_rejects_pinned_image_mismatch_before_upload_or_start(self):
        self.container.attrs["Image"] = "sha256:other"
        with self.assertRaises(SecurityVerificationError): self.prepare(image_id="sha256:pinned")
        self.container.put_archive.assert_not_called()
        self.container.start.assert_not_called()
        self.container.remove.assert_called_once_with(force=True, v=True)

    def test_inspect_mismatch_fails_before_upload_start_and_cleans_helper(self):
        cases = [("Config", "User", "10001:10001"), ("Config", "Cmd", ["sh"]),
                 ("Config", "Entrypoint", ["sh"]), ("HostConfig", "ReadonlyRootfs", False),
                 ("HostConfig", "NetworkMode", "bridge"), ("HostConfig", "Privileged", True),
                 ("HostConfig", "CapDrop", []), ("HostConfig", "CapAdd", ["SYS_ADMIN"]),
                 ("HostConfig", "SecurityOpt", [])]
        for section, key, value in cases:
            with self.subTest(key=key):
                self.container.reset_mock()
                self.container.attrs = copy.deepcopy(self.attrs)
                self.container.attrs[section][key] = value
                with self.assertRaises(SecurityVerificationError): self.prepare()
                self.container.start.assert_not_called()
                self.container.put_archive.assert_not_called()
                self.container.remove.assert_called_once_with(force=True, v=True)

    def test_mount_substitution_or_extra_mount_fails_closed(self):
        for change in ("source", "type", "rw", "extra"):
            with self.subTest(change=change):
                self.container.reset_mock()
                self.container.attrs = copy.deepcopy(self.attrs)
                mount = self.container.attrs["Mounts"][0]
                if change == "source": mount["Name"] = "other-job"
                elif change == "type": mount["Type"] = "bind"
                elif change == "rw": mount["RW"] = False
                else: self.container.attrs["Mounts"].append({"Destination": "/etc"})
                with self.assertRaises(SecurityVerificationError): self.prepare()
                self.container.start.assert_not_called()

    def test_failures_cleanup_helper_and_never_named_volumes(self):
        for failure in ("upload", "timeout", "exit", "docker"):
            with self.subTest(failure=failure):
                self.setUp()
                if failure == "upload": self.container.put_archive.return_value = False
                elif failure == "timeout": self.container.wait.side_effect = ReadTimeout("timeout")
                elif failure == "exit": self.container.wait.return_value = {"StatusCode": 1}
                else: self.container.start.side_effect = docker.errors.APIError("failed")
                with self.assertRaises(WorkspaceError): self.prepare()
                self.container.remove.assert_called_once_with(force=True, v=True)
                self.client.volumes.get.assert_not_called()

    def test_cleanup_failure_is_explicit(self):
        self.container.remove.side_effect = docker.errors.APIError("cleanup failed")
        with self.assertRaises(CleanupError): self.prepare()

    def test_invalid_language_creates_no_container(self):
        with self.assertRaises(WorkspaceError): self.prepare("PYTHON")
        self.client.containers.create.assert_not_called()

    def test_manifest_rejects_unsafe_tar_members_and_metadata(self):
        cases = [("wrong.json", tarfile.REGTYPE, 0, 0o444, b"{}"),
                 ("../filesystem-runtime.json", tarfile.REGTYPE, 0, 0o444, b"{}"),
                 ("filesystem-runtime.json", tarfile.SYMTYPE, 0, 0o444, b""),
                 ("filesystem-runtime.json", tarfile.REGTYPE, 10001, 0o444, b"{}"),
                 ("filesystem-runtime.json", tarfile.REGTYPE, 0, 0o666, b"{}"),
                 ("filesystem-runtime.json", tarfile.REGTYPE, 0, 0o444, b"x" * 65537)]
        for name, kind, uid, mode, content in cases:
            with self.subTest(name=name, kind=kind, uid=uid, mode=mode, size=len(content)):
                self.setUp()
                self.set_archive(tar_bytes(name, content, kind=kind, uid=uid, mode=mode), len(content), mode)
                with self.assertRaises(WorkspaceError): self.prepare()
                self.stream.close.assert_called_once_with()
                self.container.remove.assert_called_once_with(force=True, v=True)

    def test_manifest_rejects_docker_symlink_metadata(self):
        self.set_archive(tar_bytes("filesystem-runtime.json", b"{}"), link="/other")
        with self.assertRaises(WorkspaceError): self.prepare()

    def test_manifest_tar_byte_limit_and_duplicate_member_rejected(self):
        self.assertTrue(callable(getattr(ws, "prepare_workspace", None)))
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as archive:
            for _ in range(2):
                member = tarfile.TarInfo("filesystem-runtime.json")
                member.size, member.mode = 2, 0o444
                archive.addfile(member, io.BytesIO(b"{}"))
        for payload in (b"x" * (128 * 1024 + 1), buf.getvalue()):
            self.setUp()
            self.set_archive(payload)
            with self.assertRaises(WorkspaceError): self.prepare()


class NativeWorkspaceHelperTests(unittest.TestCase):
    def test_native_helper_uses_fixed_directory_fds_not_recursive_operations(self):
        path = Path(__file__).parents[1] / "native/filesystem/workspace_prepare.c"
        self.assertTrue(path.is_file(), "native workspace helper missing")
        source = path.read_text(encoding="utf-8")
        for required in ("O_DIRECTORY", "O_NOFOLLOW", "fstat(", "S_ISDIR(", "fchown(", "fchmod(",
                         '"/workspace/app"', '"/workspace/input"', '"/workspace/work"', "0700", "0755", "10001"):
            self.assertIn(required, source)
        for forbidden in ("system(", "exec", "nftw", "readdir"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("chown(", source.replace("fchown(", ""))

    @unittest.skipUnless(os.name == "posix" or os.environ.get("CODEGUARD_TEST_NATIVE") == "1",
                         "native Linux helper check: opt in with CODEGUARD_TEST_NATIVE=1 on Windows/WSL")
    def test_native_helper_compiles_and_prepares_only_directory_roots(self):
        # The harness remaps only the three fixed open() calls into an isolated
        # mkdtemp tree. All ownership/mode/directory syscalls remain real Linux
        # calls; nothing under the host /workspace is created or modified.
        native = Path(__file__).resolve().parents[1] / "native/filesystem/workspace_prepare.c"
        def linux_path(path):
            path = Path(path).resolve()
            if os.name == "nt":
                return "/mnt/" + path.drive[0].lower() + path.as_posix()[2:]
            return str(path)
        harness = r'''
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/capability.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <unistd.h>
static char paths[3][256];
static int mapped_open(const char *name, int flags) {
    const char *names[] = {"/workspace/app", "/workspace/input", "/workspace/work"};
    for (unsigned i = 0; i < 3; ++i)
        if (!strcmp(name, names[i])) return open(paths[i], flags);
    errno = EPERM;
    return -1;
}
#define open mapped_open
#define main prepare_main
#include "NATIVE_SOURCE"
#undef main
#undef open
int main(int argc, char **argv) {
    char base[] = "/tmp/codeguard-workspace-test-XXXXXX";
    char child[512], target[512];
    struct stat st;
    int result = 1;
    if (argc != 2 || !mkdtemp(base)) return 2;
    for (unsigned i = 0; i < 3; ++i) {
        snprintf(paths[i], sizeof(paths[i]), "%s/root%u", base, i);
        if (mkdir(paths[i], 0755)) goto done;
    }
    snprintf(child, sizeof(child), "%s/source", paths[0]);
    int fd = open(child, O_CREAT | O_WRONLY | O_EXCL, 0640);
    if (fd < 0 || close(fd)) goto done;
    snprintf(target, sizeof(target), "%s/outside", base);
    if (!strcmp(argv[1], "symlink")) {
        if (mkdir(target, 0700) || rmdir(paths[2]) || symlink(target, paths[2])) goto done;
    } else if (!strcmp(argv[1], "file")) {
        if (rmdir(paths[2])) goto done;
        fd = open(paths[2], O_CREAT | O_WRONLY | O_EXCL, 0600);
        if (fd < 0 || close(fd)) goto done;
    } else if (!strcmp(argv[1], "missing")) {
        if (rmdir(paths[2])) goto done;
    }
    struct __user_cap_header_struct cap_header = {_LINUX_CAPABILITY_VERSION_3, 0};
    struct __user_cap_data_struct caps[2] = {{0}, {0}};
    caps[0].effective = caps[0].permitted = (1U << CAP_CHOWN) | (1U << CAP_FOWNER) | (1U << CAP_DAC_OVERRIDE);
    if (syscall(SYS_capset, &cap_header, caps)) goto done;
    int outcome = prepare_main(!strcmp(argv[1], "args") ? 2 : 1, argv);
    if (!strcmp(argv[1], "success")) {
        if (outcome) goto done;
        /* Retry after work is uid 10001 and 0700 exercises DAC_OVERRIDE. */
        if (prepare_main(1, argv)) goto done;
        for (unsigned i = 0; i < 3; ++i) {
            if (stat(paths[i], &st) || st.st_uid != 10001 || st.st_gid != 10001 ||
                (st.st_mode & 07777) != (i == 2 ? 0700 : 0755)) goto done;
        }
    } else {
        if (!outcome) goto done;
        /* All fds are checked before changing any roots, including app/input. */
        if (stat(paths[0], &st) || st.st_uid != 0 || (st.st_mode & 07777) != 0755) goto done;
        if (!strcmp(argv[1], "symlink") &&
            (stat(target, &st) || st.st_uid != 0 || (st.st_mode & 07777) != 0700)) goto done;
    }
    if (stat(child, &st) || st.st_uid != 0 || (st.st_mode & 07777) != 0640) goto done;
    result = 0;
done:
    /* Explicit cleanup of only fixtures allocated by this harness. */
    unlink(child);
    for (unsigned i = 0; i < 3; ++i) { unlink(paths[i]); rmdir(paths[i]); }
    rmdir(target);
    rmdir(base);
    return result;
}
'''.replace("NATIVE_SOURCE", linux_path(native))
        if os.name == "nt":
            prefix = ["wsl", "-d", "Ubuntu-24.04", "-u", "root", "--exec"]
        else:
            if os.geteuid() != 0 or not shutil.which("gcc"):
                self.skipTest("native runtime check requires Linux root and gcc")
            prefix = []
        with tempfile.TemporaryDirectory(prefix="codeguard-helper-check-") as temp:
            source, binary = Path(temp) / "harness.c", Path(temp) / "harness"
            source.write_text(harness, encoding="utf-8")
            compiled = subprocess.run(prefix + ["gcc", "-std=c17", "-Wall", "-Wextra", "-Werror",
                linux_path(source), "-o", linux_path(binary)], capture_output=True, text=True, timeout=30)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            for case in ("success", "symlink", "file", "missing", "args"):
                with self.subTest(case=case):
                    checked = subprocess.run(prefix + [linux_path(binary), case],
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)


if __name__ == "__main__": unittest.main()
