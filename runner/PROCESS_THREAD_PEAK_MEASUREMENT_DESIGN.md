# Runner 프로세스·스레드 분리 Peak 측정 설계

## 1. 목적

Execution Container의 프로세스와 스레드를 별도로 보여주되, 제한은 기존처럼 Linux cgroup v2의 `pids.max`를 이용해 전체 Task 수를 합산하여 적용한다.

```text
정책 입력: pids_limit
    ↓
cgroup pids.max로 전체 Task 수 제한
    ↓
eBPF로 사용자 코드의 프로세스·스레드 생성/종료 추적
    ↓
process_peak · thread_peak · task_peak 분리 표시
```

프로세스와 스레드에 서로 다른 제한값을 두지 않는다. 따라서 기존 `pids_limit`과 `PIDS_LIMIT` 판정을 유지하며, 분리 측정값은 실행 특성을 설명하는 결과 지표로만 사용한다.

## 2. 용어와 계산 기준

Linux 커널은 프로세스와 스레드를 모두 Task로 관리한다. cgroup의 PIDs 컨트롤러도 둘을 구분하지 않고 합산한다.

| 항목 | 정의 |
| --- | --- |
| 프로세스 | 동일한 TGID를 공유하는 Task 그룹 |
| 메인 스레드 | `TID == TGID`인 Task |
| 추가 스레드 | `TID != TGID`인 Task |
| 전체 Task | 프로세스별 메인 스레드와 추가 스레드의 합계 |

실행 중 현재값은 다음 관계를 갖는다.

```text
current_task_count = current_process_count + current_additional_thread_count
```

반면 각 Peak는 서로 다른 시점에 발생할 수 있으므로 다음 식은 항상 성립하지 않는다.

```text
process_peak + thread_peak == task_peak  // 항상 성립하지 않음
```

예를 들어 프로세스 Peak가 먼저 발생하고 일부 프로세스가 종료된 뒤 스레드 Peak가 발생할 수 있다. 따라서 세 지표는 실행 중 각각 독립적으로 갱신한다.

## 3. 유지하는 제한 방식

외부 정책과 Docker 설정에는 기존 `pids_limit` 하나만 사용한다.

```text
사용자 정책 pids_limit
        ↓
Docker pids_limit
        ↓
cgroup v2 pids.max
```

`pids.max`는 Execution Container의 cgroup에 동시에 존재할 수 있는 프로세스와 스레드의 합계를 제한한다. 한도 도달 여부는 `pids.events`의 `max` 증가를 근거로 확인하고, 위반 사유는 기존처럼 `PIDS_LIMIT`으로 판정한다.

다음 항목은 도입하지 않는다.

- `process_limit`, `thread_limit` 별도 정책값
- BPF LSM을 이용한 프로세스·스레드별 생성 차단
- `PROCESS_LIMIT`, `THREAD_LIMIT` 별도 위반 사유
- 런타임 Task 여유분을 더한 별도 `docker_pids_limit` 계산

이 구조는 실제 제한을 커널 cgroup에 맡기므로 Runner의 사용자 공간 측정이 늦거나 실패해도 전체 Task 상한은 유지된다.

## 4. 분리 측정이 필요한 이유

현재 cgroup v2의 `pids.peak`는 프로세스와 스레드를 구분하지 않는다. 또한 Execution 전용 cgroup에는 사용자 프로그램뿐 아니라 컨테이너 시작 과정에서 잠시 생성되는 Task가 포함될 수 있다. 이 때문에 같은 단일 프로세스 코드를 반복해도 `pids_peak`가 5, 6, 7처럼 달라질 수 있다.

`/proc`, `cgroup.procs`, `cgroup.threads`를 일정 주기로 읽는 폴링 방식은 측정 사이에 생성됐다가 종료되는 짧은 Task를 놓칠 수 있다. 사용자 프로그램의 프로세스와 스레드 Peak를 분리해 정확히 표시하려면 생성·종료 이벤트를 기준으로 집계해야 한다.

## 5. 측정 범위

두 종류의 지표는 목적과 범위가 다르므로 구분해서 보관한다.

| 지표 | 측정 범위 | 용도 |
| --- | --- | --- |
| `pids_peak` | 컨테이너 시작 과정을 포함한 Execution 전용 cgroup 전체 | 기존 호환성, cgroup 진단 |
| `process_peak` | 사용자 실행파일과 그 자손 | 사용자 프로세스 특성 표시 |
| `thread_peak` | 사용자 실행파일과 그 자손의 추가 스레드 | 사용자 스레드 특성 표시 |
| `task_peak` | 사용자 실행파일과 그 자손의 전체 Task | 사용자 코드 기준 합산 Peak 표시 |

