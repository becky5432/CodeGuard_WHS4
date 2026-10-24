"""VM filesystem acceptance checks and screenshot-ready evidence report.

Run from the repository root: python -m runner.tests.filesystem_acceptance
Docker imports are deferred so report contracts and --help also run on Windows.
"""

import argparse
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import errno
import html
import io
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import tarfile
from uuid import uuid4


GROUPS = ("환경", "/workspace", "외부 허용목록", "그 밖의 경로", "strace", "eBPF")
GROUP_OPTIONS = {"workspace": "/workspace", "allowlist": "외부 허용목록", "denied": "그 밖의 경로"}
OPERATIONS = {
    "read": "읽기", "list": "목록 조회", "create": "파일 생성", "mkdir": "디렉터리 생성",
    "write": "덮어쓰기", "append": "내용 추가", "truncate": "크기 줄이기",
    "delete": "파일 삭제", "rmdir": "디렉터리 삭제", "rename": "파일 이름 변경·이동",
    "move_dir": "디렉터리 이름 변경·이동", "exec": "프로그램 실행",
    "exec_loader": "동적 로더 실행", "write_existing": "기존 파일 쓰기",
    "device_write": "장치 쓰기", "relative_create": "상대 경로 쓰기",
    "symlink_write": "심볼릭 링크 쓰기", "dirfd_write": "디렉터리 FD 쓰기",
    "delete_existing": "보호 파일 삭제", "fake": "가짜 오류 문자열",
}
CPP_SOURCE = r'''
#include <iostream>
#include <numeric>
#include <vector>
int main() {
    std::vector<int> values{10, 12, 20};
    int sum = std::accumulate(values.begin(), values.end(), 0);
    std::cout << "CGFS_RESULT {\"rc\":0,\"errno\":0,\"verified\":" << (sum == 42) << "}\n";
}
'''


@dataclass(frozen=True)
class Case:
    id: str
    group: str
    path: str
    operation: str
    allowed: bool
    aux: str = ""
    check_strace: bool = False


@dataclass
class Row:
    id: str
    group: str
    path: str
    operation: str
    expected: str
    actual: str
    status: str
    evidence: str = ""


def evaluate_probe(case, stdout, *, exit_code=0, system_error=None):
    row = Row(case.id, case.group, case.path, OPERATIONS.get(case.operation, case.operation),
              "허용" if case.allowed else "차단", "", "FAIL")
    if system_error or exit_code != 0:
        row.actual, row.status = system_error or f"프로그램 종료 코드 {exit_code}", "ERROR"
        return row
    markers = [line[len("CGFS_RESULT "):] for line in stdout.splitlines()
               if line.startswith("CGFS_RESULT ")]
    try:
        if len(markers) != 1:
            raise ValueError("진단 결과가 없거나 중복됨")
        value = json.loads(markers[0])
        if (not isinstance(value, dict) or set(value) != {"rc", "errno", "verified"}
                or any(type(item) is not int for item in value.values())
                or value["rc"] not in (-1, 0) or value["verified"] not in (0, 1)
                or value["errno"] < 0):
            raise ValueError("진단 결과 형식 오류")
    except (ValueError, TypeError) as exc:
        row.actual = str(exc)
        return row
    rc, error, verified = value["rc"], value["errno"], value["verified"]
    row.actual = "작업 성공" if rc == 0 else f"{errno.errorcode.get(error, 'UNKNOWN')} ({error})"
    if not verified:
        row.actual += " / 대상 존재·준비 단계 또는 작업 후 상태 확인 실패"
    passed = (verified == 1 and ((case.allowed and rc == 0 and error == 0)
              or (not case.allowed and rc == -1 and error in (errno.EACCES, errno.EPERM, errno.EROFS))))
    row.status = "PASS" if passed else "FAIL"
    return row


