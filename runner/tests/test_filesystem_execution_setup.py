"""Fail-closed filesystem setup at the Docker boundary (no daemon needed)."""

import io
import tarfile
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from runner.exceptions import RunnerError
from runner.pipeline.execution import create_execution_container
from runner.pipeline.workspace import VolumeWorkspace
from runner.policies.filesystem import build_filesystem_policy, parse_runtime_manifest
from runner.security import verify_container_security_config


def runtime_manifest_fixture():
    return (
        b'{"version":1,"profile":"cpp-amd64-v1","architecture":"amd64",'
        b'"loader":"/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",'
        b'"libraries":["/usr/lib/x86_64-linux-gnu/libc.so.6",'
        b'"/usr/local/lib64/libstdc++.so.6.0.34",'
        b'"/usr/local/lib64/libgcc_s.so.1",'
        b'"/usr/lib/x86_64-linux-gnu/libm.so.6"],"cache":null}'
    )


def policy_fixture(language="C"):
    profile = parse_runtime_manifest(runtime_manifest_fixture(), "sha256:" + "a" * 64)
    return build_filesystem_policy(profile.image_id, language, True, runtime_profile=profile)


def container_fixture(workspace, policy):
    container = MagicMock()
    container.put_archive.return_value = True
    container.attrs = {
        "Image": policy.image_id,
        "Config": {"User": "0:0"},
        "HostConfig": {
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "CapAdd": ["SYS_PTRACE", "SETUID", "SETGID"],
            "SecurityOpt": ["no-new-privileges=true"],
        },
        "Mounts": [
            {"Type": "volume", "Name": workspace.app_volume, "Destination": "/workspace/app", "RW": False},
            {"Type": "volume", "Name": workspace.input_volume, "Destination": "/workspace/input", "RW": False},
            {"Type": "volume", "Name": workspace.work_volume, "Destination": "/workspace/work", "RW": True},
            {"Type": "volume", "Name": "anonymous-trace", "Destination": "/run/codeguard-trace", "RW": True},
        ],
    }
    return container


def test_execution_uploads_protected_policy_and_mounts_three_volumes():
    workspace = VolumeWorkspace(uuid4(), "app", "input", "work")
    policy = policy_fixture()
    container = container_fixture(workspace, policy)
    client = MagicMock()
    client.containers.create.return_value = container
    with patch("runner.pipeline.execution._resolve_cpuset", return_value="0"):
        result = create_execution_container(
            client, workspace, "", workspace.job_id, uuid4(), 128, 1.0, 32,
            filesystem_policy=policy,
        )
    assert result is container
    options = client.containers.create.call_args.kwargs
    assert options["image"] == policy.image_id
    assert "volumes" not in options
    assert [(m["Target"], m["ReadOnly"]) for m in options["mounts"]] == [
        ("/workspace/app", True), ("/workspace/input", True),
        ("/workspace/work", False), ("/run/codeguard-trace", False),
    ]
    command = options["command"][2]
    assert "--filesystem-policy-fd 4" in command
    assert "--filesystem-status-fd 5" in command
    assert "--stdin /workspace/input/stdin" in command  # also for empty input
    assert "--workdir /workspace/work -- /workspace/app/main" in command
    destination, payload = container.put_archive.call_args.args
    assert destination == "/run/codeguard-trace"
    with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
        member = archive.getmember("filesystem.policy")
        assert member.uid == member.gid == 0
        assert member.mode == 0o400
        assert archive.extractfile(member).read().startswith(b"CGFS\t1\t")


@pytest.mark.parametrize("mutation", ["wrong_source", "extra_rw", "hidden_tmpfs", "root_rw", "wrong_image", "duplicate"])
def test_mount_or_image_mismatch_never_starts_container(mutation):
    workspace = VolumeWorkspace(uuid4(), "app", "input", "work")
    policy = policy_fixture()
    container = container_fixture(workspace, policy)
    if mutation == "wrong_source":
        container.attrs["Mounts"][0]["Name"] = "other-job"
    elif mutation == "extra_rw":
        container.attrs["Mounts"].append({"Destination": "/tmp", "RW": True})
    elif mutation == "hidden_tmpfs":
        # Explicit tmpfs configuration may be exposed only in HostConfig.
        container.attrs["HostConfig"]["Tmpfs"] = {"/tmp": "rw,size=1m"}
    elif mutation == "root_rw":
        container.attrs["HostConfig"]["ReadonlyRootfs"] = False
    elif mutation == "wrong_image":
        container.attrs["Image"] = "sha256:" + "b" * 64
    else:
        container.attrs["Mounts"].append(dict(container.attrs["Mounts"][0]))
    client = MagicMock()
    client.containers.create.return_value = container
    with patch("runner.pipeline.execution._resolve_cpuset", return_value="0"), pytest.raises(RunnerError):
        create_execution_container(
            client, workspace, "", workspace.job_id, uuid4(), 128, 1.0, 32,
            filesystem_policy=policy,
        )
    container.start.assert_not_called()
    container.remove.assert_called_once_with(force=True, v=True)


def test_policy_upload_failure_cleans_created_container():
    workspace = VolumeWorkspace(uuid4(), "app", "input", "work")
    policy = policy_fixture()
    container = container_fixture(workspace, policy)
    container.put_archive.return_value = False
    client = MagicMock()
    client.containers.create.return_value = container
    with patch("runner.pipeline.execution._resolve_cpuset", return_value="0"), pytest.raises(RunnerError):
        create_execution_container(
            client, workspace, "", workspace.job_id, uuid4(), 128, 1.0, 32,
            filesystem_policy=policy,
        )
    container.start.assert_not_called()
    container.remove.assert_called_once_with(force=True, v=True)
