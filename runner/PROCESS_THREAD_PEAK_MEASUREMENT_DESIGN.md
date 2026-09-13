# Runner 프로세스·스레드 Peak 정확 측정 설계

## 1. 목적

Execution Container에서 사용자 코드가 실행되는 동안 다음 세 가지 최대값을 서로 구분하여 정확하게 측정한다.

| 지표 | 의미 |
| --- | --- |
| `process_peak` | 동시에 존재한 사용자 프로세스(TGID)의 최대 개수 |
| `thread_peak` | 각 프로세스의 메인 스레드를 제외한 추가 스레드의 최대 개수 |
| `task_peak` | 동시에 존재한 전체 사용자 Task의 최대 개수. 프로세스와 추가 스레드의 합계 |

예를 들어 하나의 프로세스가 메인 스레드 1개와 작업 스레드 63개를 실행하면 측정값은 `process_peak=1`, `thread_peak=63`, `task_peak=64`가 된다.

각 Peak는 실행 중 서로 다른 시점에 발생할 수 있다. 따라서 최종 결과에서 `process_peak + thread_peak`가 반드시 `task_peak`와 같지는 않다.

## 2. 현재 측정 방식의 한계

Linux cgroup v2의 `pids.current`와 `pids.peak`는 프로세스와 스레드를 구분하지 않고 모두 Task로 계산한다. 따라서 현재의 `pids_peak`만으로는 최대 프로세스 수와 최대 추가 스레드 수를 각각 알 수 없다.

또한 실행 컨테이너의 전용 상위 cgroup에는 사용자 프로그램뿐 아니라 컨테이너 초기화 과정에서 잠시 생성되는 Task가 포함될 수 있다. 같은 사용자 코드를 반복 실행해도 런타임의 시작·종료 순서에 따라 `pids_peak`가 5, 6, 7처럼 달라질 수 있는 이유다.

`/proc`, `cgroup.procs`, `cgroup.threads`를 일정 주기로 읽는 방식도 매우 짧게 생성됐다가 종료되는 프로세스와 스레드를 놓칠 수 있다. 정확한 Peak가 요구되므로 폴링 결과를 최종 지표로 사용하지 않는다.

## 3. 설계 결정

호스트에서 eBPF를 이용해 사용자 프로그램 계보에 속한 Task의 생성과 종료를 이벤트 단위로 추적한다. 컨테이너 런타임이 만드는 일시적인 Task는 제외하고, 사용자 실행파일과 그 자손만 집계한다.

`ptrace`도 프로세스 생성과 종료를 추적할 수 있지만 추적 대상의 실행을 중단시키며 Wall Time을 왜곡할 수 있다. 또한 사용자 코드의 `ptrace` 사용을 제한하는 보안 정책과 충돌하고 운영 복잡도가 크므로 채택하지 않는다.

## 4. 전체 구조

```text
Runner
  ├─ Execution 전용 cgroup 생성
  ├─ Execution Container 생성
  │    └─ codeguard-init 실행 및 시작 신호 대기
  ├─ eBPF Collector에 run_id와 사용자 Root PID 등록
  ├─ codeguard-init에 시작 신호 전달
  │    └─ 같은 PID에서 /workspace/main으로 execve
  ├─ 사용자 프로세스·스레드 생성/종료 추적
  ├─ process_peak · thread_peak · task_peak 회수
  ├─ 기존 cgroup 지표와 함께 결과 판정
  └─ Container · cgroup · 추적 상태 Cleanup
```

eBPF Collector는 Runner 호스트에서 한 번 로드하여 여러 실행 작업이 공유한다. Execution Container 내부에는 BPF 관련 권한을 부여하지 않는다.

## 5. 사용자 코드 추적 시작점

컨테이너 시작 과정의 Task를 사용자 지표에서 제외하기 위해 신뢰할 수 있는 최소 실행기인 `codeguard-init`을 사용한다.

1. Runner가 실행별 전용 cgroup과 Execution Container를 생성한다.
2. 컨테이너의 최초 프로세스인 `codeguard-init`은 사용자 코드를 바로 실행하지 않고 시작 신호를 기다린다.
3. Runner가 컨테이너의 호스트 PID를 확인한다.
4. Runner가 eBPF Collector에 `run_id`와 Root PID를 등록한다.
5. 등록이 성공하면 Runner가 Docker API를 통해 `SIGUSR1` 시작 신호를 보낸다.
6. `codeguard-init`은 필요한 경우 `/workspace/stdin`을 연 뒤, 별도 자식 프로세스를 만들지 않고 같은 PID에서 `/workspace/main`을 `execve()`한다.
7. eBPF Collector는 등록된 Root PID와 그 자손만 해당 실행의 사용자 Task로 집계한다.

실행 제한 시간은 Collector 등록이 완료되고 시작 신호를 전달하는 시점부터 계산한다. 준비 과정은 사용자 코드의 Wall Time에 포함하지 않는다.

## 6. eBPF 추적 방식

### 6.1 관찰 이벤트

