# eBPF 사용자 프로세스·스레드 분리 측정

## 1. 목적

cgroup v2의 `pids.peak`는 Execution cgroup에 포함된 모든 Task의 최대값을
제공하지만 프로세스와 스레드를 구분하지 않는다. CodeGuard는 자원 제한
검증과 사용자 코드 실행 구조 분석을 분리하기 위해 두 종류의 Peak를
독립적으로 측정한다.

| 지표 | 측정 범위 | 용도 |
| --- | --- | --- |
| `pids_peak` | Execution cgroup 전체 생명주기의 Task | `pids_limit` 적용 결과 검증 |
| `user_task_peak` | 사용자 코드의 Root TID에서 파생된 Task | 사용자 코드의 최대 동시 Task 분석 |
| `process_at_user_task_peak` | `user_task_peak`가 갱신된 시점의 프로세스 | 실행 구조 분석 |
| `thread_at_user_task_peak` | 같은 시점의 추가 스레드 | 실행 구조 분석 |

`pids_peak`와 `user_task_peak`는 측정 범위가 다르므로 값과 최대 발생 시점이
서로 다를 수 있다. 프로세스 수와 추가 스레드 수는 각각의 독립 Peak가
아니라, `user_task_peak`가 갱신된 동일 이벤트의 값을 함께 저장한다.

```text
cgroup 측정
└─ pids_peak
   └─ Execution cgroup 전체 Task가 최대가 된 시점

eBPF 측정
└─ user_task_peak
   ├─ process_at_user_task_peak
   └─ thread_at_user_task_peak
      └─ 사용자 Task가 최대가 된 동일 시점
```

## 2. 제한과 측정의 역할

Task 하드 제한은 기존처럼 cgroup의 `pids.max`에 대응하는 `pids_limit`으로
적용한다. eBPF 측정값은 컨테이너를 종료하거나 `PIDS_LIMIT`을 판정하는
근거가 아니며, 사용자 코드가 만든 프로세스와 스레드 구성을 설명하는
분석 지표다.

Execution cgroup 전체의 현재값과 Peak는 커널의 `pids.current`와
`pids.peak`를 사용한다. eBPF에서 별도의 `container_task_current`와
`container_task_peak`를 중복 계산하지 않는다. 따라서 컨테이너 생명주기
Task의 측정 오류가 사용자 코드 계보의 Snapshot을 무효화하지 않는다.

바이너리 프로토콜 V2의 `initial_task_count` 요청 필드는 기존 Runner와의
호환성을 위해 유지하지만 native tracker의 측정에는 사용하지 않는다.

## 3. 측정 흐름

1. Execution Container는 사용자 프로그램 대신 `codeguard-init`을 먼저 실행한다.
2. `codeguard-init`은 실제 사용자 프로세스의 real/effective/saved/filesystem UID/GID와 허용된 supplementary groups, Capability, NoNewPrivs를 검증한다. 선행 SIGUSR1을 폐기한 다음 보호된 증거를 완성하고 대기한다.
3. Runner는 Execution cgroup ID와 컨테이너의 Root TID를 native tracker에 등록한다.
4. Runner가 보호된 권한 증거를 검증한다.
5. 검증에 성공한 경우에만 Runner가 `SIGUSR1`을 전달한다.
6. `codeguard-init`은 별도 자식 프로세스를 만들지 않고 `exec`으로 사용자 프로그램을 실행한다.
7. eBPF는 `sched_process_fork`와 `sched_process_exit` 이벤트로 Root TID에서 파생된 Task의 생성과 종료를 추적한다.
8. 이벤트마다 사용자 Task 수와 생존 TGID 수를 갱신하고, 추가 스레드 수를 두 값의 차이로 계산한다.
9. 사용자 Task 수가 기존 최대값을 초과하면 `user_task_peak`와 그 시점의 프로세스·추가 스레드 수를 함께 저장한다.
10. 실행 종료 후 Runner가 native tracker에 Snapshot을 요청하여 최종 Peak 값을 회수한다.

`codeguard-init`을 사용하는 이유는 추적 대상을 등록하기 전에 사용자
프로그램이 실행되어 짧은 Task 생성 이벤트가 누락되는 것을 방지하기
위해서다. `exec`을 사용하므로 등록한 Root TID도 유지된다.

### 3.1 실행별 계보 격리

`tracked_tasks`는 TID와 함께 해당 Task가 속한 `run_id`를 저장한다. Fork와
Exit 이벤트를 처리할 때 Task에 저장된 `run_id`와 현재 Execution cgroup의
`run_id`가 같은지 확인한다. 두 값이 다르면 Cleanup 실패 후 재사용된 오래된
TID일 수 있으므로 현재 실행의 사용자 계보에 포함하지 않는다.

```text
Task의 run_id == 현재 cgroup의 run_id
→ 현재 실행의 사용자 Task로 처리

Task의 run_id != 현재 cgroup의 run_id
→ 오래된 실행 정보로 보고 무시
```

### 3.2 native tracker 종료와 Cleanup

