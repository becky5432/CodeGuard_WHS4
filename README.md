# CodeGuard_WHS4

## 파일 경로 접근 제어

준비·컴파일·실행은 실행별 단일 RW `/workspace` 볼륨을 공유한다. 실행 시
Landlock 정책 v2가 workspace 내부의 읽기·변경·파일 실행을 허용하고,
외부는 검증된 런타임 파일과 서버가 선택한 개별 장치만 허용한다.
Landlock ABI7 이상이 필요하며, 새 정책을 사용할 때 실행 이미지도 다시 빌드한다.
장치 선택 설정·허용 권한·검증 명령은
[`runner/security/FILESYSTEM_POLICY.md`](runner/security/FILESYSTEM_POLICY.md)를 참고한다.

## eBPF 프로세스·스레드 측정기 설치

이 기능은 Linux cgroup v2와 BTF를 사용한다. Runner의 기존
`pids.max`, `pids.peak`, `pids.events` 제한은 그대로 유지하며,
`codeguard-task-tracker`는 사용자 코드 계보의 Task를 별도로 추적한다.

- `pids_peak`: Execution cgroup 전체 생명주기의 Task 최대값
- `user_task_peak`: 사용자 코드 계보의 Task 최대값
- `process_at_user_task_peak`, `thread_at_user_task_peak`:
  `user_task_peak`가 발생한 동일 시점의 프로세스 수와 추가 스레드 수

`pids_peak`와 `user_task_peak`는 측정 범위가 다르므로 최대값이 발생한
시점도 서로 다를 수 있다. 자세한 측정 기준은
[`runner/native/task_tracker/README.md`](runner/native/task_tracker/README.md)를
참고한다.

### Ubuntu 호스트 설치

`apt`와 `systemd`를 사용하는 x86_64 Ubuntu Linux 호스트에서, 저장소 루트 기준으로 실행한다.
Windows나 실행 컨테이너 내부에서는 실행하지 않는다. 설치 스크립트가
cgroup v2·BTF를 확인하고, 시스템 패키지, `.venv-runner`의 Python 패키지,
native tracker 바이너리 및 systemd 서비스를 설치한다.

```bash
bash runner/native/task_tracker/install.sh
```

기존 설치를 업데이트할 때도 같은 명령을 재실행하면 빌드·설치 후 서비스를
재시작한다. 설치 스크립트는 방화벽 규칙이나 Runner 네트워크 설정을 변경하지
않는다. Python 패키지 목록은 `runner/requirements.txt`에만 유지한다.

서비스는 `CAP_BPF`, `CAP_PERFMON`만 사용한다. 대상 커널에서 tracepoint
attach가 권한 오류로 실패할 때만 원인을 확인한 뒤 systemd unit에
`CAP_SYS_ADMIN`을 추가한다.

처음 설치했다면 `codeguard` 그룹 변경을 반영하기 위해 다시 로그인한다.
그 후 서비스 상태와 소켓을 확인한다.

```bash
systemctl status codeguard-task-tracker --no-pager
ls -l /run/codeguard/task-tracker.sock
```

### 실행 이미지 재빌드

```bash
docker build -f runner/container/cpp/Dockerfile -t codeguard-cpp:dev .
docker run --rm codeguard-cpp:dev \
  test -x /usr/local/bin/codeguard-init
```

### 검증

```bash
python3 -m unittest discover -s runner/tests -v

CODEGUARD_EBPF_INTEGRATION=1 \
python3 -m unittest \
  runner.tests.integration.test_task_tracker_integration -v
```

Tracker가 비활성화되거나 측정에 실패하면 실행 자체는 계속되며,
`user_task_peak`, `process_at_user_task_peak`,
`thread_at_user_task_peak`는 `null`로 반환된다.