BTF 기반 tracepoint를 사용하여 다음 이벤트를 관찰한다.

- `sched_process_fork`: 추적 중인 Task가 프로세스 또는 스레드를 생성했는지 확인한다.
- `sched_process_exit`: 추적 중인 Task가 종료될 때 현재 개수를 감소시킨다.

생성에 실패한 `fork`, `clone`, `pthread_create`는 `sched_process_fork` 이벤트가 발생하지 않으므로 Peak에 포함되지 않는다.

### 6.2 BPF Map

| Map | Key | Value | 목적 |
| --- | --- | --- | --- |
| `tracked_roots` | Root TID | `run_id` | 실행별 사용자 코드 시작점 등록 |
| `tracked_tasks` | TID | `run_id`, TGID | 추적 중인 모든 사용자 Task 식별 |
| `process_threads` | `run_id`, TGID | 현재 Task 수 | 프로세스별 생존 스레드 수 계산 |
| `run_metrics` | `run_id` | 현재값과 Peak | 프로세스·추가 스레드·전체 Task 집계 |

Map 크기는 동시에 실행 가능한 작업 수와 작업별 `pids.max`를 기준으로 제한한다. 무제한 Map은 사용하지 않는다.

### 6.3 생성 이벤트 처리

1. 부모 TID가 `tracked_tasks`에 등록되어 있는지 확인한다.
2. 추적 중인 부모라면 자식 TID에 동일한 `run_id`를 상속한다.
3. 자식 TGID가 `process_threads`에 없으면 새 프로세스로 계산한다.
4. 이미 같은 TGID가 존재하면 기존 프로세스에 추가된 스레드로 계산한다.
5. 현재 프로세스 수, 추가 스레드 수, 전체 Task 수를 갱신한다.
6. 각각의 현재값이 기존 Peak보다 크면 해당 Peak를 갱신한다.

### 6.4 종료 이벤트 처리

1. 종료 TID의 `run_id`와 TGID를 확인한다.
2. 해당 프로세스의 현재 Task 수를 1 감소시킨다.
3. 같은 TGID에 생존한 Task가 남아 있으면 프로세스 수는 유지한다.
4. 마지막 Task가 종료된 경우에만 현재 프로세스 수를 1 감소시킨다.
5. 종료 TID를 `tracked_tasks`에서 제거한다.

Linux에서는 프로세스의 메인 스레드가 다른 작업 스레드보다 먼저 종료될 수 있다. TGID에 속한 마지막 Task가 종료될 때 프로세스를 제거해야 이 경우에도 프로세스 수를 정확하게 유지할 수 있다.

### 6.5 동시성 처리

현재값과 Peak는 사용자 공간으로 전달된 이벤트를 다시 계산하지 않고 BPF Map 안에서 갱신한다. Ring Buffer 이벤트가 유실되더라도 최종 Peak가 훼손되지 않도록, 실행별 카운터 갱신에는 BPF spin lock 또는 동등한 원자적 갱신 방식을 사용한다.

## 7. 기존 cgroup 측정과의 관계

eBPF 지표가 기존 전용 cgroup을 대체하지는 않는다.

| 지표·기능 | 담당 | 용도 |
| --- | --- | --- |
| `pids.max` | cgroup v2 | 프로세스와 스레드를 합한 전체 Task 수를 커널에서 강제 제한 |
| `pids.peak` | cgroup v2 | 컨테이너 시작 과정을 포함한 전용 cgroup의 전체 Task Peak 진단 |
| `pids.events` | cgroup v2 | `pids.max` 도달 여부 확인 |
| `memory.peak`, `memory.events` | cgroup v2 | 메모리 Peak와 제한 이벤트 확인 |
| `process_peak` | eBPF Collector | 사용자 코드 계보의 최대 프로세스 수 표시 |
| `thread_peak` | eBPF Collector | 사용자 코드 계보의 최대 추가 스레드 수 표시 |
| `task_peak` | eBPF Collector | 사용자 코드 계보의 최대 전체 Task 수 표시 |

`pids_peak`와 `task_peak`가 다를 수 있다. `pids_peak`에는 컨테이너 초기화 과정이 포함될 수 있지만 `task_peak`는 등록된 사용자 Root PID와 자손만 포함하기 때문이다.

이번 변경에서는 프로세스 수와 스레드 수를 별도로 강제 제한하지 않는다. 정책의 `pids_limit`은 기존처럼 전체 Task 수를 제한한다. 별도 제한을 강제하려면 eBPF LSM 또는 다른 커널 수준 집행 방식과 생성 경쟁 조건에 대한 별도 설계가 필요하다.

## 8. 결과 모델 변경

Runner와 Backend의 실행 결과 모델에 다음 필드를 추가한다.

```json
{
  "process_peak": 1,
  "thread_peak": 63,
  "task_peak": 64,
  "pids_peak": 67
}
```