`pids_peak`와 `task_peak`는 서로 다른 범위를 측정하므로 값이 다를 수 있다. 제한 집행과 `PIDS_LIMIT` 판정에는 `pids.max`, `pids.events`, `pids_peak`를 사용하고, 프로세스·스레드 구분은 표시와 분석에만 사용한다.

## 6. eBPF 측정 구조

호스트의 eBPF Controller가 사용자 실행파일의 Root Task와 자손을 이벤트 단위로 추적한다. Execution Container 내부에는 BPF 권한을 부여하지 않는다. eBPF는 제한이나 종료를 수행하지 않고, 프로세스·스레드 측정값만 갱신한다.

```text
Runner
  ├─ Execution 전용 cgroup 생성
  │    └─ pids.max = pids_limit
  ├─ Execution Container 생성
  │    └─ codeguard-init이 시작 신호 대기
  ├─ eBPF Controller에 run_id · Root TID 등록
  ├─ 사용자 실행파일 시작
  ├─ fork/clone/exit 이벤트 추적
  ├─ process_peak · thread_peak · task_peak 회수
  ├─ cgroup의 pids_peak · pids.events 회수 및 결과 판정
  └─ Container · cgroup · 추적 상태 Cleanup
```

### 6.1 시작 시점

컨테이너 초기화 Task를 사용자 지표에서 제외하기 위해 최소 실행기인 `codeguard-init`을 사용한다.

1. Runner가 Execution Container를 생성한다.
2. `codeguard-init`은 사용자 코드를 즉시 실행하지 않고 시작 신호를 기다린다.
3. Runner가 컨테이너의 Root TID를 eBPF Controller에 `run_id`와 함께 등록한다.
4. 초기 상태를 프로세스 1개, 추가 스레드 0개, 전체 Task 1개로 설정한다.
5. 등록이 끝나면 `codeguard-init`이 같은 PID에서 사용자 실행파일로 `execve()`한다.
6. 이후 Root Task와 그 자손의 생성·종료 이벤트만 집계한다.

실행 제한 시간은 추적 등록을 마치고 사용자 실행파일을 시작하는 시점부터 계산한다.

### 6.2 집계할 값과 계산식

eBPF는 다음 세 개의 현재값을 실행별로 관리한다.

```text
process_current = 현재 생존한 TGID의 개수
task_current    = 현재 추적 중인 TID의 개수
thread_current  = task_current - process_current
```

Peak는 현재값이 증가할 때마다 다음과 같이 갱신한다.

```text
process_peak = max(process_peak, process_current)
thread_peak  = max(thread_peak, thread_current)
task_peak    = max(task_peak, task_current)
```

여기서 `thread_current`와 `thread_peak`는 각 프로세스의 메인 스레드를 제외한 추가 스레드 수다. 종료 순서에 따른 계산 오류를 줄이기 위해 종료 시 `thread_current`를 별도로 추정하지 않고 `task_current - process_current`로 다시 계산한다.

### 6.3 이벤트 처리

BTF/CO-RE 기반 eBPF 프로그램으로 다음 이벤트를 관찰한다.

- `sched_process_fork`: 새 Task를 추적 대상에 등록한다.
- `sched_process_exit`: 종료된 Task를 추적 대상에서 제거한다.

새 Task의 `TID`와 `TGID`를 확인하여 다음과 같이 분류한다.

```text
TID == TGID → 새 프로세스의 메인 스레드
TID != TGID → 기존 프로세스의 추가 스레드
```

단순히 `TID == TGID`만 확인하지 않고 TGID별 생존 Task 수를 참조 카운트로 관리한다. 메인 스레드가 다른 스레드보다 먼저 종료되는 경우에도 같은 TGID의 마지막 Task가 종료될 때까지 프로세스가 존재하는 것으로 계산하기 위해서다.

### 6.4 생성 이벤트 처리

`sched_process_fork` 이벤트가 발생하면 다음 순서로 처리한다.

1. 부모 TID가 `tracked_tasks`에 등록되어 있는지 확인한다.
2. 등록된 부모의 자식이면 자식 TID에 동일한 `run_id`를 기록한다.
3. 자식의 `TID == TGID`이면 새 프로세스로 분류하고 `process_current`를 1 증가시킨다.
4. 자식의 `TID != TGID`이면 기존 프로세스의 추가 스레드로 분류한다.
5. 모든 성공한 생성 이벤트에서 `task_current`를 1 증가시킨다.
6. `thread_current = task_current - process_current`를 계산한다.
7. 세 현재값으로 `process_peak`, `thread_peak`, `task_peak`를 갱신한다.