def build_cases(policy):
    cases = [
        Case("W01", "/workspace", "/workspace", "list", True),
        Case("W02", "/workspace", "/workspace/stdin", "read", True),
        Case("W03", "/workspace", "/workspace/cg-create", "create", True),
        Case("W04", "/workspace", "/workspace/cg-directory", "mkdir", True),
        Case("W05", "/workspace", "/workspace/cg-write", "write", True),
        Case("W06", "/workspace", "/workspace/cg-append", "append", True),
        Case("W07", "/workspace", "/workspace/cg-truncate", "truncate", True),
        Case("W08", "/workspace", "/workspace/cg-delete", "delete", True),
        Case("W09", "/workspace", "/workspace/cg-rmdir", "rmdir", True),
        Case("W10", "/workspace", "/workspace/cg-old → /workspace/cg-new", "rename", True,
             "/workspace/cg-new"),
        Case("W11", "/workspace", "/workspace/cg-dir-old → /workspace/cg-dir-new", "move_dir", True,
             "/workspace/cg-dir-new"),
        Case("W12", "/workspace", "/workspace/main", "exec", True),
    ]
    for index, rule in enumerate(policy.rules, 1):
        profile = rule.profile.value
        if profile == "WORK":
            continue
        prefix = f"A{index:02}"
        cases.append(Case(prefix + "R", "외부 허용목록", rule.path, "read", True))
        if profile == "FILE_EXEC":
            cases.append(Case(prefix + "X", "외부 허용목록", rule.path, "exec_loader", True))
        if profile.startswith("DEVICE"):
            cases.append(Case(prefix + "W", "외부 허용목록", rule.path, "device_write", profile == "DEVICE_RW"))
        else:
            cases.append(Case(prefix + "W", "외부 허용목록", rule.path, "write_existing", False, check_strace=True))
    cases.extend([
        Case("N01", "그 밖의 경로", "/etc/hostname", "read", False),
        Case("N02", "그 밖의 경로", "/proc/self/status", "read", False),
        Case("N03", "그 밖의 경로", "/etc/cg-acceptance-created", "create", False, check_strace=True),
        Case("N04", "그 밖의 경로", "/tmp/cg-acceptance-created", "create", False, check_strace=True),
        Case("N05", "그 밖의 경로", "/dev/shm/cg-acceptance-created", "create", False, check_strace=True),
        Case("N06", "그 밖의 경로", "/workspace/cg-link → /dev/shm/cg-link-target", "symlink_write", False,
             "/dev/shm/cg-link-target", True),
        Case("N07", "그 밖의 경로", "/workspace/../etc/cg-relative", "relative_create", False, check_strace=True),
        Case("N08", "그 밖의 경로", "/etc (dirfd) / cg-dirfd", "dirfd_write", False, "cg-dirfd", True),
        Case("N09", "그 밖의 경로", "/bin/sh", "exec", False),
        Case("N10", "그 밖의 경로", "/run/codeguard-trace", "list", False),
        Case("N11", "그 밖의 경로", "/run/codeguard-trace/cg-created", "create", False, check_strace=True),
        Case("T00", "strace", "stderr (파일 접근 없음)", "fake", True, check_strace=True),
    ])
    cache = next((rule.path for rule in policy.rules if rule.path == "/etc/ld.so.cache"), None)
    if cache:
        cases.append(Case("N12", "그 밖의 경로", cache, "delete_existing", False, check_strace=True))
    # Run last: this deliberately replaces the C probe binary in the test volume.
    cases.append(Case("W13", "/workspace", "/workspace/main (C++17)", "exec", True))
    return cases


class ObservedTaskTracker:
    """Delegate production tracking, retaining collector availability before cleanup."""

    def __init__(self, client):
        self.client = client
        self.active_by_run = {}

    def __getattr__(self, name):
        return getattr(self.client, name)

    def filesystem_snapshot(self, run_id):
        snapshot = self.client.filesystem_snapshot(run_id)
        self.active_by_run[str(run_id)] = snapshot.active
        return snapshot


