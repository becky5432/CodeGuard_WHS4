# CodeGuard_WHS4

## eBPF 프로세스·스레드 측정기 설치

이 기능은 Linux cgroup v2와 BTF를 사용한다. Runner의 기존
`pids.max`, `pids.peak`, `pids.events` 제한은 그대로 유지하며,
`codeguard-task-tracker`는 cgroup 전체 Peak 시점의 사용자 프로세스 수와
추가 스레드 수를 분석 지표로 제공한다.

### Ubuntu 의존성

```bash
sudo apt update
sudo apt install -y \
  clang llvm libbpf-dev bpftool \
  linux-headers-$(uname -r) build-essential pkg-config
```

다음 파일이 있어야 CO-RE 프로그램을 빌드할 수 있다.

```bash
test -r /sys/kernel/btf/vmlinux
test -r /sys/fs/cgroup/cgroup.controllers
```

### 빌드 및 서비스 설치

```bash
make -C runner/native/task_tracker

sudo groupadd -f codeguard
sudo install -d -m 0755 /usr/local/libexec
sudo install -m 0755 \
  runner/native/task_tracker/codeguard-task-tracker \
  /usr/local/libexec/codeguard-task-tracker
sudo install -m 0644 \
  runner/native/task_tracker/codeguard-task-tracker.service \
  /etc/systemd/system/codeguard-task-tracker.service

sudo usermod -aG codeguard "$USER"
sudo systemctl daemon-reload
sudo systemctl enable --now codeguard-task-tracker
```

서비스는 `CAP_BPF`, `CAP_PERFMON`만 사용한다. 대상 커널에서 tracepoint
attach가 권한 오류로 실패할 때만 원인을 확인한 뒤 systemd unit에
`CAP_SYS_ADMIN`을 추가한다.

그룹 변경을 반영하려면 다시 로그인한 뒤 상태를 확인한다.

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
`process_at_pids_peak`와 `thread_at_pids_peak`는 `null`로 반환된다.
