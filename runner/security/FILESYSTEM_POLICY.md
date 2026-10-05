# 경로별 파일시스템 정책

## 적용 범위

Runner가 선택한 Linux amd64 이미지의 C/C++ 제출 프로그램에 적용한다. 실행 RootFS는 RO이며, Job마다 독립적인 app/input/work Volume을 만든다. 컴파일 컨테이너는 app Volume을 `/workspace` RW로 사용하고, 종료 후 실행 컨테이너가 같은 Volume을 `/workspace/app` RO로 사용한다. 재컴파일하거나 실행 파일을 복사하지 않는다.

| 대상 | 실행 단계의 허용 작업 |
| --- | --- |
| `/workspace/app/main` | 읽기·파일 실행 |
| 실제 제공한 소스 및 `/workspace/input/stdin` | 읽기 |
| `/workspace/app`, `/workspace/input` | 디렉터리 목록 조회 |
| `/workspace/work` 하위 | 일반 파일·디렉터리 생성·읽기·쓰기·수정·삭제·내부 이름 변경·이동 |
| 검증된 동적 로더 | 읽기·파일 실행 |
| 이미지 프로파일에 등록한 런타임·캐시 | 읽기·로딩 |
| `/proc`, `/sys`, `/dev`(포함 `/dev/null`), 내부 운영 경로, 기타 외부 경로 | 위 정책이 처리하는 파일 작업 기본 거부 |

파일 작업의 기본 상대경로는 `/workspace/work`이다. 입력이 없어도 빈 `stdin` 파일을 만들고 FD 0에 연결한다. 기존 FD 1·2를 통한 출력은 유지한다.

### 작업 영역 소유권 유지

준비 helper가 세 Volume의 루트 소유자를 `10001:10001`로 설정한다. app/input은 `0755`, work는 `0700`이다. 실행 단계의 app/input RO와 work RW 및 Landlock 규칙은 그대로 유지한다. RW 마운트나 Landlock 허용은 Linux 파일 권한을 추가로 부여하지 않는다.

