import io
import tarfile
import unittest
from unittest.mock import MagicMock
from uuid import uuid4

import docker
from requests.exceptions import ConnectionError, ReadTimeout
from urllib3.exceptions import ReadTimeoutError

from runner.config import settings
from runner.exceptions import ContainerExecutionError, SecurityVerificationError, WorkspaceError
from runner.pipeline.compiler import (
    _artifact_exists,
    compile_source,
    create_compile_container,
)
from runner.pipeline.workspace import VolumeWorkspace
from runner.tests.test_workspace import tar_bytes


class CompilerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = MagicMock()
        self.container = MagicMock()
        self.client.containers.create.return_value = self.container
        self.container.put_archive.return_value = True
        self.container.wait.return_value = {"StatusCode": 0}
        self.security_marker = (
            b"CODEGUARD_SECURITY_CHECK status=verified uid=10001 gid=10001 "
            b"cap_inh=0000000000000000 cap_prm=0000000000000000 "
            b"cap_eff=0000000000000000 cap_bnd=0000000000000000 "
            b"cap_amb=0000000000000000 no_new_privileges=1\n"
        )
        self.container.logs.side_effect = [b"", self.security_marker]
        archive_stream = MagicMock()
        self.artifact_stream = archive_stream
        self.artifact_bytes = tar_bytes("main", b"\x7fELFpayload", uid=10001, gid=10001, mode=0o755)
        archive_stream.__iter__.side_effect = lambda: iter([self.artifact_bytes])
        self.container.get_archive.return_value = (
            archive_stream,
            {"name": "main", "size": 11, "mode": 0o755, "linkTarget": ""},
        )
        self.container.attrs = {
            "Config": {"User": "10001:10001"},
            "HostConfig": {
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges=true"],
            },
            "Mounts": [{"Destination": "/workspace", "RW": True,
                        "Type": "volume", "Name": "codeguard-job-test-app"}],
        }
        self.workspace = VolumeWorkspace(
            job_id=uuid4(),
            volume_name="codeguard-job-test-app",
        )

    def test_create_compile_container_returns_registered_container(self) -> None:
        result = create_compile_container(
            client=self.client,
            workspace=self.workspace,
            language="CPP",
        )

        self.assertIs(result, self.container)
        self.client.containers.create.assert_called_once_with(
            image=settings.cpp_image,
            command=[
                "/usr/local/bin/codeguard-security-preflight",
                "g++",
                "-std=c++17",
                "-Wall",
                "-Wextra",
                "-O0",
                "/workspace/main.cpp",
                "-o",
                "/workspace/main",
            ],
            mounts=[docker.types.Mount("/workspace", self.workspace.volume_name, type="volume", read_only=False, no_copy=True)],
            detach=True,
            network_mode="none",
            user="10001:10001",
            cap_drop=["ALL"],
            security_opt=["no-new-privileges=true"],
            environment={
                "CODEGUARD_EXPECTED_UID": "10001",
                "CODEGUARD_EXPECTED_GID": "10001",
            },
            labels={
                "codeguard.managed": "true",
                "codeguard.job_id": str(self.workspace.job_id),
                "codeguard.stage": "compile",
            },
        )

    def test_create_compile_container_selects_c17(self) -> None:
        create_compile_container(
            client=self.client,
            workspace=self.workspace,
            language="C",
        )

        command = self.client.containers.create.call_args.kwargs["command"]
        self.assertEqual(
            command[0:3],
            ["/usr/local/bin/codeguard-security-preflight", "gcc", "-std=c17"],
        )
        self.assertEqual(command[3:6], ["-Wall", "-Wextra", "-O0"])
        self.assertEqual(command[6], "/workspace/main.c")

    def test_compile_source_uploads_and_runs_without_removing_container(self) -> None:
        result = compile_source(
            container=self.container,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
            stdin="21\n",
        )

        self.assertTrue(result.success)
        archive_bytes = self.container.put_archive.call_args.kwargs["data"]
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            self.assertEqual(archive.getnames(), ["main.cpp"])
        method_names = [call[0] for call in self.container.method_calls]
        self.assertLess(method_names.index("put_archive"), method_names.index("start"))
        self.container.remove.assert_not_called()

    def test_compile_source_times_out_kills_and_reaps_container(self) -> None:
        self.container.wait.side_effect = [
            ReadTimeout("compile timeout"),
            {"StatusCode": 137},
        ]

        result = compile_source(
            container=self.container,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
        )

        self.assertTrue(result.timed_out)
        self.assertFalse(result.success)
        self.assertIsNone(result.exit_code)
        self.container.kill.assert_called_once_with()
        self.assertEqual(self.container.wait.call_count, 2)
        self.container.wait.assert_any_call(timeout=10)
        self.container.wait.assert_any_call(timeout=2)
        self.container.remove.assert_not_called()

    def test_compile_source_treats_wrapped_read_timeout_as_timeout(self) -> None:
        read_timeout = ReadTimeoutError(None, None, "Read timed out.")
        self.container.wait.side_effect = [
            ConnectionError(read_timeout),
            {"StatusCode": 137},
        ]

        with self.assertLogs("runner", level="WARNING") as logs:
            result = compile_source(
                container=self.container,
                workspace=self.workspace,
                language="CPP",
                code="int main() { return 0; }",
            )

        self.assertTrue(result.timed_out)
        self.container.kill.assert_called_once_with()
        self.assertTrue(
            any("event=compile_timeout" in message for message in logs.output)
        )

    def test_compile_source_does_not_treat_connection_error_as_timeout(self) -> None:
        self.container.wait.side_effect = ConnectionError(
            "Docker socket disconnected",
        )

        with self.assertRaises(ConnectionError):
            compile_source(
                container=self.container,
                workspace=self.workspace,
                language="CPP",
                code="int main() { return 0; }",
            )

        self.container.kill.assert_not_called()

    def test_compile_source_returns_compile_error(self) -> None:
        self.container.wait.return_value = {"StatusCode": 1}
        self.container.logs.side_effect = [
            b"",
            self.security_marker + b"syntax error",
        ]

        result = compile_source(
            container=self.container,
            workspace=self.workspace,
            language="CPP",
            code="int main( {",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.exit_code, 1)
        self.assertEqual(result.stderr, "syntax error")
        self.container.remove.assert_not_called()

    def test_compile_source_marks_missing_artifact_as_not_ready(self) -> None:
        self.container.get_archive.side_effect = docker.errors.NotFound("missing")

        result = compile_source(
            container=self.container,
            workspace=self.workspace,
            language="CPP",
            code="int main() { return 0; }",
        )

        self.assertFalse(result.success)
        self.assertFalse(result.artifact_ready)
        self.container.remove.assert_not_called()

    def test_create_compile_container_wraps_missing_image(self) -> None:
        self.client.containers.create.side_effect = docker.errors.ImageNotFound(
            "missing",
        )

        with self.assertRaises(ContainerExecutionError):
            create_compile_container(
                client=self.client,
                workspace=self.workspace,
                language="CPP",
            )

    def test_compile_source_rejects_failed_archive_upload(self) -> None:
        self.container.put_archive.return_value = False

        with self.assertRaises(WorkspaceError):
            compile_source(
                container=self.container,
                workspace=self.workspace,
                language="CPP",
                code="int main() { return 0; }",
            )

        self.container.remove.assert_not_called()

    def test_create_compile_container_accepts_pinned_image_id(self):
        self.container.attrs["Image"] = "sha256:pinned"
        create_compile_container(self.client, self.workspace, "CPP", image_id="sha256:pinned")
        self.assertEqual(self.client.containers.create.call_args.kwargs["image"], "sha256:pinned")

    def test_pinned_compile_image_mismatch_fails_before_start_and_removes_container(self):
        self.container.attrs["Image"] = "sha256:other"
        with self.assertRaises(SecurityVerificationError):
            create_compile_container(self.client, self.workspace, "CPP", image_id="sha256:pinned")
        self.container.start.assert_not_called()
        self.container.remove.assert_called_once_with(force=True)

    def test_compile_mount_identity_is_app_only_and_fails_closed(self):
        for change in ("source", "type", "extra", "duplicate"):
            with self.subTest(change=change):
                self.setUp()
                mount = self.container.attrs["Mounts"][0]
                if change == "source": mount["Name"] = "other-job"
                elif change == "type": mount["Type"] = "bind"
                elif change == "duplicate": self.container.attrs["Mounts"].append(dict(mount))
                else: self.container.attrs["Mounts"].append({"Destination": "/workspace/work", "RW": True,
                                                            "Type": "volume", "Name": "other-work"})
                with self.assertRaises(SecurityVerificationError):
                    create_compile_container(self.client, self.workspace, "CPP")
                self.container.start.assert_not_called()
                self.container.remove.assert_called_once_with(force=True)

    def test_artifact_rejects_nonregular_nonelf_or_untrusted_metadata(self):
        cases = [("main", tarfile.SYMTYPE, 10001, 10001, 0o755, b""),
                 ("main", tarfile.LNKTYPE, 10001, 10001, 0o755, b""),
                 ("main", tarfile.DIRTYPE, 10001, 10001, 0o755, b""),
                 ("other", tarfile.REGTYPE, 10001, 10001, 0o755, b"\x7fELFpayload"),
                 ("main", tarfile.REGTYPE, 10001, 10001, 0o755, b"text-script"),
                 ("main", tarfile.REGTYPE, 0, 10001, 0o755, b"\x7fELFpayload"),
                 ("main", tarfile.REGTYPE, 10001, 0, 0o755, b"\x7fELFpayload"),
                 ("main", tarfile.REGTYPE, 10001, 10001, 0o777, b"\x7fELFpayload"),
                 ("main", tarfile.REGTYPE, 10001, 10001, 0o4755, b"\x7fELFpayload"),
                 ("main", tarfile.REGTYPE, 10001, 10001, 0o644, b"\x7fELFpayload")]
        for name, kind, uid, gid, mode, data in cases:
            with self.subTest(name=name, kind=kind, uid=uid, gid=gid, mode=mode, data=data):
                self.artifact_bytes = tar_bytes(name, data, kind=kind, uid=uid, gid=gid, mode=mode)
                self.assertFalse(_artifact_exists(self.container))
        self.assertEqual(self.artifact_stream.close.call_count, len(cases))

    def test_artifact_rejects_docker_stat_symlink_and_special_modes(self):
        for metadata in ({"name": "main", "size": 11, "mode": 0o755, "linkTarget": "/elsewhere"},
                         {"name": "main", "size": 11, "mode": (1 << 27) | 0o755},
                         {"name": "main", "size": 11, "mode": (1 << 31) | 0o755},
                         {"name": "other", "size": 11, "mode": 0o755},
                         {"name": "main", "size": 3, "mode": 0o755}):
            with self.subTest(metadata=metadata):
                self.container.get_archive.return_value = self.artifact_stream, metadata
                self.assertFalse(_artifact_exists(self.container))

    def test_artifact_stream_reads_only_header_and_elf_prefix_and_closes(self):
        # A valid 100 MiB artifact must not be materialized or drained. Docker's
        # close() cancels the HTTP stream once the checked prefix is consumed.
        header = tarfile.TarInfo("main")
        header.size, header.uid, header.gid, header.mode = 100 * 1024 * 1024, 10001, 10001, 0o755
        consumed = []
        def chunks():
            consumed.append("header")
            yield header.tobuf()
            consumed.append("prefix")
            yield b"\x7fELF" + b"x" * 508
            raise AssertionError("must not download artifact body")
        self.artifact_stream.__iter__.side_effect = chunks
        self.container.get_archive.return_value = self.artifact_stream, {
            "name": "main", "size": header.size, "mode": 0o755, "linkTarget": ""}
        self.assertTrue(_artifact_exists(self.container))
        self.assertEqual(consumed, ["header", "prefix"])
        self.artifact_stream.close.assert_called_once_with()
        self.container.get_archive.assert_called_once_with("/workspace/main", chunk_size=512)

    def test_artifact_rejects_malformed_or_oversized_header(self):
        for payload in (b"not-a-tar", b"x" * (16 * 1024 + 1)):
            with self.subTest(size=len(payload)):
                self.artifact_bytes = payload
                self.assertFalse(_artifact_exists(self.container))


if __name__ == "__main__":
    unittest.main()
