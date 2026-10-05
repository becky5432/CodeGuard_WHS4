#!/usr/bin/env python3
"""Measure the image's fixed C/C++ runtime without inspecting user programs.

Run in the final image at build time, after compiler/runtime installation.
Only these internally generated probes are passed to readelf and ldd.
No network/TLS/DNS/config dependencies and no candidate-file enumeration.
"""

import argparse
import json
import os
import platform
import re
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath


DEFAULT_DESTINATION = "/usr/local/share/codeguard/filesystem-runtime.json"
LOADER = "/usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
ROOTS = {"/usr/lib/x86_64-linux-gnu", "/usr/local/lib64"}
ALIAS_ROOTS = ROOTS | {"/lib/x86_64-linux-gnu"}
LOADER_ALIASES = {LOADER, "/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2",
                  "/lib64/ld-linux-x86-64.so.2"}
PATTERNS = {
    "libc.so.6": re.compile(r"(?:libc\.so\.6|libc-[0-9]+(?:\.[0-9]+)*\.so)"),
    "libstdc++.so.6": re.compile(r"libstdc\+\+\.so\.6(?:\.[0-9]+)*"),
    "libgcc_s.so.1": re.compile(r"libgcc_s\.so\.1(?:\.[0-9]+)*"),
    "libm.so.6": re.compile(r"(?:libm\.so\.6|libm-[0-9]+(?:\.[0-9]+)*\.so)"),
}
C_SOURCE = r"""#include <stdio.h>
#include <math.h>
int main(void) {
    volatile double input = 0.5;
    printf("%f\n", sin(input));
    return 0;
}
"""
CPP_SOURCE = r"""#include <iostream>
#include <cmath>
#include <stdexcept>
int main() {
    volatile double input = 0.5;
    try {
        std::cout << std::sin(input) << '\n';
        throw std::runtime_error("fixed trusted probe");
    } catch (const std::exception&) {
        return 0;
    }
}
"""


def verify_elf(path):
    """Resolve aliases and inspect the same actual regular ELF64 x86_64 file."""
    try:
        real = Path(path).resolve(strict=True)
        if not stat.S_ISREG(real.lstat().st_mode):
            raise ValueError("runtime dependency is not a regular file")
        with real.open("rb") as file:
            if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
                raise ValueError("runtime dependency is not a regular file")
            header = file.read(64)
    except (OSError, RuntimeError) as exc:
        raise ValueError("runtime dependency is missing or not regular") from exc
    if (len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01"
            or int.from_bytes(header[16:18], "little") != 3
            or int.from_bytes(header[18:20], "little") != 62):
        raise ValueError("runtime dependency is not a verified ELF64 x86_64 shared object")
    return str(real)


def _safe_alias(path):
    if (not path.startswith("/") or len(path.encode()) > 4095
            or any(char in path for char in "\0\t\r\n")
            or any(part in {"", ".", ".."} for part in path[1:].split("/"))):
        raise ValueError("invalid dependency alias path")


def parse_ldd_output(output):
    """Allow the four known SONAMEs, fixed loader and non-file vDSO only."""
    if len(output.encode()) > 65536:
        raise ValueError("oversized ldd output")
    dependencies, loaders = {}, []
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if re.fullmatch(r"linux-vdso\.so\.1 \(0x[0-9a-fA-F]+\)", line):
            continue
        linked = re.fullmatch(r"([^\s]+) => (/[^\s]+) \(0x[0-9a-fA-F]+\)", line)
        if linked:
            name, path = linked.groups()
            _safe_alias(path)
            if name not in PATTERNS or name in dependencies or str(PurePosixPath(path).parent) not in ALIAS_ROOTS:
                raise ValueError("arbitrary or duplicate runtime dependency")
            dependencies[name] = path
            continue
        direct = re.fullmatch(r"(/[^\s]+) \(0x[0-9a-fA-F]+\)", line)
        if direct and direct[1] in LOADER_ALIASES:
            if loaders:
                raise ValueError("duplicate runtime loader")
            loaders.append(direct[1])
            continue
        raise ValueError("missing, arbitrary or malformed ldd dependency")
    if not dependencies or len(loaders) != 1:
        raise ValueError("ldd did not establish a dynamic runtime")
    return dependencies, tuple(loaders)


def existing_cache():
    """The only optional configuration file is an existing regular ld cache."""
    path = Path("/etc/ld.so.cache")
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(mode):
        raise ValueError("loader cache is not an existing regular file")
    return str(path)


def _run(command):
    return subprocess.run(command, check=True, capture_output=True, text=True, timeout=30).stdout


def build_manifest():
    """Compile trusted probes and measure their actual dependency union."""
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise ValueError("cpp-amd64-v1 requires a Linux x86_64 image build")
    observed = {}
    with tempfile.TemporaryDirectory(prefix="codeguard-runtime-probe-") as directory:
        root = Path(directory)
        for name, compiler, standard, source, suffix, flags in [
            ("c", "gcc", "-std=c17", C_SOURCE, ".c", ["-lm"]),
            ("cpp", "g++", "-std=c++17", CPP_SOURCE, ".cpp", []),
        ]:
            source_path, program = root / ("probe" + suffix), root / ("probe-" + name)
            source_path.write_text(source, encoding="utf-8")
            _run([compiler, "-O0", standard, str(source_path), "-o", str(program), *flags])
            headers = _run(["readelf", "-l", str(program)])
            interpreters = re.findall(r"\[Requesting program interpreter: ([^\]]+)\]", headers)
            if len(interpreters) != 1 or interpreters[0] not in LOADER_ALIASES:
                raise ValueError("trusted probe has an unverified ELF interpreter")
            if verify_elf(interpreters[0]) != LOADER:
                raise ValueError("trusted probe loader does not resolve to fixed actual path")
            dependencies, loaders = parse_ldd_output(_run(["ldd", str(program)]))
            if any(verify_elf(loader) != LOADER for loader in loaders):
                raise ValueError("ldd runtime loader differs from verified ELF interpreter")
            for soname, alias in dependencies.items():
                actual = verify_elf(alias)
                if str(PurePosixPath(actual).parent) not in ROOTS or not PATTERNS[soname].fullmatch(PurePosixPath(actual).name):
                    raise ValueError("resolved dependency is outside exact runtime allowlist")
                if soname in observed and observed[soname] != actual:
                    raise ValueError("trusted probes resolved different files for one runtime library")
                observed[soname] = actual
    if set(observed) != set(PATTERNS):
        raise ValueError("trusted probes did not verify all four runtime candidates")
    return dict(version=1, profile="cpp-amd64-v1", architecture="amd64", loader=LOADER,
                libraries=sorted(observed.values()), cache=existing_cache())


def write_manifest(manifest_value, destination=DEFAULT_DESTINATION):
    """Atomically install the measured, deterministic readonly build artifact."""
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(manifest_value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > 65536:
        raise ValueError("runtime manifest exceeds byte limit")
    fd, temporary = tempfile.mkstemp(prefix=".filesystem-runtime-", dir=output.parent)
    try:
        with os.fdopen(fd, "wb") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, 0o444)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", nargs="?", default=DEFAULT_DESTINATION)
    arguments = parser.parse_args()
    write_manifest(build_manifest(), arguments.destination)


if __name__ == "__main__":
    main()
