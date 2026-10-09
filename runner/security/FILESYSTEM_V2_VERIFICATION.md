# 파일 경로 제어 v2 구현 검증 결과

검증일: 2026-10-09

## 반영한 코드

- 실행별 nonce를 포함한 단일 Job 볼륨을 준비·컴파일·실행에 `/workspace:rw`로 연결한다. 컴파일·실행 컨테이너는 분리한다.
- `/workspace` 계층의 읽기·목록·변경·파일 실행과 파일 종류별 생성 권한을 허용한다. 소스·stdin·main도 같은 정책이다. Linux의 기존 권한·capability 제한은 유지한다.
- 외부 로더·공유 라이브러리 4개·선택적 캐시는 검증된 개별 파일만 허용한다. `/tmp`, `/var/tmp`, `/dev/shm` 허용 규칙은 없으며 임시 환경 변수는 `/workspace`이다.
- 서버 설정 `FILESYSTEM_DEVICE_PATHS`의 기본값은 빈 목록이다. 선택한 `/dev/null`은 `READ_FILE|WRITE_FILE`, `/dev/urandom`·`/dev/zero`·`/dev/random`은 `READ_FILE`을 부여한다. 경로·문자 장치 타입·장치 번호·실제 대상을 같은 FD에서 검증한다.
- Python 생성기·C 파서·적용기·실패 상태의 정책 ID 복구를 정책 v2로 맞췄다. manifest와 상태 형식은 v1, 최소 Landlock ABI는 7을 유지한다.
- 내부 trace는 별도 익명 RW 볼륨이다. RootFS RO, UID/GID10001, 기존 capability·NNP·자원 제한과 FD 정리를 유지한다.
- workspace 작업을 허용하는 정책에 맞춰 테스트를 수정했다. strace의 EROFS 판정 계약을 유지하며 EACCES·EPERM을 자동 위반으로 분류하지 않는다.

장치 설정 예시:

```bash
FILESYSTEM_DEVICE_PATHS='["/dev/null","/dev/urandom"]'
```

## 수행 결과

아래 표는 develop 동기화 전 검증 기록이다. 동기화 후 PR 반영 검증은 문서 마지막 절에서 구분한다.

| 검증 | 결과 | 확인 범위 |
| --- | --- | --- |
| Windows 전체 Runner pytest | 482 passed, 78 skipped | Python 정책·설정·파이프라인·trace·회귀 테스트 |
| Linux 전체 Runner pytest | 491 passed, 69 skipped, 84 subtests passed | 위 회귀와 Linux 네이티브 harness |
| 네이티브·시작 승인 opt-in 검증 | 8 passed, 2 skipped | 파서·규칙·실패 경로·시작 승인. 실제 적용 smoke는 ABI 부족으로 skip |
| 실제 workspace 준비 helper 및 관련 테스트 | 55 passed | 루트 소유권·모드, 단일 볼륨·마운트 계약 |
| 통합 테스트 수정 후 관련 회귀 | Windows/Linux 각각 176 passed, 48 skipped | workspace 쓰기 기대값·외부 위반·외부 metadata ioctl 테스트 정합성 |
| 새 Docker 이미지 빌드 | 성공 | C 런처 빌드, manifest 생성, 단일 workspace 디렉토리 |
| 실제 C/C++ 컨테이너 준비·컴파일 | 두 언어 성공 | 소스·stdin 소유권과0600, ELF 산출물, 동일 Job 볼륨 |
| 실제 실행 컨테이너 구성 | 성공 | Job RW와 별도 trace RW 두 마운트, RootFS RO, 임시 환경 변수 |
| 실제 ABI 부족 시 시작 | 두 언어에서 사용자 코드 시작 금지 | exit126, FAILED/abi=3/step=abi, APPLIED 없음 |
| 검증 자원 정리·재시도 격리 | 성공 | 검증 Job·trace 볼륨과 컨테이너 삭제, 동일 Job ID 재시도의 다른 nonce |

Windows의 기존 FastAPI 관련 deprecation 경고 4개와 Linux의 Starlette/httpx 경고 1개가 기록됐다. 테스트 실패는 없었다. skipped 결과를 실제 Landlock 적용 성공으로 계산하지 않는다.

