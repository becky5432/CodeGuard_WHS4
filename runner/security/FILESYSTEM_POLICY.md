# 파일 경로 접근 정책 v2

## 작업 영역과 외부 허용 목록

준비·컴파일·실행 컨테이너가 실행별 단일 Job 볼륨을 `/workspace` RW로 사용한다. 컴파일·실행 컨테이너는 분리하며, 사용자 Landlock 정책은 실행 시작 전에 init이 적용한다. 기존 app/input/work 세 영역 구성은 사용하지 않는다.

| 대상 | 사용자 프로그램의 허용 작업 |
| --- | --- |
| `/workspace` 및 하위 경로 | 읽기·목록·쓰기·잘라내기·생성·삭제·이름 변경·내부 링크·파일 실행 |
| 검증된 외부 동적 로더 | 읽기·파일 실행 |
| manifest에서 검증한 공유 라이브러리 4개 | 읽기·동적 로딩 |
| `/etc/ld.so.cache` | manifest에 포함된 경우 읽기 |
| 서버가 선택한 `/dev/null` | 장치 데이터 읽기·쓰기 |
| 서버가 선택한 `/dev/urandom`, `/dev/zero`, `/dev/random` | 장치 데이터 읽기 |
| 허용 목록 밖의 경로 | Landlock이 처리하도록 지정한 접근 기본 거부 |

`/workspace/main`, 소스 및 `/workspace/stdin`은 같은 계층 권한을 받는다. main만 실행하도록 제한하지 않으며, 소스·입력을 실행 중 변경·삭제·교체할 수 있다. 심볼릭 링크·FIFO·경로형 Unix 소켓·장치 파일 생성에도 workspace의 생성 권한을 부여한다. capability 추가는 없으므로 문자·블록 장치 생성은 Linux의 별도 권한 조건을 만족해야 한다. 외부 대상으로 연결되는 링크를 만들 수 있어도 그 대상 접근은 허용 목록을 따른다.

## 볼륨과 임시 파일

Job 볼륨 루트는 UID/GID10001:10001, 모드0700이며 소스·stdin은 해당 사용자 소유0600이다. Job 마운트는 `no_copy=True`로 준비된 소유권을 유지한다. 컴파일 산출물의 일반 파일·ELF·소유권·실행 권한 검증도 유지한다.

실행 RootFS는 RO이다. 명시적 실행 마운트는 Job RW `/workspace`와 별도 익명 RW `/run/codeguard-trace` 두 개이다. 정책·상태·strace는 root가 기록하며 사용자 허용 목록에 넣지 않는다. 추가 마운트·tmpfs·중복 경로·다른 Job 볼륨·잘못된 RW 설정은 거부한다.

`TMPDIR`, `TMP`, `TEMP`는 `/workspace`이다. `/tmp`·`/var/tmp`·`/dev/shm`에는 허용 규칙을 두지 않는다. `/tmp` 하드코딩이나 POSIX 공유 메모리 사용까지 지원하는 구성은 아니다. Docker의 별도 마운트가 RW여도 Landlock 허용 규칙을 대체하지 않는다.

## 선택적 장치 설정

`FILESYSTEM_DEVICE_PATHS`는 서버 설정이며 기본값은 빈 목록이다. 사용자 제출 API에는 장치 선택 필드를 추가하지 않는다. 예시는 다음과 같다.

```bash
FILESYSTEM_DEVICE_PATHS='["/dev/null","/dev/urandom"]'
```

| 정책 프로파일 | 허용할 수 있는 경로 | Landlock `allowed_access` |
| --- | --- | --- |
| `DEVICE_RW` | `/dev/null` | `READ_FILE`·`WRITE_FILE` |
| `DEVICE_READ` | `/dev/urandom`, `/dev/zero`, `/dev/random` | `READ_FILE` |

프로파일은 CodeGuard가 정의한 이름이다. 실제 커널에는 해당 권한 비트를 전달한다. 장치에 실행·잘라내기·생성·삭제·이름 변경이나 `IOCTL_DEV`를 부여하지 않는다. `IOCTL_DEV`가 제한하는 ioctl은 거부하지만 일부 일반 ioctl 예외도 있으므로 모든 ioctl의 전면 차단으로 설명하지 않는다.

장치는 `O_PATH`·`O_NOFOLLOW`·`O_CLOEXEC`로 열고 같은 FD에서 문자 장치 타입·실제 대상·기대 장치 번호를 검사한다. 임의 일반 파일·링크·다른 장치로 교체된 경로는 거부한다. 전체 `/dev` 허용 규칙은 추가하지 않는다. 표준 입출력은 init이 전달한 FD0·1·2를 사용하며 이 때문에 `/dev/stdin` 등의 경로를 자동 허용하지 않는다.

## 정책 생성과 시작 순서

지원 이미지의 신뢰된 C/C++ 예제에서 로더·공유 라이브러리 4개·선택적 캐시를 검증하여 runtime manifest를 만든다. 사용자 제출 바이너리에 `ldd`를 실행하거나 제출별 의존성을 분석하는 기능은 아니다. 준비·컴파일·실행은 동일한 불변 이미지 ID를 사용한다.

