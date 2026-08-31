# Runner cgroup 전용 자원 측정 설계

## 목적

Execution 단계의 Memory Peak와 PIDs Peak를 Linux cgroup v2가 기록한 값으로만 판정한다. Docker Stats polling과 자동 fallback을 제거하여 응답값의 측정 출처를 단일화한다.

## 지원 조건

- Runner는 Linux cgroup v2 환경을 필수로 요구한다.
- Docker cgroup 드라이버는 `cgroupfs` 또는 `systemd`여야 한다.
- 실행별 상위 cgroup에서 `memory.peak`, `pids.peak`, `memory.events`, `pids.events`를 읽을 수 있어야 한다.
- 조건을 충족하지 못하면 해당 Job은 `ERROR / INTERNAL_ERROR`로 처리한다.

## 실행 흐름

1. 컴파일 성공 후 Docker cgroup 드라이버를 확인한다.
2. `ExecutionCgroupScope`로 실행별 상위 cgroup을 반드시 생성한다.
3. 상위 cgroup을 Execution Container의 `cgroup_parent`로 전달한다.
4. 사용자 프로그램을 실행하고 시간·출력·PIDs 제한을 감시한다.
5. 프로그램 종료 후 상위 cgroup에서 Peak와 제한 이벤트를 회수한다.
6. 회수된 cgroup 값만 `ExecutionResult`에 기록한다.
7. 성공 여부와 관계없이 Container, Volume, 실행별 cgroup을 정리한다.

## 설정 변경

`execution_cgroup_enabled` 설정과 실행 조건문을 제거한다. `execution_cgroup_root`는 경로 변경이 필요한 환경을 위해 유지하며 기본값은 `/sys/fs/cgroup/codeguard`로 둔다.

## 측정 변경

- `ResourceMonitor`와 Docker Stats 호출을 제거한다.
- `memory_peak_bytes`는 `memory.peak` 값만 사용한다.
- `pids_peak`는 실행별 상위 cgroup의 `pids.peak` 값만 사용한다.
- `PidsLimitMonitor`는 실행 중 PIDs 제한 도달을 빠르게 감지하는 역할로 유지하되 최종 Peak fallback으로 사용하지 않는다.
- Docker의 `mem_limit`, `memswap_limit`, `pids_limit`, `nano_cpus` 제한 옵션은 그대로 유지한다.

## 오류 처리

다음 중 하나가 발생하면 `CgroupScopeError`를 발생시킨다.

- 실행별 cgroup 생성 실패
- `memory.peak` 또는 `pids.peak` 읽기·정수 변환 실패
- `memory.events` 또는 `pids.events` 읽기·파싱 실패

오류는 `execute_job()`의 기존 `RunnerError` 처리 흐름에서 `ERROR / INTERNAL_ERROR`로 변환한다. 오류가 실행 종료 후 발생해도 성공 응답으로 낮추지 않으며, `finally` Cleanup은 계속 수행한다.

## API 영향

정상 응답 스키마는 변경하지 않는다. 측정 성공 시 기존 `resource_usage.memory_peak_bytes`와 `resource_usage.pids_peak`에 cgroup 값을 반환한다. 측정에 실패하면 Job 자체가 `ERROR / INTERNAL_ERROR`가 되므로 근삿값이나 출처가 다른 값을 반환하지 않는다.

## 코드 정리

더 이상 참조되지 않는 다음 파일을 삭제한다.

- `runner/metrics/resource_monitor.py`
- `runner/tests/test_resource_monitor.py`

## 테스트

- 활성화 환경변수 없이도 모든 Execution Job이 cgroup을 생성하는지 검증한다.
- Execution Container에 `cgroup_parent`가 항상 전달되는지 검증한다.
- cgroup Peak와 events가 결과·판정에 반영되는지 검증한다.
- 각 필수 cgroup 파일의 누락·읽기 실패·잘못된 형식이 `INTERNAL_ERROR`가 되는지 검증한다.
- cgroup 생성 및 측정 실패 후에도 Container, Volume, cgroup Cleanup이 수행되는지 검증한다.
- Docker Stats API가 실행 경로에서 호출되지 않음을 코드와 테스트로 검증한다.
- Runner 전체 단위 테스트와 `git diff --check`를 통과해야 한다.

## 범위 밖

- CPU time 측정 구현
- API에 측정 출처 필드 추가
- systemd slice 수명주기 방식의 별도 재설계
- Compile Container 자원 Peak 측정