def ebpf_rows(samples, *, supported, enabled, required):
    specs = (("E01", "Landlock 거부 감지", "거부 이벤트 수집"),
             ("E02", "정상 접근 오탐 확인", "정상 접근의 위반 이벤트 없음"),
             ("E03", "실행별 이벤트 구분", "수집 이벤트의 run_id 일치"))
    if not supported or not enabled:
        reason = "파일시스템 감지 필드 미지원" if not supported else "eBPF 서비스 미사용·소켓 미준비"
        return [Row(key, "eBPF", "Landlock 거부 이벤트", operation, expected,
                    reason, "FAIL" if required else "SKIP") for key, operation, expected in specs]
    if not samples or any(item.get("collector_active") is not True for item in samples):
        unavailable = bool(samples) and all(item.get("collector_active") is False for item in samples)
        reason = "데몬의 파일시스템 감지 훅 비활성" if unavailable else "실행별 감지 활성 상태 확인 실패"
        status = "SKIP" if unavailable and not required else "FAIL"
        return [Row(key, "eBPF", "Landlock 거부 이벤트", operation, expected,
                    reason, status) for key, operation, expected in specs]
    # Dedicated cases exercise Landlock rather than merely the RO root mount.
    denied = [item for item in samples if item["case"].id in {"N02", "N05", "N06"} and item["probe_pass"]]
    normal = [item for item in samples if item["case"].allowed and item["probe_pass"]]
    events = [(item, event) for item in samples for event in item["events"]]
    valid_sources = {"LANDLOCK_EBPF", "LANDLOCK_EBPF_FENTRY"}
    detected = any(event.get("source") in valid_sources for item in denied for event in item["events"])
    quiet = bool(normal) and all(not item["events"] and not item["violation"] for item in normal)
    attributed = bool(events) and all(str(event.get("run_id")) == item["run_id"]
                                    and event.get("source") in valid_sources for item, event in events)
    actual = (f"Landlock 시험 {len(denied)}건 / 전체 이벤트 {len(events)}건",
              f"정상 시험 {len(normal)}건 / 위반 {sum(bool(item['events']) or item['violation'] for item in normal)}건",
              f"run_id 대조 {len(events)}건")
    return [Row(key, "eBPF", "Landlock 거부 이벤트", operation, expected, observed,
                "PASS" if passed else "FAIL")
            for (key, operation, expected), observed, passed in zip(specs, actual, (detected, quiet, attributed))]


def exit_status(rows):
    if any(row.status in {"FAIL", "ERROR"} for row in rows):
        return 1
    return 0 if any(row.status == "PASS" for row in rows) else 2