TSV 정책은 `CGFS` 버전2이며 runtime manifest 스키마1·프로파일 `cpp-amd64-v1`, 상태 레코드 `CGFS_STATUS 1`과 구분한다. 이전 v1 정책은 거부한다. Python 생성기와 C 파서는 경로·프로파일·크기·중복을 검증하며, workspace 루트도 `O_NOFOLLOW` 경로 확인 대상에 포함한다.

1. root tracer가 보호된 권한·정책·상태 FD를 준비한다.
2. init이 사용자10001:10001·capability·NNP를 검증한다. 검증 통과 후 stdin과 현재 디렉토리 `/workspace`를 연결한다.
3. C 모듈이 정책과 실제 파일을 확인하고 ruleset을 준비한다. init이 `PREPARED`를 기록한다.
4. Runner가 정책 ID·상태를 확인하고 기존 감시를 준비한 후 시작 승인한다.
5. init이 Landlock을 적용하고 `APPLIED`를 기록한다. 내부 FD 및 불필요한 상속 FD를 닫고 `/workspace/main`을 실행한다.
6. Runner가 일치하는 증거와 결과를 수집하고 실행 자원을 정리한다.

**Landlock ABI7 이상이 필요하다.** 정책 준비·적용·필수 증거 실패 시 사용자 코드를 시작하지 않는다. ABI 요구를 낮추거나 제한 없는 실행으로 바꾸는 fallback은 없다. 이후 생성된 사용자 자식과 스레드에도 제한이 상속된다. 신뢰된 Runner·strace에는 사용자 Landlock 정책을 적용하지 않는다.

## 검증과 배포

이전 이미지의 런처와 새 정책을 혼합하지 않는다. 저장소 루트에서 새 이미지를 빌드한 뒤 같은 태그를 Runner에 설정한다.

```bash
docker build -f runner/container/cpp/Dockerfile -t codeguard-cpp:filesystem-workspace-v2 .
python -m pytest -q runner/tests
CG_RUN_LANDLOCK_SMOKE=1 CODEGUARD_TEST_NATIVE=1 python -m pytest -q runner/tests/test_native_filesystem.py runner/tests/test_workspace.py
CPP_IMAGE=codeguard-cpp:filesystem-workspace-v2 RUNNER_DOCKER_TESTS=1 python -m pytest -q runner/tests/test_filesystem_integration.py runner/tests/test_permission_integration.py
```

실제 적용 테스트에는 ABI7 이상 Linux 커널이 필요하다. 통합 자원 측정에는 Docker 호스트의 `/proc`·cgroup v2 접근 및 기존 운영 설정이 필요하다. skipped 테스트나 시스템 호출 주입 테스트를 실제 Landlock 적용 검증으로 계산하지 않는다. 테스트 종료 후 해당 Job 볼륨·익명 증거 볼륨·컨테이너·cgroup을 확인한다.

## 차단과 감지의 범위

Landlock은 처리 대상으로 지정한 접근을 차단한다. strace 판정은 추적된 변경 요청의 `EROFS`와, Landlock이 중재하는 경로 변경·쓰기 open이 정책상 쓰기 허용 경로 밖에서 `EACCES`로 실패한 경우를 사용한다. `EPERM`과 `/workspace` 안의 일반 Unix 권한 오류는 자동으로 `FILESYSTEM_LIMIT`로 분류하지 않는다. 오류를 처리하는 프로그램은 계속 실행할 수 있다. 자세한 기록 계약은 [filesystem detection](FILESYSTEM_DETECTION.md)을 따른다.

develop의 응답 계약에 따라 감지한 위반은 `policy_violations`에 기록하며 실행 결과의 `status`와 구분한다. 차단 오류를 처리하고 0으로 종료한 프로그램은 `SUCCESS`와 `policy_violations=["FILESYSTEM_LIMIT"]`를 함께 반환할 수 있다.

파일 실행 권한은 exec 계열을 제어한다. 읽기만 허용한 공유 라이브러리도 실행 가능한 코드로 로딩될 수 있다. Landlock은 이 정책의 파일 내용·경로 구조 변경 권한을 중재하지만 모든 메타데이터 조회·변경, 실행 가능한 메모리 매핑·통신을 제어하는 기능은 아니다. Linux Landlock은 현재 `chmod`, `chown`, `setxattr`, `utime` 계열 자체를 제한하지 않는다. ABI7 네이티브 측정에서 정책 밖의 사용자 소유 파일 `chmod`, `setxattr`, `utimensat`는 성공했다. 따라서 해당 메타데이터 변경의 차단을 Landlock만으로 보장하지 않는다. 적용 전에 열린 FD를 모두 회수하는 기능도 아니므로 init의 FD 정리를 유지한다. 마운트·DAC·capability·seccomp와 기존 자원 제한은 계속 적용한다.