이미지: `codeguard-cpp:filesystem-workspace-v2`

WSL Docker 이미지 ID: `sha256:e55e0d361f3039a1a0d867c31eb08c985e3fd39e9990731e01e7731091e1cb78`

빌드의 기본 bridge에서 패키지 저장소 DNS 조회가 지연되어 신뢰된 이미지 빌드에만 `--network=host`를 사용했다. 실제 실행 구성 검증에는 `EXECUTION_NETWORK=none`을 사용했다.

## 남은 실제 적용 검증

현재 검증 호스트는 WSL kernel `6.6.87.2-microsoft-standard-WSL2`, Landlock ABI3이다. 요구 ABI7을 충족하지 않으므로 정상 사용자 실행·workspace 권한·외부 차단·선택 장치의 실제 Landlock 적용은 아직 확인하지 못했다. 요구 ABI를 낮추거나 제한 없이 실행하는 fallback은 추가하지 않았다.

ABI7 이상 Linux 호스트에서 새 이미지와 [정책 문서의 통합 테스트 명령](FILESYSTEM_POLICY.md)을 사용해 실제 적용과 C/C++ 실행, 선택 장치, 자원 제한·cgroup 정리를 확인해야 한다. 이번 실제 컨테이너 검증은 prepare/compile/execution 구성과 ABI 실패 경로를 확인했으며 전체 Runner 자원 측정 통합을 대체하지 않는다.

상세 정책: [파일 경로 접근 정책 v2](FILESYSTEM_POLICY.md). 구현 확인 목록: [단일 workspace 구현과 검증 계획](../WORKSPACE_MOUNT_POLICY_PLAN.md).

## 구현 후 재점검

2026-10-09 사용자 요청으로 현재 작업 파일을 다시 확인했다. Windows 전체 테스트는 482 passed/78 skipped, Linux 네이티브·시작 승인 검증은 8 passed/2 skipped였다. 실제 C/C++ 컨테이너 검증도 준비·컴파일·동일 볼륨 마운트·자원 정리·nonce 격리를 통과했고, ABI3에서는 사용자 코드 시작이 차단됐다. 코드에서 workspace 권한, 개별 외부 파일·장치 권한, 정책 v2, 최소 ABI7, 실패 시 시작 금지의 연결을 확인했다.

추가 주요 결함은 발견하지 못했다. 기존 마운트 검증기는 trace 볼륨이 Job 볼륨과 별개라는 점을 확인하지만 익명 할당 여부까지 독립적으로 확인하지 않는다. 생성 코드는 익명 볼륨을 요청하며 이번 실제 검증에서 해당 볼륨의 정리를 확인했다. ABI7 실제 적용과 전체 Runner 자원 통합 검증은 위의 미확인 항목으로 유지한다.

## develop 동기화 후 PR 반영 검증

2026-10-09 origin/develop `0ce712e`를 병합했다. 네트워크 프리셋·DNS 옵션과 파일시스템 정책을 함께 전달하며, develop의 `policy_violations` 응답 계약을 유지한다. 파일·네트워크 위반 증거는 실행 결과의 status와 구분하며 0으로 종료한 프로그램은 SUCCESS와 위반 목록을 함께 반환할 수 있다. 기존 단위·API·Docker 통합 테스트의 기대값과 관련 정책 문서를 이 계약에 맞췄다.

- 최종 Windows 전체 Runner 테스트: **486 passed, 78 skipped**, 기존 경고4개. 두 종류의 위반 동시 보존, backend 모델 변환, 네트워크 프리셋과 파일시스템 정책의 동시 전달을 확인했다.
- PR 코드 재검토: Critical/Important 결함 없음. trace 익명 할당의 독립 inspect 검증 한계와 ABI7 실제 적용 미확인은 유지한다.
- 이번 Linux 전체 테스트·실제 컨테이너 재검증은 WSL이 상태 조회에도 응답하지 않아 결과를 얻지 못했다. 대기 중인 검증 클라이언트를 중단했으며 통과로 기록하지 않는다. 위의 Linux 및 컨테이너 결과는 동기화 전 수행 기록이다.
- 네트워크·DNS 옵션의 전달 확인은 Landlock 적용 환경에서 DNS 이름 해석 성공을 보장하는 검증이 아니다. 외부 resolver 설정 파일을 허용 목록에 추가하지 않았다.

