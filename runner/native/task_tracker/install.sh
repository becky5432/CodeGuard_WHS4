#!/usr/bin/env bash
set -Eeuo pipefail

die() {
  printf '설치 중단: %s\n' "$*" >&2
  exit 1
}

trap 'printf "설치 실패 (줄 %s): %s\n" "$LINENO" "$BASH_COMMAND" >&2' ERR

# Check the host before changing any packages or service files.
[[ "$(uname -s)" == Linux ]] || die "Linux (Ubuntu) 호스트에서 실행해야 합니다."
[[ "$(uname -m)" == x86_64 ]] || die "현재 native tracker Makefile은 x86_64 호스트만 지원합니다."
command -v apt-get >/dev/null || die "apt-get을 사용할 수 있는 Ubuntu 호스트가 필요합니다."
command -v systemctl >/dev/null || die "systemd가 필요합니다."
command -v sudo >/dev/null || die "sudo가 필요합니다."
[[ -d /run/systemd/system ]] || die "systemd가 실행 중이지 않습니다."
[[ -r /sys/kernel/btf/vmlinux ]] || die "커널 BTF 파일이 없습니다: /sys/kernel/btf/vmlinux"
[[ -r /sys/fs/cgroup/cgroup.controllers ]] || die "cgroup v2를 사용할 수 없습니다."

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "$script_dir/../../.." && pwd -P)"
[[ -f "$repo_root/runner/requirements.txt" ]] || die "Runner requirements.txt가 없습니다."
[[ -f "$script_dir/Makefile" ]] || die "native tracker Makefile이 없습니다."
[[ -f "$script_dir/codeguard-task-tracker.service" ]] || die "systemd unit 파일이 없습니다."

install_user="${SUDO_USER:-$(id -un)}"
sudo -v

sudo apt-get update
sudo apt-get install -y \
  python3 python3-venv \
  clang llvm libbpf-dev linux-tools-common \
  "linux-tools-$(uname -r)" "linux-headers-$(uname -r)" \
  build-essential pkg-config

# Ubuntu 24.04 exposes bpftool as a virtual package. The common package
# supplies its wrapper, and the matching kernel tools supply the executable.
command -v bpftool >/dev/null || die "bpftool 명령을 찾을 수 없습니다."
bpftool version >/dev/null 2>&1 || die "bpftool을 실행할 수 없습니다. 현재 커널용 linux-tools 패키지를 확인하세요."

venv_dir="$repo_root/.venv-runner"
if [[ ! -d "$venv_dir" ]]; then
  python3 -m venv "$venv_dir"
fi
[[ -x "$venv_dir/bin/python" ]] || die "가상환경의 Python 실행 파일이 없습니다: $venv_dir/bin/python"
"$venv_dir/bin/python" -m pip install -r "$repo_root/runner/requirements.txt"

make -C "$script_dir"

sudo groupadd -f codeguard
sudo install -d -m 0755 /usr/local/libexec
sudo install -m 0755 \
  "$script_dir/codeguard-task-tracker" \
  /usr/local/libexec/codeguard-task-tracker
sudo install -m 0644 \
  "$script_dir/codeguard-task-tracker.service" \
  /etc/systemd/system/codeguard-task-tracker.service

if [[ "$install_user" != root ]]; then
  sudo usermod -aG codeguard "$install_user"
fi
sudo systemctl daemon-reload
sudo systemctl enable codeguard-task-tracker
sudo systemctl restart codeguard-task-tracker

# Type=simple can become active before the daemon creates its socket.
for ((attempt=0; attempt<100; attempt++)); do
  if [[ -S /run/codeguard/task-tracker.sock ]]; then
    break
  fi
  sudo systemctl is-active --quiet codeguard-task-tracker || die "task tracker 서비스가 종료되었습니다."
  sleep 0.1
done
[[ -S /run/codeguard/task-tracker.sock ]] || die "task tracker 소켓이 생성되지 않았습니다."
sudo systemctl is-active --quiet codeguard-task-tracker || die "task tracker 서비스가 실행 중이지 않습니다."

printf '\n설치 완료: codeguard-task-tracker 서비스와 소켓을 확인했습니다.\n'
printf 'Runner Python: %s/bin/python\n' "$venv_dir"
printf 'codeguard 그룹 변경을 적용하려면 로그아웃 후 다시 로그인하세요.\n'
printf '실행 이미지가 오래되었다면 저장소 루트에서 다음 명령으로 재빌드하세요:\n'
printf '  docker build -f runner/container/cpp/Dockerfile -t codeguard-cpp:dev .\n'
