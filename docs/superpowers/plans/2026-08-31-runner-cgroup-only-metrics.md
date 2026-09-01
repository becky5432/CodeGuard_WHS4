# Runner cgroup 전용 자원 측정 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 모든 Execution Job에 실행별 상위 cgroup을 필수 적용하고 Memory/PIDs Peak 및 제한 이벤트를 cgroup v2에서만 회수한다.

**Architecture:** `execute_job()`이 컴파일 성공 직후 Docker cgroup 드라이버를 검증하고 `ExecutionCgroupScope`를 항상 생성한다. 실행 컨테이너는 해당 scope를 `cgroup_parent`로 사용하며, 실행 종료 후 `memory.peak`, `pids.peak`, `memory.events`, `pids.events`를 엄격하게 읽는다. Docker Stats 기반 `ResourceMonitor`는 삭제하고 필수 cgroup 값 회수 실패는 `CgroupScopeError`를 통해 `ERROR / INTERNAL_ERROR`로 변환한다.

**Tech Stack:** Python 3.12, Docker SDK for Python, Linux cgroup v2, `unittest`

## Global Constraints

- Runner는 Linux cgroup v2 환경을 필수로 요구한다.
- Docker cgroup 드라이버는 `cgroupfs` 또는 `systemd`여야 한다.
- API 응답 스키마는 변경하지 않는다.
- Docker의 `mem_limit`, `memswap_limit`, `pids_limit`, `nano_cpus` 제한은 유지한다.
- `PidsLimitMonitor`는 실행 중 제한 도달 감시용으로 유지하되 최종 Peak fallback으로 사용하지 않는다.
- Container, Volume, 실행별 cgroup Cleanup은 성공·실패와 관계없이 수행한다.

---

### Task 1: cgroup 활성화 설정 제거와 필수 scope 생성

**Files:**
- Modify: `runner/config.py`
- Modify: `runner/pipeline/executor.py`
- Modify: `runner/tests/test_executor.py`
- Modify: `runner/tests/test_execute_api.py`

**Interfaces:**
- Consumes: `validate_docker_cgroup_driver(client) -> str`, `ExecutionCgroupScope.create(root, run_id, driver)`
- Produces: 모든 컴파일 성공 Job에서 생성되어 Execution Container와 `execute_program()`에 전달되는 `ExecutionCgroupScope`

- [ ] **Step 1: 활성화 설정 없이 scope가 생성되는 실패 테스트 작성**

`test_enabled_cgroup_scope_is_passed_and_removed`에서 `execution_cgroup_enabled` 패치를 제거하고 이름을 `test_cgroup_scope_is_always_passed_and_removed`로 변경한다. `test_settings_include_execution_cgroup_configuration`은 `execution_cgroup_enabled`가 없고 `execution_cgroup_root`만 존재한다고 검증한다.

- [ ] **Step 2: 실패 테스트 실행**

Run: `python -m unittest runner.tests.test_executor runner.tests.test_execute_api`

Expected: 기존 조건문 때문에 scope 전달 테스트가 실패하고 설정 필드 테스트도 실패한다.

- [ ] **Step 3: scope 필수 생성 구현**

`Settings`에서 `execution_cgroup_enabled`를 삭제한다. `execute_job()`에서 조건문 없이 드라이버를 검증하고 scope를 생성한 뒤 다음처럼 직접 전달한다.

```python
cgroup_driver = validate_docker_cgroup_driver(client)
execution_cgroup_scope = ExecutionCgroupScope.create(
    root=settings.execution_cgroup_root,
    run_id=run_id,
    driver=cgroup_driver,
)

execution_container = create_execution_container(
    ...,
    cgroup_scope=execution_cgroup_scope,
)
execution_result = execute_program(
    ...,
    cgroup_scope=execution_cgroup_scope,
)
```

- [ ] **Step 4: 관련 테스트 통과 확인**

Run: `python -m unittest runner.tests.test_executor runner.tests.test_execute_api`

Expected: PASS

### Task 2: cgroup 파일 엄격 읽기

**Files:**
- Modify: `runner/metrics/cgroup_scope.py`
- Modify: `runner/tests/test_cgroup_scope.py`

**Interfaces:**
- Consumes: 실행별 cgroup 경로의 `memory.peak`, `pids.peak`, `memory.events`, `pids.events`
- Produces: 완전한 `CgroupMetrics`; 읽기 또는 파싱 실패 시 `CgroupScopeError`

- [ ] **Step 1: 누락·잘못된 형식 실패 테스트 작성**

기존 `test_snapshot_returns_none_when_peak_files_are_missing`을 `CgroupScopeError` 기대 테스트로 바꾸고, Peak 정수 파일의 비정수 값과 events의 잘못된 행 형식도 각각 예외를 발생시키는지 검증한다.

- [ ] **Step 2: 실패 테스트 실행**

Run: `python -m unittest runner.tests.test_cgroup_scope`

