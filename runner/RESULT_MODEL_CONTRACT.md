# Runner 최신 응답 계약 전환 설계

## 1. 목적

Backend의 최신 `RunnerResponse` 계약과 실제 Runner의 응답 모델을 통일한다.

현재 Backend는 대표 실행 상태와 단계별 처리 결과를 다음과 같이 구분한다.

- `status`: 전체 실행의 대표 상태
- `reason_code`: 대표 실패 또는 차단 사유
- `stage_summary`: 단계별 성공·실패·생략 및 오류 내역

Runner의 기존 단일 `stage` 필드는 제거하고 단계 정보는 모두 `stage_summary`로 전달한다.

## 2. 현재 불일치

| 항목 | Backend 최신 계약 | 현재 Runner |
| --- | --- | --- |
| 실행 상태 | `SUCCESS`, `ERROR`, `BLOCKED` | `SUCCESS`, `ERROR` |
| 사유 코드 | 정책 제한 포함 9개 | 컴파일·런타임·내부 오류 3개 |
| `StageError` | `reason_code`, `message` | `stage`, `message` |
| `StageSummary` | 성공·실패·생략·오류 | 오류 리스트만 존재 |
| 대표 `stage` | 사용하지 않음 | `RunnerResponse.stage` 존재 |

## 3. 목표 모델

### 3.1 실행 상태

```python
class RunnerStatus(str, Enum):
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"
```

### 3.2 사유 코드

```python
class RunnerReasonCode(str, Enum):
    TIME_LIMIT = "TIME_LIMIT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    PROCESS_LIMIT = "PROCESS_LIMIT"
    OUTPUT_LIMIT = "OUTPUT_LIMIT"
    NETWORK_BLOCKED = "NETWORK_BLOCKED"
    COMPILE_ERROR = "COMPILE_ERROR"
    COMPILE_TIMEOUT = "COMPILE_TIMEOUT"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"
```

정책 제한 기능이 아직 구현되지 않았더라도 API 계약을 먼저 통일한다. 실제 `BLOCKED` 판정은 자원 제한 및 위반 감지 기능을 구현한 뒤 연결한다.

### 3.3 단계별 오류

```python
class StageError(BaseModel):
    reason_code: RunnerReasonCode
    message: str
```

오류가 발생한 단계는 `StageError` 안에 중복해서 넣지 않는다. `StageSummary.errors`의 key가 오류 단계를 나타낸다.

### 3.4 단계 요약

```python
class StageSummary(BaseModel):
    succeeded: list[RunnerStage] = Field(default_factory=list)
    failed: list[RunnerStage] = Field(default_factory=list)
    skipped: list[RunnerStage] = Field(default_factory=list)
    errors: dict[
        RunnerStage,
        list[StageError],
    ] = Field(default_factory=dict)
```

| 필드 | 의미 |
| --- | --- |
| `succeeded` | 정상적으로 완료된 단계 |
| `failed` | 실패하거나 정책에 의해 중단된 단계 |
| `skipped` | 앞 단계 실패로 실행하지 않은 단계 |
| `errors` | 단계별 세부 오류 목록 |

### 3.5 RunnerResponse

```python
class RunnerResponse(BaseModel):
    job_id: UUID
    run_id: UUID
    status: RunnerStatus
    reason_code: RunnerReasonCode | None = None
    error_message: str | None = None
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    compile_log: str | None = None
    resource_usage: ResourceUsage | None = None
    stage_summary: StageSummary = Field(default_factory=StageSummary)
    finished_at: datetime | None = None
```

기존 `stage: RunnerStage | None` 필드는 제거한다.

Backend에서는 `stage_summary`와 `finished_at`이 필수다. 현재 Runner는 실행 도중 응답 객체를 만들기 때문에 `finished_at`은 내부적으로 `None`을 임시 허용하고, `/execute` 응답 반환 전에 반드시 현재 UTC 시각을 설정한다.

## 4. 단계별 판정 기준