def write_report(directory, rows, metadata):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    counts = {status: sum(row.status == status for row in rows) for status in ("PASS", "FAIL", "ERROR", "SKIP")}
    data = {"metadata": metadata, "counts": counts, "rows": [asdict(row) for row in rows]}
    (directory / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    def md(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["# 파일시스템 정책 자동 검증", "", " · ".join(f"{key} {value}" for key, value in counts.items()), "",
             "| 범위 | 경로 | 작업 | 기대 정책 | 실제 결과 | 판정 |", "|---|---|---|---|---|---|"]
    lines.extend("| " + " | ".join(md(value) for value in (row.group, row.path, row.operation,
                 row.expected, row.actual, row.status)) + " |" for row in rows)
    (directory / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    esc = lambda value: html.escape(str(value), quote=True)
    sections = []
    for index, group in enumerate(GROUPS):
        selected = [row for row in rows if row.group == group]
        if not selected:
            continue
        rendered = []
        for row in selected:
            evidence = (f'<a href="{esc(row.evidence)}">근거 보기</a>' if row.evidence else "")
            rendered.append(f'<tr><td>{esc(row.id)}</td><td class="path">{esc(row.path)}</td>'
                            f'<td>{esc(row.operation)}</td><td>{esc(row.expected)}</td>'
                            f'<td>{esc(row.actual)} {evidence}</td><td><b class="{esc(row.status)}">{esc(row.status)}</b></td></tr>')
        sections.append(f'<section id="group-{index}"><h2>{esc(group)} <small>{len(selected)}개 항목</small></h2>'
                        '<table><thead><tr><th>ID</th><th>경로</th><th>작업</th><th>기대 정책</th><th>실제 결과·근거</th><th>판정</th></tr></thead>'
                        '<tbody>' + "".join(rendered) + '</tbody></table></section>')
    badges = "".join(f'<span class="count {key}">{key} <strong>{value}</strong></span>' for key, value in counts.items())
    nav = "".join(f'<a href="#group-{index}">{esc(group)}</a>' for index, group in enumerate(GROUPS)
                  if any(row.group == group for row in rows))
    document = '''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CodeGuard 파일시스템 검증 결과</title><style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fb;color:#162338;font:15px/1.6 "Malgun Gothic","Noto Sans KR",sans-serif}
main{max-width:1680px;margin:32px auto;padding:0 28px}header,section{background:white;border:1px solid #d6deea;border-radius:16px;padding:24px;margin:20px 0}
h1{font-size:30px;margin:0 0 8px}h2{font-size:23px;margin:0 0 16px}h2 small{font-size:14px;color:#64748b;font-weight:400;margin-left:12px}
.counts{display:flex;gap:12px;margin:20px 0}.count{padding:7px 16px;border-radius:8px}.count strong{margin-left:12px}
.PASS{color:#117a39;background:#e9f8ee}.FAIL,.ERROR{color:#c02631;background:#fff0f1}.SKIP{color:#71582e;background:#fff8e8}
td b{padding:4px 8px;border-radius:6px}nav{display:flex;gap:18px;flex-wrap:wrap}a{color:#315ab9;text-decoration:none}
table{width:100%;border-collapse:collapse;table-layout:fixed;font-size:14px}th{text-align:left;background:#f5f8fc;color:#516279}td,th{padding:12px;border-bottom:1px solid #e6ebf2;vertical-align:top;overflow-wrap:anywhere}
th:nth-child(1){width:6%}th:nth-child(2){width:28%}th:nth-child(3){width:12%}th:nth-child(4){width:16%}th:nth-child(5){width:30%}th:nth-child(6){width:8%}td a{display:block}
.path{font-family:Consolas,monospace;overflow-wrap:anywhere;max-width:420px}td:last-child{white-space:nowrap}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;color:#64748b}
.note{color:#64748b;margin:8px 0}section{scroll-margin-top:20px}@media print{body{background:white}main{margin:0;padding:0}header,section{border-radius:0;break-inside:auto}tr{break-inside:avoid}nav{display:none}section{break-before:page}}
</style><main><header><h1>CodeGuard 파일시스템 정책 자동 검증</h1><p class="note">경로별 실제 접근 결과와 strace·eBPF 감지를 분리해 기록합니다. SKIP은 통과 수에 포함하지 않습니다.</p>'''
    document += '<div class="counts">' + badges + '</div><nav>' + nav + '</nav><details><summary>실행 환경·정책 정보</summary><pre>'
    document += esc(json.dumps(metadata, ensure_ascii=False, indent=2)) + '</pre></details></header>' + "".join(sections) + '</main></html>'
    output = directory / "report.html"
    output.write_text(document, encoding="utf-8")
    return output


def read_container_file(container, path, limit):
    """Bound tar and extracted contents; never extract a path onto the VM."""
    stream, _ = container.get_archive(path)
    buffer = io.BytesIO()
    try:
        for chunk in stream:
            if buffer.tell() + len(chunk) > limit + 131072:
                raise ValueError(f"증거 아카이브 크기 초과: {path}")
            buffer.write(chunk)
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    buffer.seek(0)
    with tarfile.open(fileobj=buffer, mode="r:") as archive:
        members = archive.getmembers()
        if len(members) != 1 or not members[0].isreg() or members[0].size > limit:
            raise ValueError(f"증거 파일 형식·크기 오류: {path}")
        with archive.extractfile(members[0]) as file:
            return file.read(limit + 1)


def _probe_paths(case):
    if case.operation == "rename":
        return "/workspace/cg-old", case.aux
    if case.operation == "move_dir":
        return "/workspace/cg-dir-old", case.aux
    if case.operation == "symlink_write":
        return "/workspace/cg-link", case.aux
    if case.operation == "dirfd_write":
        return "/etc", case.aux
    return case.path, case.aux


def trace_row(case, execution, trace, enforcement):
    from runner.security.filesystem_trace import analyze_filesystem_trace
    path, aux = _probe_paths(case)
    target = aux if case.operation == "dirfd_write" else path
    observed = analyze_filesystem_trace(trace)
    expected = "위반 없음" if case.operation == "fake" else "실제 실패 호출 기록·위반 판정"
    excerpt = next((line for line in trace.splitlines()
                    if target in line and re.search(r"= -1 (EACCES|EPERM|EROFS)\b", line)), "")
    if case.operation == "fake":
        passed = enforcement.status == "PASS" and not observed.detected and not execution.filesystem_limit_exceeded
        actual = f"추적 위반={observed.detected}, Runner 위반={execution.filesystem_limit_exceeded}"
    else:
        passed = enforcement.status == "PASS" and bool(excerpt) and observed.detected and execution.filesystem_limit_exceeded
        actual = excerpt or "대상 경로의 권한 거부 호출 기록 없음"
        actual += f" / 추적 판정={observed.detected}, Runner 정책 위반=" + ("FILESYSTEM_LIMIT" if execution.filesystem_limit_exceeded else "없음")
    return Row("T-" + case.id, "strace", case.path, OPERATIONS[case.operation], expected,
               actual, "PASS" if passed else "FAIL", f"evidence/{case.id}/trace.log")


def run_checks(args, output, rows, metadata):
    from unittest.mock import patch
    from runner.config import settings
    from runner.pipeline.compiler import get_docker_client, create_compile_container, compile_source
    from runner.pipeline.classifier import classify_execution, classify_policy_violations
    from runner.pipeline.execution import ExecutionResult, create_execution_container, execute_program
    from runner.pipeline.workspace import create_workspace, remove_workspace, build_stdin_archive
    from runner.metrics.cgroup_scope import ExecutionCgroupScope, validate_docker_cgroup_driver
    from runner.metrics.task_tracker import TaskTrackerClient
    from runner.security.filesystem_startup import STATUS_PATH, parse_filesystem_status
    from runner.security.filesystem_trace import TRACE_PATH, TRACE_LIMIT_BYTES
    from runner.tests.filesystem_helpers import pin_execution_image, prepare_filesystem_policy
    supported = "filesystem_violations" in ExecutionResult.__dataclass_fields__
    requested = args.ebpf != "off" and (args.ebpf == "require" or settings.task_tracker_enabled)
    enabled = requested and supported and settings.task_tracker_socket.exists()
    tracker = ObservedTaskTracker(TaskTrackerClient.from_settings(settings)) if enabled else None
    metadata.update({"ebpf_mode": args.ebpf, "ebpf_filesystem_supported": supported,
                     "task_tracker_enabled_for_test": enabled, "network": args.network})
    samples, abis = [], set()
    with ExitStack() as stack:
        stack.enter_context(patch.object(settings, "task_tracker_enabled", enabled))
        stack.enter_context(patch.object(settings, "execution_network", args.network))
        client = get_docker_client()
        stack.callback(client.close)
        image_id = pin_execution_image(client)
        metadata.update({"image_id": image_id, "docker": client.version().get("Version")})
        workspace = create_workspace(client, uuid4())
        stack.callback(remove_workspace, client, workspace)
        source = Path(__file__).with_name("filesystem_acceptance_probe.c").read_text(encoding="utf-8")
        policy = prepare_filesystem_policy(client, workspace, image_id, "C", source)
        metadata.update({"policy_id": policy.policy_id, "policy_rules": [
            {"path": rule.path, "profile": rule.profile.value} for rule in policy.rules]})
        compiler = create_compile_container(client, workspace, "C", image_id=image_id)
        stack.callback(compiler.remove, force=True)
        compiled = compile_source(compiler, workspace, "C", source)
        if not compiled.success or not compiled.artifact_ready:
            raise RuntimeError("진단 프로그램 컴파일 실패: " + compiled.stderr)
        driver = validate_docker_cgroup_driver(client)
        cases = build_cases(policy)
        if args.group in GROUP_OPTIONS:
            cases = [case for case in cases if case.group == GROUP_OPTIONS[args.group]]
        elif args.group == "strace":
            cases = [case for case in cases if case.check_strace]
        elif args.group == "ebpf":
            cases = [case for case in cases if case.id in {"W01", "W12", "N02", "N05"}]
        for index, case in enumerate(cases, 1):
            directory = output / "evidence" / case.id
            directory.mkdir(parents=True)
            path, aux = _probe_paths(case)
            payload = f"{case.operation}\n{path}\n{aux}\n"
            run_id = uuid4()
            try:
                if case.id == "W13":
                    cpp_compiler = create_compile_container(client, workspace, "CPP", image_id=image_id)
                    stack.callback(cpp_compiler.remove, force=True)
                    cpp_result = compile_source(cpp_compiler, workspace, "CPP", CPP_SOURCE)
                    if not cpp_result.success or not cpp_result.artifact_ready:
                        raise RuntimeError("C++ 진단 프로그램 컴파일 실패: " + cpp_result.stderr)
                if not compiler.put_archive("/workspace", build_stdin_archive(payload)):
                    raise RuntimeError("시험 입력 전달 실패")
                with ExitStack() as execution_stack:
                    scope = ExecutionCgroupScope.create(root=settings.execution_cgroup_root, run_id=run_id, driver=driver)
                    execution_stack.callback(scope.remove)
                    container = create_execution_container(client=client, workspace=workspace, stdin=payload,
                        job_id=workspace.job_id, run_id=run_id, memory_limit_mb=128, cpu_bandwidth=1.0,
                        pids_limit=32, cgroup_scope=scope, filesystem_policy=policy, network_mode=args.network)
                    execution_stack.callback(container.remove, force=True, v=True)
                    container.reload()
                    if index == 1:
                        mounts = container.attrs.get("Mounts", [])
                        root_ro = container.attrs["HostConfig"].get("ReadonlyRootfs") is True
                        work_rw = any(m.get("Destination") == "/workspace" and m.get("Name") == workspace.volume_name
                                      and m.get("RW") is True for m in mounts)
                        rows.extend([Row("P01", "환경", "/", "RootFS", "RO", "RO" if root_ro else "RW", "PASS" if root_ro else "FAIL"),
                                     Row("P02", "환경", "/workspace", "마운트", "RW", "RW" if work_rw else "설정 불일치", "PASS" if work_rw else "FAIL")])
                    result = execute_program(container, workspace.job_id, run_id, timeout_ms=3000,
                        output_limit_bytes=65536, cgroup_scope=scope, filesystem_policy_id=policy.policy_id,
                        filesystem_policy=policy, task_tracker=tracker)
                    (directory / "stdout.txt").write_text(result.stdout, encoding="utf-8")
                    (directory / "stderr.txt").write_text(result.stderr, encoding="utf-8")
                    row = evaluate_probe(case, result.stdout, exit_code=result.exit_code, system_error=result.system_error)
                    row.evidence = f"evidence/{case.id}/result.json"
                    events = [asdict(event) for event in getattr(result, "filesystem_violations", ())]
                    collector_active = tracker.active_by_run.pop(str(run_id), None) if tracker else None
                    record = {"case": asdict(case), "run_id": str(run_id), "exit_code": result.exit_code,
                              "system_error": result.system_error, "filesystem_limit_exceeded": result.filesystem_limit_exceeded,
                              "runner_status": classify_execution(result).status.value,
                              "policy_violations": [item.value for item in classify_policy_violations(result)],
                              "filesystem_violation_source": getattr(result, "filesystem_violation_source", None),
                              "collector_active": collector_active,
                              "events": events, "assessment": asdict(row)}
                    (directory / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
                    rows.append(row)
                    status = read_container_file(container, STATUS_PATH, 16384)
                    (directory / "filesystem.status").write_bytes(status)
                    abis.add(parse_filesystem_status(status, policy.policy_id).abi)
                    trace = read_container_file(container, TRACE_PATH, TRACE_LIMIT_BYTES).decode("utf-8", errors="replace")
                    (directory / "trace.log").write_text(trace, encoding="utf-8")
                    if case.check_strace:
                        rows.append(trace_row(case, result, trace, row))
                    if case.allowed and case.operation != "fake":
                        rows.append(Row("F-" + case.id, "strace", case.path, "허용 작업 오탐 확인", "위반 없음",
                            f"Runner 위반={result.filesystem_limit_exceeded}",
                            "PASS" if row.status == "PASS" and not result.filesystem_limit_exceeded else "FAIL"))
                    samples.append({"case": case, "run_id": str(run_id), "events": events,
                                    "collector_active": collector_active,
                                    "violation": result.filesystem_limit_exceeded, "probe_pass": row.status == "PASS"})
                print(f"[{index:02}/{len(cases):02}] {row.status:5} {row.operation} | {row.path} | 기대={row.expected} | 실제={row.actual}", flush=True)
            except Exception as exc:
                rows.append(Row(case.id + "-ERR", case.group, case.path, OPERATIONS[case.operation],
                                "시험 완료", str(exc), "ERROR", f"evidence/{case.id}/error.txt"))
                (directory / "error.txt").write_text(str(exc), encoding="utf-8")
                print(f"[{index:02}/{len(cases):02}] ERROR {case.path}: {exc}", flush=True)
        metadata["landlock_abis"] = sorted(abis)
        rows.append(Row("P03", "환경", "/run/codeguard-trace/filesystem.status", "Landlock 적용",
                        "ABI ≥ 7, APPLIED", str(sorted(abis)), "PASS" if abis and min(abis) >= 7 else "FAIL"))
        if not args.group or args.group in {"strace", "ebpf"}:
            rows.extend(ebpf_rows(samples, supported=supported, enabled=enabled, required=args.ebpf == "require"))
        elif args.ebpf == "require":
            rows.extend(ebpf_rows(samples, supported=supported, enabled=enabled, required=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description="경로·작업별 파일시스템 검증 및 HTML/JSON/Markdown 보고서 생성")
    parser.add_argument("--output-dir", type=Path, help="결과 저장 폴더 (기본: artifacts/filesystem-acceptance-시각)")
    parser.add_argument("--ebpf", choices=("auto", "off", "require"), default="auto",
                        help="auto: 지원 환경에서 검증, off: strace 기준, require: eBPF 미지원도 실패")
    parser.add_argument("--network", default="none", help="시험 컨테이너 Docker 네트워크 (기본: none)")
    parser.add_argument("--group", choices=(*GROUP_OPTIONS, "strace", "ebpf"), help="특정 범위만 재실행")
    args = parser.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    output = (args.output_dir or Path("artifacts") / f"filesystem-acceptance-{stamp}-{uuid4().hex[:6]}").resolve()
    if output.exists() and any(output.iterdir()):
        parser.error("기존 증거를 보존하기 위해 비어 있는 결과 폴더를 지정해야 합니다.")
    output.mkdir(parents=True, exist_ok=True)
    metadata = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
                "python": platform.python_version(), "report_scope": "real Docker / Runner integration"}
    try:
        metadata["git_commit"] = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                                               timeout=5, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        metadata["git_commit"] = "unknown"
    rows = []
    try:
        run_checks(args, output, rows, metadata)
    except KeyboardInterrupt:
        rows.append(Row("INTERRUPTED", "환경", "실행 중단", "시험 완료", "전체 수행", "사용자 중단·부분 결과", "ERROR"))
    except Exception as exc:
        rows.append(Row("ENV-ERROR", "환경", "Docker / Runner", "시험 준비·정리", "정상 완료", str(exc), "ERROR"))
    report = write_report(output, rows, metadata)
    counts = {status: sum(row.status == status for row in rows) for status in ("PASS", "FAIL", "ERROR", "SKIP")}
    print("\n" + " | ".join(f"{key}={value}" for key, value in counts.items()))
    print(f"HTML: {report}\nJSON: {output / 'results.json'}\nMarkdown: {output / 'results.md'}")
    return exit_status(rows)


if __name__ == "__main__":
    raise SystemExit(main())