생성에 실패한 `fork`, `clone`, `clone3`, `pthread_create`는 성공한 `sched_process_fork` 이벤트가 발생하지 않으므로 Peak에 포함하지 않는다.

### 6.5 종료 이벤트 처리

`sched_process_exit` 이벤트가 발생하면 다음 순서로 처리한다.

1. 종료 TID가 `tracked_tasks`에 등록되어 있는지 확인한다.
2. 등록된 TID를 `tracked_tasks`에서 제거하고 해당 TGID의 생존 Task 참조 카운트를 1 감소시킨다.
3. `task_current`를 1 감소시킨다.
4. 해당 TGID의 생존 Task 수가 0이면 프로세스가 완전히 종료된 것이므로 `process_current`를 1 감소시킨다.
5. `thread_current = task_current - process_current`를 다시 계산한다.

따라서 메인 스레드가 먼저 종료되더라도 같은 TGID의 작업 스레드가 남아 있으면 프로세스 1개가 유지된다. 반대로 마지막 Task가 종료될 때만 프로세스 수를 감소시킨다.

### 6.6 순간적인 Peak 처리

생성 이벤트가 발생한 시점에 BPF Map의 현재값과 Peak를 커널에서 즉시 갱신한다. Runner가 일정 주기로 `/proc`나 cgroup 파일을 읽어 나중에 계산하지 않는다.

```text
스레드 31개 생성
  → fork 이벤트 31회 발생
  → thread_current가 1씩 증가
  → thread_peak=31 기록
  → Barrier 해제
  → 스레드가 즉시 종료
  → 현재값은 감소하지만 thread_peak=31은 유지
```

따라서 스레드가 Runner의 다음 폴링 시점 전에 모두 종료되어도 Peak를 회수할 수 있다. 최종 Peak의 근거는 Ring Buffer에서 전달된 이벤트를 사용자 공간에서 다시 합산한 값이 아니라, eBPF가 이벤트마다 갱신한 `run_metrics` Map이다.

### 6.7 BPF Map

| Map | Key | Value | 목적 |
| --- | --- | --- | --- |
| `tracked_roots` | Root TID | `run_id` | 사용자 코드 추적 시작점 |
| `tracked_tasks` | TID | `run_id`, TGID | 실행별 추적 대상 식별 |
| `process_tasks` | `run_id`, TGID | 생존 Task 수 | 프로세스 생존 여부와 추가 스레드 수 계산 |
| `run_metrics` | `run_id` | `process_current`, `thread_current`, `task_current`, 세 Peak | 프로세스·추가 스레드·전체 Task 집계 |

카운터와 Peak 갱신은 실행별 상태의 BPF Map 안에서 원자적으로 처리한다. 여러 Task가 동시에 생성·종료되는 경우에도 한 실행의 현재값과 Peak 갱신이 서로 덮어쓰이지 않도록 BPF spin lock 또는 동등한 원자적 갱신 방식을 사용한다. Ring Buffer는 필요할 경우 진단 이벤트 전달에만 사용하며 최종 Peak를 사용자 공간에서 다시 계산하는 근거로 사용하지 않는다.

## 7. API 변경

### 7.1 요청

기존 요청값을 유지한다.

```json
{
  "pids_limit": 128
}
```

`process_limit`과 `thread_limit`은 추가하지 않는다.

### 7.2 응답

기존 `pids_peak`를 유지하고 분리 측정값을 추가한다.

```json
{
  "pids_peak": 67,
  "process_peak": 1,
  "thread_peak": 63,
  "task_peak": 64,
  "pids_limit_exceeded": false
}
```

- `pids_peak`: Execution 전용 cgroup 전체의 Task Peak
- `process_peak`: 사용자 코드의 동시 프로세스 Peak
- `thread_peak`: 메인 스레드를 제외한 동시 추가 스레드 Peak
- `task_peak`: 사용자 코드의 동시 전체 Task Peak
- `pids_limit_exceeded`: cgroup의 합산 제한 도달 여부

분리 측정 기능을 지원하지 않거나 측정에 실패하면 `process_peak`, `thread_peak`, `task_peak`를 `null`로 반환하고 내부 진단 로그를 남긴다. 폴링으로 계산한 근삿값을 대신 반환하지 않는다. cgroup 제한은 eBPF 측정과 독립적으로 계속 적용한다.

## 8. Frontend 표시

정책 입력 화면에는 기존 `프로세스·스레드 합산 제한` 또는 `전체 Task 제한` 하나만 표시한다.

결과 화면은 다음처럼 구분한다.