준비·실행 단계의 세 Job Volume 마운트에 Docker SDK `no_copy=True` (`VolumeOptions.NoCopy`)를 지정한다. work는 준비 후에도 비어 있으므로, 재연결 시 기본 이미지 초기 복사가 준비한 Volume 루트의 소유권·모드에 개입하지 않도록 한다. 익명 내부 기록 Volume의 기존 초기화와 컴파일 단계의 app Volume 경로는 변경하지 않는다. [Docker Volume 초기 복사와 volume-nocopy](https://docs.docker.com/engine/storage/volumes/)

VM에서 확인한 실패는 사용자 `10001:10001`이 작업 영역을 `root:root / 0755`로 보아 `fopen`이 `Permission denied`를 반환한 사례다. 이 변경의 실제 해결 여부는 같은 VM에서 아래 회귀 테스트로 확인해야 한다. 호스트에 `/workspace`를 만들거나 `chmod 777`·사용자 root 실행으로 우회하지 않는다.

```bash
# 업데이트한 PR 브랜치 및 프로젝트 가상환경에서 실행
EXECUTION_NETWORK=none CPP_IMAGE=codeguard-cpp:dev RUNNER_DOCKER_TESTS=1 \
  python -m pytest \
  runner/tests/test_filesystem_integration.py::FilesystemIntegrationTests::test_relative_work_file_and_directory_lifecycle \
  runner/tests/test_filesystem_integration.py::FilesystemIntegrationTests::test_cross_boundary_rename_and_hardlink_are_denied \
  -vv -s -x
```

작업 영역 회귀 테스트는 실행 사용자와 디렉터리 소유자가 `10001:10001`, 모드가 `0700`인지 확인한 뒤 파일 생성·읽기·수정·삭제·내부 이름 변경을 검증한다. 생성 실패 시 실제 오류를 출력한다. 통합 테스트의 네트워크 구성 검사는 Runner 설정값과 비교하며, 위 명령은 테스트 프로세스에서만 `none`을 지정한다.

## 이미지와 정책 준비

이미지 빌드 스크립트 `runner/container/cpp/build-filesystem-manifest.py`가 고정된 신뢰 C/C++ 예제의 동적 로더·런타임 의존성을 확인하고 `/usr/local/share/codeguard/filesystem-runtime.json`을 생성한다. 사용자 제출 실행 파일에 `ldd`를 실행하지 않는다. 런타임 디렉터리 전체를 허용하지 않는다.

Runner는 설정된 이미지 태그를 Job 시작 시 불변 이미지 ID로 한 번 해석한다. 준비·컴파일·실행에 같은 ID를 사용한다. 준비 helper에서 읽은 보호된 manifest를 해당 ID에 결합하고, 실제 제공한 소스·입력 및 작업 영역 규칙과 함께 TSV 정책을 만든다. 임의 경로·프로파일을 사용자 요청에 추가하지 않는다.

정책은 `/run/codeguard-trace/filesystem.policy`에 root 소유 0400으로 전달한다. 상태 파일은 root 소유 0600이다. 내부 기록용 Volume은 RW이지만 사용자 허용 규칙에 포함하지 않는다.

## 시작 순서

1. 신뢰된 root 시작 주체가 권한 증거 FD 3, 정책 읽기 FD 4, 적용 상태 FD 5를 열고 기존 strace를 실행한다.
2. init이 입력 연결·work 기준 경로 설정·기존 UID/GID/capability/NNP 검증을 수행한다.
3. 별도 C 모듈이 TSV를 파싱하고 ABI와 각 객체를 검사하여 ruleset을 준비한다. init이 `PREPARED`를 기록한다.
4. Runner가 보호된 `PREPARED`와 정책 ID를 확인하고 기존 Task·자원 감시를 준비한 뒤 `start.ready`를 만든다.
5. init이 Landlock을 실제 적용하고 `APPLIED`를 기록한다. 내부 FD를 닫고 `/workspace/app/main`으로 exec한다.
6. Runner는 종료 후에도 일치하는 `PREPARED → APPLIED` 증거를 확인한다. 초기화·적용·필수 기록 실패 시 사용자 프로그램을 실행하지 않으며, 공개 응답은 기존 내부 오류 경로를 사용한다.

Landlock ABI 7 이상이 필요하다. 지원하지 않는 환경에서 제한 없이 실행하는 fallback은 없다. 신뢰된 strace·Runner에는 사용자 Landlock을 적용하지 않는다. 이후 생성되는 사용자 자손은 제한을 상속한다.

## 검증 및 배포 전 확인

기존 이미지는 새 helper·manifest·init을 포함하지 않으므로 반드시 다시 빌드한다. 먼저 전용 테스트 태그를 사용한다.

```bash
docker build -f runner/container/cpp/Dockerfile \
  -t codeguard-cpp:filesystem-design-test .

python -m pytest runner/tests -q

# 실제 Linux Docker 호스트의 /proc·cgroup 접근 및 기존 Runner 운영 설정 필요
CPP_IMAGE=codeguard-cpp:filesystem-design-test RUNNER_DOCKER_TESTS=1 \
  python -m pytest runner/tests/test_filesystem_integration.py -q
```

설정 기본값은 `FILESYSTEM_POLICY_PROFILE=cpp-amd64-v1`, `FILESYSTEM_STARTUP_TIMEOUT_SECONDS=5.0`이다. Docker 기본 seccomp에서 Landlock 및 init에 필요한 호출이 가능한지 실제 VM에서 검증한다. `seccomp=unconfined`로 바꾸지 않는다. 이미지 manifest 생성만으로 실제 제한·로딩 검증이 완료된 것은 아니다.

### 현재 확인한 결과 (2026-10-05)

- Windows에서 `CODEGUARD_TEST_NATIVE=1 python -m pytest runner/tests -q`: **468 passed, 76 skipped**, 기존 FastAPI deprecation 경고 4개. 이 옵션에는 WSL을 이용한 준비 helper의 실제 UID·모드 변경 및 제한된 capability 테스트가 포함된다.
- WSL Ubuntu-24.04(root)에서 `CG_RUN_LANDLOCK_SMOKE=1 CODEGUARD_TEST_NATIVE=1 python3 -m unittest runner.tests.test_native_filesystem runner.tests.test_native_start_gate -v`: **10개 중 8개 통과, 2개 건너뜀**. C 모듈은 `-O2 -Wall -Wextra -Werror`로 빌드했다. init의 준비·승인·적용·FD 정리 및 실패 시 exec 금지는 시스템 호출 주입 테스트로 확인했다.
- 실제 WSL 커널은 `6.6.87.2-microsoft-standard-WSL2`, Landlock ABI는 **3**이었다. 따라서 ABI 7 이상에서의 실제 제한 smoke test는 건너뛰었다. strace도 설치되지 않아 해당 경유 테스트는 건너뛰었다. 주입 테스트의 통과를 실제 Landlock 적용 검증으로 해석하지 않는다.
- 현재 Windows Docker daemon에는 연결할 수 없다. 새 이미지의 Docker 빌드 및 VM에서의 C/C++ 실행·마운트·strace·자원 제한 통합 검증은 **미완료**이다. 위 테스트 수의 skipped 항목에는 이 opt-in 통합 테스트가 포함된다.

배포 전에는 위 전용 이미지 빌드 후, ABI 7 이상인 실제 Linux Docker 호스트에서 다음 통합 테스트를 반드시 실행한다. opt-in 상태의 Docker 연결·이미지·환경 오류는 테스트 실패로 취급하며 제한을 약화하여 통과시키지 않는다.

```bash
CPP_IMAGE=codeguard-cpp:filesystem-design-test RUNNER_DOCKER_TESTS=1 \
  python -m pytest runner/tests/test_filesystem_integration.py \
  runner/tests/test_permission_integration.py \
  runner/tests/integration/test_task_tracker_integration.py -q
```

Task tracker 테스트에는 기존 eBPF 서비스와 cgroup 설정이 필요하다. 테스트가 끝난 뒤 준비·컴파일·실행 컨테이너, 해당 Job의 세 Volume 및 전용 cgroup이 정리됐는지도 확인한다. 위 결과는 로컬 검증 결과이며 배포 완료를 의미하지 않는다.

## 보장하지 않는 사항

- Audit/eBPF를 이용한 새 위반 감지·자동 종료·응답 코드 분류는 추가하지 않는다. 금지된 파일 작업은 커널이 거부하지만 프로그램이 오류를 처리하고 계속 실행할 수 있다. 기존 strace의 RO 변경 판정은 유지하며 새 실행 경로를 인식하도록 연결한다.
- 동적 로더의 파일 실행을 허용하므로 직접 실행도 가능하다. 모든 코드 로딩·실행 가능한 mmap·memfd를 포괄 차단하는 기능이 아니다.
- `work` 내부의 정책 조건을 만족하는 하드 링크까지 일괄 금지하지 않는다. 심볼릭 링크·특수 파일 생성 권한은 부여하지 않는다.
- chmod/chown 등 모든 메타데이터 작업을 Landlock으로 제어하지 않는다. 보호 원본에는 RO 마운트와 Linux 권한을 함께 사용한다.
- 저장 공간·inode 한도, 네트워크 정책 변경, TLS/DNS/CA 파일 제공, 파일 다운로드·영구 저장은 이번 기능에 포함하지 않는다.