native tracker는 `SIGINT`와 `SIGTERM`을 `SA_RESTART` 없는 `sigaction()`으로
등록하고, signal handler에서 non-blocking self-pipe에 종료 알림을 기록한다.
메인 루프는 `poll()`로 요청 소켓과 self-pipe를 함께 기다린다. 따라서 종료
신호가 `poll()` 호출 전이나 대기 중 어느 시점에 도착하더라도 self-pipe가
읽기 가능 상태가 되어 대기가 해제된다. 클라이언트가 연결된 후 요청을 보내지
않는 경우에도 accepted socket과 self-pipe를 함께 `poll()`하므로 종료 신호를
처리할 수 있다. 따라서 `systemctl stop`과 `systemctl restart`가 요청 대기나
수신 대기 때문에 `deactivating` 상태에 머물지 않는다.

실행 Cleanup에서는 `tracked_cgroups`, `tracked_tasks`, `process_tasks`,
`run_metrics`를 모두 정리한다. 하나의 Map 삭제가 실패해도 나머지 삭제를
계속 시도하며, `ENOENT`를 제외한 최초 오류를 Runner에 반환한다. 이를 통해
삭제 실패를 성공으로 숨기지 않고 Runner 로그에서 확인할 수 있다.

## 4. 프로세스와 스레드 분류

Linux 커널에서 TID는 개별 Task의 식별자이고 TGID는 같은 프로세스에
속한 Task를 묶는 식별자다.

| TID | TGID | 분류 |
| ---: | ---: | --- |
| 100 | 100 | 프로세스의 대표 Task |
| 101 | 100 | 프로세스 100의 추가 스레드 |
| 200 | 200 | 별도 프로세스의 대표 Task |

`TID == TGID`는 새로운 프로세스의 대표 Task를 식별하는 기준이다. 프로세스가
등록된 이후의 생존 여부는 대표 Task의 생존 여부가 아니라 동일 TGID에 속한
전체 Task의 생존 수로 판단한다. 따라서 대표 Task가 먼저 종료되어도 같은
TGID의 Task가 하나라도 남아 있으면 해당 프로세스는 생존한 것으로 본다.

현재값은 다음 기준으로 계산한다.

```text
user_task_current
= 사용자 코드 계보에서 현재 생존한 전체 Task 수

process_current
= 현재 생존한 서로 다른 TGID 그룹 수

thread_current
= user_task_current - process_current
```

여기서 `thread_current`는 커널 관점의 전체 스레드 수가 아니라, 프로세스
그룹별 기준 Task 하나를 제외한 **추가 스레드 수**다. `TID != TGID`인 Task의
생성·종료 이벤트만으로 `thread_current`를 직접 증감하면 대표 Task 선종료
상황에서 중복 집계될 수 있으므로 사용하지 않는다.

예를 들어 프로세스 1개에 Task 3개가 생존하면 다음과 같이 표시한다.

```text
사용자 Task 3개
= 프로세스 그룹 1개 + 추가 스레드 2개
```

대표 Task가 먼저 종료되어 Task 2개만 남더라도 프로세스 그룹은 유지된다.

```text
사용자 Task 2개
= 프로세스 그룹 1개 + 추가 스레드 1개
```

카운터는 항상 다음 불변식을 만족해야 한다.

```text
user_task_current
= process_current + thread_current
```

## 5. Runner API 예시

```json
{
  "resource_usage": {
    "pids_peak": 6,
    "user_task_peak": 3,
    "process_at_user_task_peak": 1,
    "thread_at_user_task_peak": 2
  }
}
```

위 예시는 Execution cgroup 전체 생명주기에는 최대 6개의 Task가 있었고,
사용자 코드 계보에는 최대 3개의 Task가 동시에 존재했음을 의미한다.
사용자 Task Peak 시점의 구성은 프로세스 1개와 추가 스레드 2개다.

## 6. 실패 처리

eBPF 측정은 실행 분석을 위한 보조 기능이다. Tracker 비활성화, 등록 실패,
Snapshot 실패 또는 측정 오류가 발생해도 사용자 프로그램 실행과 cgroup
제한 판정은 계속한다. 이 경우 다음 필드는 `null`로 반환한다.

```json
{
  "user_task_peak": null,
  "process_at_user_task_peak": null,
  "thread_at_user_task_peak": null
}
```

`pids_peak`는 eBPF 결과와 독립적으로 cgroup에서 회수한다.
`error_flags`에는 사용자 Task Map, 프로세스 Map, 사용자 계보 카운터와 관련된
오류만 기록한다. 사용하지 않는 eBPF 컨테이너 카운터의 오류는 발생하지 않는다.

## 7. 대표 Task 선종료 처리 검증

다음 순서의 회귀 테스트를 추가한다.

1. 대표 Task가 작업 스레드 1개를 생성한다.
2. 대표 Task가 `pthread_exit()`으로 먼저 종료된다.
3. 남은 작업 스레드가 새로운 스레드 2개를 생성한다.
4. 사용자 Task Peak와 동일 시점의 프로세스·추가 스레드 수를 검증한다.

예상 결과는 다음과 같다.

```json
{
  "user_task_peak": 3,
  "process_at_user_task_peak": 1,
  "thread_at_user_task_peak": 2
}
```

모든 결과는 다음 관계를 만족해야 한다.

```text
user_task_peak
= process_at_user_task_peak + thread_at_user_task_peak
```

이 검증은 `pids_peak`와 `user_task_peak`의 시점 분리 정책과는 별개다.
`pids_peak`는 계속 cgroup 전체 생명주기의 Peak로 측정한다.
