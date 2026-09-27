# Runner 사용자 코드 시작 동기화 설계

## 목적

`strace`를 Execution Container의 PID 1로 유지하여 파일시스템 trace를 끝까지 수집하면서, Runner의 eBPF 추적 등록과 자원 측정 준비가 완료되기 전에 사용자 코드가 실행되지 않도록 한다.

## 현재 문제

- `codeguard-init`은 권한 검증 후 `execv()`로 즉시 사용자 프로그램을 실행한다.
- Runner는 컨테이너 시작 후 cgroup과 Root TID를 등록하므로, 등록 전에 발생한 짧은 프로세스·스레드 생성 이벤트가 누락될 수 있다.
- `container.kill(signal="SIGUSR1")`은 컨테이너 PID 1인 `strace`에 전달되므로 `codeguard-init`의 시작 승인 신호로 사용할 수 없다.
- CPU time 기준값과 wall time 시작 시점이 사용자 코드의 실제 시작보다 늦어질 수 있다.

## 선택한 방식

`SIGUSR1`을 제거하고 `/run/codeguard-trace/start.ready` 파일을 실행 게이트로 사용한다. 해당 경로는 기존 trace/evidence 익명 Volume 안에 있어 RootFS가 읽기 전용이어도 Runner가 파일을 생성할 수 있다.

Runner는 Docker SDK의 `put_archive()`로 시작 파일을 전달한다. 이 방식은 컨테이너 안에 추가 `exec` Task를 생성하지 않고, `strace`와 `codeguard-init` 중 어느 PID가 신호를 받아야 하는지 찾을 필요도 없다.

## 실행 순서

1. Runner가 Execution Container를 시작한다.
2. PID 1인 `strace`가 `codeguard-init`을 실행한다.
3. `codeguard-init`이 UID·GID·Capability·NoNewPrivs를 검증하고 `security.status`에 결과를 기록한다.
4. 권한 검증이 성공하면 `codeguard-init`은 `start.ready`가 생성될 때까지 유한 시간 대기한다.
5. Runner는 검증 성공 Evidence를 확인한다.
6. Runner는 컨테이너 PID 1의 직접 자식을 같은 cgroup인지 확인하고 `/proc/<tid>/exe`로 검증하여 대기 중인 `codeguard-init`의 호스트 TID를 찾는다. 다른 UID의 `exe` 읽기가 거부되면, 사용자 코드가 아직 시작되지 않았다는 게이트 조건 아래 `/proc/<tid>/cmdline`의 첫 인자를 검증한다.
7. Runner가 Execution cgroup ID와 `codeguard-init` TID를 native tracker에 등록한다.
8. Runner가 출력·PIDs·CPU 감시기를 준비하고 CPU time 기준값을 저장한다.
9. Runner가 wall time과 timeout 기준 시점을 저장한 뒤 `put_archive()`로 `start.ready`를 생성한다.
10. `codeguard-init`이 파일을 확인하고 `execv()`로 자신을 `/workspace/main`으로 교체한다. TID가 유지되므로 이후 생성되는 사용자 Task는 같은 eBPF 계보에서 추적된다.
11. 실행 종료 후 Runner가 cgroup 지표, eBPF Snapshot, trace 로그를 회수한다.

## 컴포넌트 변경

### `codeguard-init`

- 권한 검증 성공 후 `start.ready`를 대기한다.
- 대기는 busy loop 대신 짧은 sleep을 사용하고, 고정된 내부 준비 timeout을 넘기면 코드 126으로 종료한다.
- 시작 파일은 일반 파일이어야 하며 예상 경로 외의 입력은 받지 않는다.

### Runner

- `container.kill(signal="SIGUSR1")`을 제거한다.
- `strace` PID를 Root TID로 등록하지 않고, 대기 중인 `codeguard-init`의 호스트 TID를 확인해 등록한다.
- 추적 등록과 감시기 준비 전에는 `start.ready`를 생성하지 않는다.
- 파일 생성에 실패하면 사용자 코드를 실행하지 않고 Execution 내부 오류로 처리한다.

### eBPF/native tracker

- 기존 cgroup 필터와 Root TID 계보 추적 방식을 유지한다.
- Root TID가 `strace`가 아닌 `codeguard-init`으로 전달되는지 검증한다.
- 측정 실패는 사용자 코드 판정과 분리하되, 측정값은 `null`로 반환하고 경고 로그를 남긴다. 단, 시작 파일 생성 실패는 실행 자체를 시작할 수 없으므로 내부 오류로 처리한다.

## 실패 처리

| 상황 | 처리 |
| --- | --- |
| 권한 검증 실패 | `codeguard-init`이 사용자 코드를 실행하지 않고 종료 |
| Evidence 확인 실패 | Runner가 컨테이너를 종료하고 내부 오류로 판정 |
| `codeguard-init` TID 탐색 실패 | eBPF 측정값을 `null`로 처리하고 실행은 계속 |
| native tracker 등록 실패 | 측정 경고를 남기고 `start.ready`를 생성해 실행은 계속 |
| `start.ready` 생성 실패 | 컨테이너를 종료하고 내부 오류로 판정 |
| Runner 중단·통지 누락 | `codeguard-init` 내부 준비 timeout 후 종료 |

## 보안 및 정합성

- `start.ready`는 작업별 익명 Volume에 생성되므로 다른 실행의 오래된 파일을 재사용하지 않는다.
- evidence Volume의 디렉터리는 `0711`로 설정하여 비루트 `codeguard-init`에 파일 탐색만 허용한다. `security.status`는 root 소유 `0600`으로 유지하고, 사용자 코드는 디렉터리 목록 조회·파일 생성·증거 읽기를 할 수 없다.
- 사용자 프로그램은 시작 파일이 생성된 후에만 실행되므로 게이트를 스스로 열 수 없다.
- `put_archive()`는 호스트 Docker API를 통해 수행하며, 컨테이너 안에 별도의 프로세스를 만들지 않는다.
- 시작 파일 경로는 상수로 고정하고 외부 입력을 경로에 삽입하지 않는다.

## 테스트

1. `codeguard-init`이 `start.ready` 전에 `/workspace/main`을 실행하지 않는다.
2. Runner가 파일을 생성하면 동일 TID가 `execv()` 후 사용자 프로그램으로 유지된다.
3. 실행 즉시 종료하는 단일 Task 코드를 반복해도 `user_task_peak=1`, 프로세스 1, 추가 스레드 0이 안정적으로 반환된다.
4. 실행 즉시 `fork()`하는 코드와 `pthread_create()`하는 코드의 최초 생성 이벤트가 누락되지 않는다.
5. 권한 검증 실패 시 사용자 코드가 실행되지 않는다.
6. `strace` trace 끝의 정상 종료 기록이 존재하고 정상 코드가 `FILESYSTEM_LIMIT`으로 오판되지 않는다.
7. timeout·CPU time·PIDs·출력 제한이 시작 파일 생성 시점부터 정상 적용된다.
8. Runner가 시작 파일을 생성하지 않으면 `codeguard-init`이 준비 timeout 후 종료한다.

## 제외 범위

- Backend·Frontend API 변경
- seccomp 프로파일 변경
- eBPF 측정 필드 추가
- `strace` 기반 파일시스템 판정 규칙 변경