- `process_peak`, `thread_peak`, `task_peak`는 사용자 프로그램 기준 지표다.
- `pids_peak`는 cgroup 전체 기준 진단 지표로 유지한다.
- 기존 API 호환성을 위해 `pids_peak`를 즉시 제거하지 않는다.
- 마이그레이션 기간에는 신규 필드를 nullable로 추가하되, Collector가 정상인 실행에서는 반드시 정수값을 반환한다.
- Frontend에서는 `PIDs Peak` 대신 `프로세스 Peak`, `추가 스레드 Peak`, `전체 Task Peak`를 구분하여 표시한다.

## 9. 오류 처리 원칙

정확한 측정을 요구하므로 `/proc` 또는 cgroup 파일 폴링으로 조용히 대체하지 않는다.

- Runner 시작 시 BPF 프로그램 로드, tracepoint 연결, Map 접근을 검사한다.
- Collector가 준비되지 않으면 Runner의 readiness를 실패 처리하여 새 실행을 받지 않는다.
- 작업 도중 등록 또는 지표 회수에 실패하면 컨테이너와 cgroup을 정리한 뒤 `INTERNAL_ERROR`로 처리한다.
- 자원 보호는 기존 cgroup 제한이 계속 담당하므로 Collector 장애가 호스트의 자원 제한 해제로 이어지지 않는다.
- Cleanup에서는 `run_id`에 연결된 모든 BPF Map 항목을 제거한다.

## 10. 보안 및 운영 조건

- Execution Container는 기존처럼 non-root, `cap_drop=["ALL"]`, `no-new-privileges=true`를 유지한다.
- BPF 권한은 호스트의 Collector에만 부여한다.
- 커널 버전에 따라 `CAP_BPF`, `CAP_PERFMON` 또는 root 권한이 필요할 수 있으므로 배포 환경에서 최소 권한을 확인한다.
- Linux cgroup v2와 BTF/CO-RE를 지원하는 커널을 전제한다.
- 사용자 입력으로 BPF 프로그램이나 Map Key를 직접 구성하지 않는다.
- `run_id`와 Root PID를 함께 관리하여 PID 재사용으로 다른 실행이 잘못 집계되지 않도록 한다.
- Collector는 작업마다 새로 로드하지 않고 Runner 시작 시 한 번 로드한다.

## 11. 검증 시나리오

| 시나리오 | 예상 결과 |
| --- | --- |
| 단일 스레드 프로그램 | `process_peak=1`, `thread_peak=0`, `task_peak=1` |
| 메인 1개와 작업 스레드 63개가 Barrier에서 대기 | `1`, `63`, `64` |
| 부모 1개가 자식 프로세스 31개를 동시에 유지 | `32`, `0`, `32` |
| 여러 프로세스가 각각 스레드를 생성 | 프로세스와 추가 스레드 Peak를 독립적으로 정확히 계산 |
| 생성 직후 바로 종료되는 Task 반복 | 폴링 간격과 관계없이 모든 성공한 생성 이벤트 반영 |
| 메인 스레드가 작업 스레드보다 먼저 종료 | 마지막 Task 종료 전까지 프로세스 1개 유지 |
| `fork` 또는 `pthread_create` 실패 | 실패한 생성 시도는 집계하지 않음 |
| 시간 초과 후 컨테이너 강제 종료 | 종료 전까지의 Peak 회수 후 `TIME_LIMIT` 판정 |
| `pids.max` 초과 | cgroup 이벤트를 근거로 기존 `PIDS_LIMIT` 판정 유지 |
| 여러 작업 동시 실행 | `run_id`별 지표가 서로 섞이지 않음 |
| Collector 등록 전 컨테이너 런타임 Task 생성 | 사용자 Peak에서 제외 |
| Collector 등록 또는 시작 신호 전달 실패 | 사용자 코드 미실행, 자원 Cleanup, `INTERNAL_ERROR` |

## 12. 완료 조건

- 같은 테스트 프로그램을 반복 실행해 사용자 기준 세 Peak가 동일하게 측정된다.
- 수명이 매우 짧은 프로세스와 스레드도 누락되지 않는다.
- 컨테이너 시작 과정의 Task가 사용자 지표에 포함되지 않는다.
- `pids.max`, `pids.events`, `memory.peak` 등 기존 cgroup 제한과 판정이 유지된다.
- Collector 장애 시 부정확한 값으로 대체하지 않고 명시적으로 실패한다.
- 정상 종료, 제한 초과, 강제 종료, 내부 오류의 모든 경로에서 Container, 전용 cgroup, BPF Map 상태가 정리된다.

## 13. 참고 자료

- [Linux Control Group v2 문서](https://cdn.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html)
- [Linux ptrace(2) 매뉴얼](https://www.man7.org/linux/man-pages/man2/ptrace.2.html)
- [eBPF `bpf_get_current_cgroup_id` Helper](https://docs.ebpf.io/linux/helper-function/bpf_get_current_cgroup_id/)
- [eBPF `bpf_get_current_ancestor_cgroup_id` Helper](https://docs.ebpf.io/linux/helper-function/bpf_get_current_ancestor_cgroup_id/)