Expected: 현재 `_read_int()`와 `_read_events()`가 `None` 또는 빈 딕셔너리를 반환하므로 FAIL

- [ ] **Step 3: 엄격한 읽기 구현**

`_read_int()`와 `_read_events()`에서 파일 경로와 원인을 포함한 `CgroupScopeError`를 발생시킨다. events의 각 행은 정확히 `key integer` 형식이어야 하며 `memory.events`에는 `oom_kill`, `pids.events`에는 `max`가 존재해야 한다.

- [ ] **Step 4: cgroup 단위 테스트 통과 확인**

Run: `python -m unittest runner.tests.test_cgroup_scope`

Expected: PASS

### Task 3: Docker Stats fallback 제거와 cgroup 결과 단일화

**Files:**
- Modify: `runner/pipeline/execution.py`
- Modify: `runner/tests/test_execution.py`
- Delete: `runner/metrics/resource_monitor.py`
- Delete: `runner/tests/test_resource_monitor.py`

**Interfaces:**
- Consumes: 필수 `ExecutionCgroupScope`, 실시간 `PidsLimitMonitor`
- Produces: cgroup 전용 `ExecutionResult.memory_peak_bytes`, `ExecutionResult.pids_peak`, `oom_killed`, `pids_limit_exceeded`

- [ ] **Step 1: Stats 미호출·cgroup 실패 전파 테스트 작성**

`test_execute_program_prefers_parent_cgroup_peak_values`에서 `ResourceMonitor` 패치를 제거하고 cgroup 값을 그대로 반환하는지 검증한다. `cgroup_scope.snapshot.side_effect = CgroupScopeError(...)`인 경우 예외가 전파되는 테스트와 `container.stats.assert_not_called()` 검증을 추가한다.

- [ ] **Step 2: 실패 테스트 실행**

Run: `python -m unittest runner.tests.test_execution`

Expected: ResourceMonitor가 생성·시작되고 snapshot 오류가 삼켜지므로 FAIL

- [ ] **Step 3: Stats 코드와 fallback 제거**

`ResourceMonitor` import, 생성, 시작, 중지와 fallback 대입을 제거한다. `create_execution_container()`와 `execute_program()`의 `cgroup_scope`를 필수 인자로 만들고 snapshot 오류를 잡지 않는다. 최종 값은 다음처럼 단일화한다.

```python
cgroup_metrics = cgroup_scope.snapshot()
memory_peak_bytes = cgroup_metrics.memory_peak_bytes
pids_peak = cgroup_metrics.pids_peak
oom_killed = cgroup_metrics.oom_killed
pids_limit_exceeded = (
    pids_limit_exceeded or cgroup_metrics.pids_limit_exceeded
)
```

Docker 처리 자체가 실패한 반환 경로에는 출처가 다른 Peak 값을 넣지 않는다. 사용되지 않는 ResourceMonitor 구현과 테스트 파일을 삭제한다.

- [ ] **Step 4: 실행 단위 테스트 통과 확인**

Run: `python -m unittest runner.tests.test_execution runner.tests.test_cgroup_scope`

Expected: PASS

### Task 4: INTERNAL_ERROR 및 Cleanup 통합 검증

**Files:**
- Modify: `runner/tests/test_executor.py`

**Interfaces:**
- Consumes: `execute_program()`에서 전파된 `CgroupScopeError`
- Produces: `RunnerResponse(status=ERROR, reason_code=INTERNAL_ERROR)`와 실행 컨테이너·Volume·scope Cleanup 증거

- [ ] **Step 1: cgroup snapshot 실패 통합 테스트 작성**

`execute_program`이 `CgroupScopeError("Execution cgroup 측정값을 읽지 못했습니다.")`를 발생시키도록 설정하고 응답이 `ERROR / INTERNAL_ERROR`인지, Execution Container와 Compile Container가 강제 삭제됐는지, Volume과 scope가 정리됐는지 검증한다.

- [ ] **Step 2: 실패 테스트 실행**

Run: `python -m unittest runner.tests.test_executor`

Expected: Task 1~3 구현 전에는 조건부 scope 또는 snapshot 오류 삼킴 때문에 FAIL

- [ ] **Step 3: 오류 경로 최소 보완**

필요한 경우 `execute_job()`의 기존 `except RunnerError` 흐름만 사용하도록 예외를 보존한다. cgroup 측정 실패를 성공 결과나 근삿값으로 변환하는 catch 문은 두지 않는다.

- [ ] **Step 4: Runner 전체 회귀 테스트 실행**

Run: `python -m unittest discover -s runner/tests -p "test_*.py"`

Expected: 모든 테스트 PASS

- [ ] **Step 5: 정적 검증과 커밋**

Run: `git diff --check`

Expected: 출력 없음, exit code 0

Run: `rg -n "ResourceMonitor|resource_monitor|execution_cgroup_enabled|\.stats\(" runner`

Expected: 일치 항목 없음

변경 파일만 stage하고 `feat(runner): cgroup 전용 자원 측정 적용`으로 커밋한다.
