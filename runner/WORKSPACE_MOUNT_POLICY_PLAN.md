# Runner Workspace Read-Only Execution Mount Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compile Container는 Job Volume을 읽기·쓰기로 사용하고 Execution Container는 같은 Volume을 읽기 전용으로 사용한다.

**Architecture:** 기존 Job Volume과 Compile/Execution Container 분리 구조를 유지한다. Compile 설정은 변경하지 않고 Execution Container 생성 설정의 Volume 모드만 `ro`로 변경한다.

**Tech Stack:** Python, Docker SDK for Python, unittest

## Global Constraints

- Compile Container의 `/workspace` 마운트는 `rw`를 유지한다.
- Execution Container의 `/workspace` 마운트만 `ro`로 변경한다.
- 루트 파일시스템, tmpfs, 자원 제한 설정은 변경하지 않는다.

---

### Task 1: Execution Container Workspace 읽기 전용화

**Files:**
- Modify: `runner/tests/test_execution.py:41-47`
- Modify: `runner/pipeline/execution.py:104-110`

**Interfaces:**
- Consumes: `VolumeWorkspace.volume_name`
- Produces: Docker SDK `containers.create(..., volumes={... "mode": "ro"})`

- [ ] **Step 1: 테스트 기대값을 `ro`로 변경한다**

```python
volumes={
    self.workspace.volume_name: {
        "bind": "/workspace",
        "mode": "ro",
    },
}
```

- [ ] **Step 2: 테스트가 현재 `rw` 설정 때문에 실패하는지 확인한다**

Run: `python -m unittest runner.tests.test_execution -v`

Expected: Execution Container 생성 인자의 Volume 모드가 `rw`여서 FAIL

- [ ] **Step 3: Execution Container의 Volume 모드를 `ro`로 변경한다**

```python
volumes={
    workspace.volume_name: {
        "bind": "/workspace",
        "mode": "ro",
    },
}
```

- [ ] **Step 4: 관련 테스트와 Runner 전체 테스트를 실행한다**

Run: `python -m unittest runner.tests.test_execution -v`

Expected: PASS

Run: `python -m unittest discover -s runner/tests -v`

Expected: 전체 PASS

- [ ] **Step 5: 변경 파일을 커밋한다**

```text
feat(runner): 실행 Workspace 읽기 전용 마운트 적용
```
