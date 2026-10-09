"""Real-image setup shared by opt-in Docker integration tests.

Never synthesize a runtime profile: only trusted workspace preparation may
provide the C/C++ probe union used to build the exact-file policy.
"""

import re

from runner.config import settings
from runner.pipeline.compiler import compile_source, create_compile_container
from runner.pipeline.workspace import prepare_workspace
from runner.policies.filesystem import build_filesystem_policy, parse_runtime_manifest


def pin_execution_image(client) -> str:
    image = client.images.get(settings.cpp_image)
    if image.attrs.get("Os") != "linux" or image.attrs.get("Architecture") != "amd64":
        raise AssertionError("Docker integration requires the trusted Linux amd64 image")
    if not isinstance(image.id, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", image.id):
        raise AssertionError("Docker integration requires an immutable sha256 image ID")
    return image.id


def prepare_filesystem_policy(client, workspace, image_id, language, code, stdin=""):
    manifest = prepare_workspace(
        client, workspace, language, code, stdin, image_id=image_id,
    )
    profile = parse_runtime_manifest(manifest, image_id)
    return build_filesystem_policy(image_id, language, runtime_profile=profile, device_paths=settings.filesystem_device_paths)


def prepare_and_compile(testcase, workspace, code, stdin="", language="C"):
    policy = prepare_filesystem_policy(
        testcase.client, workspace, testcase.image_id, language, code, stdin,
    )
    compiler = create_compile_container(
        testcase.client, workspace, language, image_id=testcase.image_id,
    )
    testcase.addCleanup(compiler.remove, force=True)
    compiled = compile_source(compiler, workspace, language, code, stdin=stdin)
    testcase.assertTrue(compiled.success, compiled.stderr)
    testcase.assertTrue(compiled.artifact_ready)
    compiler.reload()
    testcase.assertEqual(compiler.attrs["Image"], testcase.image_id)
    return policy, compiler