| 상황 | 대표 `status` | 대표 `reason_code` | `succeeded` | `failed` | `skipped` |
| --- | --- | --- | --- | --- | --- |
| 전체 성공 | `SUCCESS` | `null` | Workspace, Compile, Execute, Cleanup | 없음 | 없음 |
| Workspace 실패 | `ERROR` | `INTERNAL_ERROR` | 없음 | Workspace | Compile, Execute |
| 컴파일 실패 | `ERROR` | `COMPILE_ERROR` | Workspace | Compile | Execute |
| 실행 실패 | `ERROR` | `RUNTIME_ERROR` | Workspace, Compile | Execute | 없음 |
| 정책 제한 | `BLOCKED` | 해당 제한 코드 | Workspace, Compile | Execute | 없음 |
| 실행 성공 후 Cleanup 실패 | `SUCCESS` | `null` | Workspace, Compile, Execute | Cleanup | 없음 |

Cleanup 실패는 이미 완료된 컴파일·실행 결과를 덮어쓰지 않는다. Cleanup 오류는 `stage_summary.failed`와 `stage_summary.errors`에만 기록한다.

## 5. executor.py 변경 방향

기존 단일 단계 지정 방식은 제거한다.

```python
stage=RunnerStage.COMPILE
```

대신 단계별 결과를 `StageSummary`에 기록한다.

```python
stage_summary.failed.append(RunnerStage.COMPILE)
stage_summary.skipped.append(RunnerStage.EXECUTE)
```

Cleanup 오류는 다음 구조로 누적한다.

```python
stage_summary.failed.append(RunnerStage.CLEANUP)
stage_summary.errors.setdefault(
    RunnerStage.CLEANUP,
    [],
).append(
    StageError(
        reason_code=RunnerReasonCode.INTERNAL_ERROR,
        message="Cleanup에 실패했습니다.",
    )
)
```

중복 단계가 들어가지 않도록 단계 추가 시 존재 여부를 확인하거나 전용 보조 함수를 둔다.

## 6. 응답 예시

### 6.1 전체 성공

```json
{
  "status": "SUCCESS",
  "reason_code": null,
  "stage_summary": {
    "succeeded": ["WORKSPACE", "COMPILE", "EXECUTE", "CLEANUP"],
    "failed": [],
    "skipped": [],
    "errors": {}
  }
}
```

### 6.2 컴파일 실패

```json
{
  "status": "ERROR",
  "reason_code": "COMPILE_ERROR",
  "stage_summary": {
    "succeeded": ["WORKSPACE", "CLEANUP"],
    "failed": ["COMPILE"],
    "skipped": ["EXECUTE"],
    "errors": {
      "COMPILE": [
        {
          "reason_code": "COMPILE_ERROR",
          "message": "소스 코드 컴파일에 실패했습니다."
        }
      ]
    }
  }
}
```

### 6.3 실행 성공 후 Cleanup 실패

```json
{
  "status": "SUCCESS",
  "reason_code": null,
  "stdout": "Hello",
  "exit_code": 0,
  "stage_summary": {
    "succeeded": ["WORKSPACE", "COMPILE", "EXECUTE"],
    "failed": ["CLEANUP"],
    "skipped": [],
    "errors": {
      "CLEANUP": [
        {
          "reason_code": "INTERNAL_ERROR",
          "message": "Job Volume 삭제에 실패했습니다."
        }
      ]
    }
  }
}
```

## 7. 검증 기준

- Runner가 생성한 JSON을 Backend의 `RunnerResponse` 모델로 검증할 수 있어야 한다.
- 모든 응답에 `stage_summary`와 `finished_at`이 포함되어야 한다.
- 단일 `stage` 필드는 반환하지 않아야 한다.
- 컴파일 실패 시 Execution 단계는 `skipped`에 기록되어야 한다.
- Cleanup 실패가 기존 `status`, `stdout`, `stderr`, `exit_code`, `compile_log`를 덮어쓰지 않아야 한다.
- 정책 제한 구현 전에도 `BLOCKED`와 정책 사유 코드가 스키마에 정의되어 있어야 한다.
