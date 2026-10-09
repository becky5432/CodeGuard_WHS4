# Runner 단일 Workspace 마운트 설계

## 목적

준비·컴파일·실행 컨테이너가 같은 Job 볼륨을 `/workspace` RW로 사용한다.
실행 단계에서는 Landlock이 workspace 내부의 읽기·변경·파일 실행을 허용하고,
외부 경로는 검증된 개별 런타임 파일과 서버가 선택한 장치만 허용한다.

## 마운트 정책

| 단계 | 컨테이너 경로 | 권한 | 목적 |
| --- | --- | --- | --- |
| Prepare | `/workspace` | `rw` | 소스·stdin 업로드, UID/GID10001·0700 준비 |
| Compile | `/workspace` | `rw` | 소스·stdin 읽기 및 실행파일 `main` 생성 |
| Execute | `/workspace` | `rw` | 제출 파일 및 작업 파일 읽기·변경·파일 실행 |
| Execute | `/run/codeguard-trace` | 별도 `rw` | root 추적 주체의 정책·상태·로그 기록 |

## 실행 흐름

1. Runner가 새 nonce를 포함하는 Job 볼륨 하나를 생성한다.
2. 준비 컨테이너가 소스·stdin을 `/workspace`에 제공하고 루트 소유권·권한을 설정한다.
3. 별도 컴파일 컨테이너가 같은 볼륨에서 `/workspace/main`을 생성하고 산출물을 검증한다.
4. 실행 컨테이너가 같은 Job 볼륨과 별도 익명 증거 볼륨을 연결한다.
5. init이 stdin을 연결하고 `/workspace`를 현재 디렉토리로 설정한다. Landlock 정책 준비·시작 승인·실제 적용 및 내부 FD 정리 후 main을 실행한다.
6. Runner가 컨테이너·Job 볼륨·익명 증거 볼륨과 기존 실행 자원을 정리한다.

## 권한과 검증

- 단일 `VolumeWorkspace(job_id, volume_name)`을 사용한다. app/input/work 볼륨 구분은 제거한다.
- Job 마운트에 `no_copy=True`를 사용해 준비된 UID/GID10001·0700을 유지한다. 소스·stdin은 0600이다.
- 실행 RootFS는 RO, Job workspace는 RW이다. 실행의 명시적 볼륨은 정확히 2개이며 이름이 달라야 한다.
- 마운트 이름·대상·RW·타입·중복·추가 mount/tmpfs 및 기존 사용자·capability·NNP 설정을 검증한다.
- `/workspace/main`은 workspace의 일반 실행 파일이며 별도 권한 영역이 아니다. 소스·stdin도 실행 중 변경·삭제할 수 있다.

## 임시 파일과 외부 접근

- `TMPDIR`, `TMP`, `TEMP`는 `/workspace`이다. `/tmp`·`/var/tmp`·`/dev/shm`에는 Landlock 허용 규칙을 두지 않는다.
- 외부 로더는 읽기·실행, manifest의 공유 라이브러리 4개와 선택적 캐시는 읽기만 허용한다.
- 개별 장치의 서버 선택 시 권한은 [파일시스템 정책](security/FILESYSTEM_POLICY.md)을 따른다. 허용 목록 밖의 처리 대상 접근은 거부한다.
- Landlock의 처리 대상은 파일 내용·경로 구조 작업이다. `chmod`·`chown`·`setxattr`·`utime` 같은 메타데이터 변경은 Landlock 권한에 포함되지 않으며 RootFS RO, DAC, capability에 따라 별도로 제한된다.
- 내부 증거 볼륨은 사용자 허용 목록에 포함하지 않는다. 저장 공간·inode 한도 및 추가 라이브러리·장치 지원은 별도 범위다.

## 완료 기준

- Compile Container가 `/workspace`에 `main`을 생성할 수 있다.
- Execution Container 생성 설정에 `/workspace:rw`와 별도 보호된 trace 볼륨이 적용된다.
- 기존 컴파일·실행·응답 동작이 유지된다.
- 관련 Runner 테스트가 모두 통과한다.