| 표시 항목 | 값 | 설명 |
| --- | --- | --- |
| 전체 Task Peak | `task_peak` | 사용자 코드의 프로세스와 스레드 합산 Peak |
| 프로세스 Peak | `process_peak` | 동시에 존재한 사용자 프로세스의 최대 개수 |
| 추가 스레드 Peak | `thread_peak` | 메인 스레드를 제외한 추가 스레드의 최대 개수 |

기존 `pids_peak`는 cgroup 진단값이므로 상세 정보 또는 관리자 화면에 `cgroup Task Peak`로 표시한다. `process_peak + thread_peak`를 화면에서 자동 합산해 `task_peak`로 표시하지 않는다. 각 Peak의 발생 시점이 다를 수 있기 때문이다.

측정값이 `null`이면 `0`으로 표시하지 않고 `측정 불가`로 표시한다.

## 9. 오류 처리 원칙

- eBPF Controller가 준비되지 않아도 cgroup `pids.max`를 통한 안전 제한은 유지한다.
- 분리 측정 등록이나 회수에 실패하면 해당 지표를 `null`로 반환하고 오류 원인을 로그에 기록한다.
- 분리 측정 실패를 정상적인 `0`개로 처리하지 않는다.
- 최종 제한 위반 판정은 `pids.events`를 근거로 `PIDS_LIMIT` 하나만 사용한다.
- Cleanup에서는 `run_id`에 연결된 모든 BPF Map 항목을 제거한다.
- 컨테이너나 Runner가 비정상 종료되더라도 오래된 추적 상태를 제거할 수 있도록 만료 시각과 주기적 정리 절차를 둔다.

## 10. 검증 시나리오

| 시나리오 | 예상 결과 |
| --- | --- |
| 단일 스레드 프로그램 | `process_peak=1`, `thread_peak=0`, `task_peak=1` |
| 메인 1개와 작업 스레드 63개가 Barrier에서 대기 | `1`, `63`, `64` |
| 부모 1개가 자식 프로세스 31개를 동시에 유지 | `32`, `0`, `32` |
| 여러 프로세스가 각각 스레드를 생성 | 세 Peak를 각각 독립적으로 갱신 |
| 생성 직후 종료되는 Task 반복 | 폴링 간격과 관계없이 성공한 생성 이벤트 반영 |
| 메인 스레드가 작업 스레드보다 먼저 종료 | 같은 TGID의 마지막 Task 종료 전까지 프로세스 유지 |
| `fork` 또는 `pthread_create` 실패 | 생성되지 않은 Task는 Peak에 포함하지 않음 |
| `pids_limit=32`에서 전체 Task 생성 시도 | cgroup이 합산 제한하고 `PIDS_LIMIT` 판정 |
| 같은 코드를 여러 번 실행 | 사용자 코드 기준 세 Peak가 반복 실행마다 동일 |
| 컨테이너 시작 Task 수가 실행마다 변동 | `pids_peak`는 달라질 수 있으나 사용자 지표에는 미포함 |
| 여러 작업 동시 실행 | `run_id`별 측정값이 서로 섞이지 않음 |
| 분리 측정 실패 | 분리 지표는 `null`, cgroup 제한과 실행 결과는 유지 |

## 11. 완료 조건

- 정책 요청은 기존 `pids_limit` 하나만 사용한다.
- Docker와 cgroup의 합산 Task 제한 및 `PIDS_LIMIT` 판정을 유지한다.
- 사용자 실행파일과 자손만 대상으로 `process_peak`, `thread_peak`, `task_peak`를 이벤트 기반으로 측정한다.
- 수명이 짧은 프로세스와 스레드도 누락하지 않는다.
- 컨테이너 시작 과정의 Task는 사용자 분리 지표에서 제외한다.
- 분리 측정값을 제한 판정에 사용하지 않는다.
- 분리 측정 실패 시 근삿값이나 0으로 대체하지 않는다.
- 정상 종료, 제한 초과, 강제 종료, 내부 오류의 모든 경로에서 Container, 전용 cgroup, BPF Map 상태를 정리한다.

## 12. 참고 자료

- [Linux Control Group v2 문서](https://docs.kernel.org/admin-guide/cgroup-v2.html)
- [Linux `sched_process_fork` Tracepoint 정의](https://github.com/torvalds/linux/blob/master/include/trace/events/sched.h)
- [eBPF `bpf_get_current_pid_tgid` Helper](https://docs.ebpf.io/linux/helper-function/bpf_get_current_pid_tgid/)
- [eBPF `bpf_get_current_cgroup_id` Helper](https://docs.ebpf.io/linux/helper-function/bpf_get_current_cgroup_id/)
- [libbpf-bootstrap](https://github.com/libbpf/libbpf-bootstrap)
