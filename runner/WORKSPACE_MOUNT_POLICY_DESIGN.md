# Runner Workspace 마운트 권한 분리 설계

## 목적

Compile Container에는 소스 코드와 실행파일을 생성할 쓰기 권한을 제공하고,
Execution Container에는 생성된 실행파일과 stdin을 읽을 권한만 제공한다.

## 마운트 정책

| 단계 | 컨테이너 경로 | 권한 | 목적 |
| --- | --- | --- | --- |
| Compile | `/workspace` | `rw` | 소스·stdin 읽기 및 실행파일 `main` 생성 |
| Execute | `/workspace` | `ro` | `main`과 stdin 읽기 및 실행만 허용 |

## 실행 흐름

1. Runner가 Job Volume을 생성한다.
2. Compile Container가 Job Volume을 `/workspace:rw`로 마운트한다.
3. 소스 TAR를 `/workspace`에 전달하고 `main`을 생성한다.
4. 컴파일 성공 시 Execution Container가 같은 Job Volume을 `/workspace:ro`로 마운트한다.
5. Execution Container가 `/workspace/main`을 실행한다.
6. Runner가 컨테이너와 Job Volume을 정리한다.

## 변경 범위

- `runner/pipeline/execution.py`의 Execution Container Volume 모드를 `rw`에서 `ro`로 변경한다.
- Compile Container의 기존 `rw` 설정은 유지한다.
- 단위 테스트에서 Compile=`rw`, Execute=`ro`를 각각 검증한다.

## 제외 범위

- 컨테이너 루트 파일시스템의 `read_only=True` 적용
- `/tmp` tmpfs 구성
- 자원 제한 및 seccomp 정책 변경
- 별도 실행 전용 Volume 생성

## 완료 기준

- Compile Container가 `/workspace`에 `main`을 생성할 수 있다.
- Execution Container 생성 설정에 `/workspace:ro`가 적용된다.
- 기존 컴파일·실행·응답 동작이 유지된다.
- 관련 Runner 테스트가 모두 통과한다.