## VMware 실제 smoke 검사 수정

사용자가 제공한 VMware Linux 실행 기록에서 실제 smoke가 규칙 적용·읽기 및 작업 파일 검사 후 장치 생성 assertion에서 실패했다. 기존 테스트는 문자 장치 번호0:0에 CAP_MKNOD 제거 후 EPERM을 기대했으나, Linux는 이 번호를 whiteout으로 취급해 해당 capability 검사에서 예외로 처리한다. 테스트를 일반 장치 번호 `makedev(1, 3)`으로 수정하고 sys/sysmacros.h를 포함했다. CAP_MKNOD 제거·EPERM 확인과 부모의 정확한 테스트 경로 정리를 유지한다. [Linux v6.12 커널 코드](https://github.com/torvalds/linux/blob/v6.12/fs/namei.c#L3865)

수정 후 Windows 회귀는 486 passed/78 skipped였다. 로컬 WSL 조회는 응답 시간 초과였으며 수정된 실제 smoke의 Linux 실행 결과는 아직 확인하지 못했다. VMware에서 수정본을 받은 뒤 동일 smoke를 다시 실행해야 한다. 이 변경은 테스트 파일에 한정되며 운영 Landlock 권한과 C 런처는 바꾸지 않는다.

## VMware 실제 적용 결과 및 API 테스트 수정

2026-10-09 사용자가 제공한 VMware Linux/Python 3.12.3 실행 기록을 확인했다. 아래 결과는 사용자의 호스트에서 수행한 증거이며 로컬 Windows에서 실제 컨테이너를 재실행한 결과로 기록하지 않는다.

- `39903a1`을 받은 뒤 실제 커널 smoke: **1 passed, 7 deselected**. 요구 ABI7 이상과 실제 규칙 적용, 허용·거부 및 상속 검사를 통과했다. 출력에 정확한 ABI 숫자는 표시되지 않았다.
- 새 이미지의 파일시스템·권한 Docker 통합 테스트: **50 passed, 1 failed, 15 subtests passed**. C/C++ 실행, workspace 변경·실행·링크 생성, 외부 읽기·실행·쓰기 차단, 선택 장치, 자식·스레드 상속, 보호된 증거, 시작 승인·APPLIED 및 테스트에 포함된 자원 제한을 확인했다.
- 유일한 실패는 `test_real_runner_api_filesystem_limit_validates_with_backend`의 마지막 스키마 변환이다. 실제 `/execute` 응답의 HTTP200·exit0·denied=1·SUCCESS 및 Backend RunnerResponse 검증까지 통과했으며, 반환된 위반 목록에는 FILESYSTEM_LIMIT가 있었다.
- Backend 조회 응답은 저장된 요청 정보와 Runner 결과를 함께 사용한다. 테스트가 Runner 결과만 전달해 language/code/stdin/created_at/policy가 누락됐다. 요청을 보존하고 필수 output_limit_bytes를 명시한 뒤 요청 정보와 검증된 Runner 응답을 합쳐 조회 응답 스키마를 검사하도록 수정했다.
- 수정 전 동일한 5개 필드 누락 오류를 로컬에서 재현했다. 수정 후 실제 Pydantic 모델과 해당 테스트 메서드의 스키마 부분은 빈 위반 목록 및 FILESYSTEM_LIMIT 목록 모두 통과했다. 이 확인에는 제공된 HTTP 응답을 사용했으며 Docker 실행을 포함하지 않는다. 관련 API·응답 계약 단위 테스트는 **26 passed**, 기존 경고4개였다.

수정된 API 테스트의 VMware 재실행은 아직 남아 있다. TASK_TRACKER_ENABLED=false로 실행한 통합 결과이므로 eBPF task tracker를 활성화한 전체 측정 경로의 검증으로 확대하지 않는다. 로그의 resource_monitor_close_error 경고는 이번 스키마 실패의 원인이 아니며, 이 테스트 수정에서 해당 자원 모니터 코드를 변경하지 않았다.
