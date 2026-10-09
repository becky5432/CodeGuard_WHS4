# Runner 단일 Workspace 구현과 검증 계획

## 목표

실행별 단일 RW `/workspace`를 준비·컴파일·실행에서 공유한다. 실행 시 Landlock v2가 workspace 전체의 읽기·변경·파일 실행을 허용하고 외부는 검증된 개별 런타임 파일과 서버가 선택한 장치만 허용한다.

## 구성요소

- `pipeline/workspace.py`: nonce가 붙은 단일 Job 볼륨 생성·준비·마운트·정리.
- `native/filesystem/workspace_prepare.c`: workspace 루트만 UID/GID10001·0700으로 준비.
- `pipeline/compiler.py`: 같은 볼륨에서 main 생성·기존 ELF 산출물 확인.
- `pipeline/execution.py` 및 `security/__init__.py`: Job RW와 내부 trace RW 두 마운트·신원·capability·NNP 검증.
- `policies/filesystem.py` 및 native 정책 모듈: v2·workspace 공통 권한·개별 장치 프로파일.
- `config.py` 및 `pipeline/executor.py`: 서버 장치 선택 목록 전달. 기본값은 빈 목록.

## 구현 확인

- [x] 단일 `VolumeWorkspace(job_id, volume_name)` 및 재시도 nonce 격리.
- [x] 소스·stdin은 같은 workspace, 사용자10001 소유0600. 준비 루트0700과 `no_copy=True` 유지.
- [x] 컴파일·실행 모두 `/workspace` RW. main·stdin·현재 디렉토리 경로 통합.
- [x] 실행 RootFS RO, 별도 내부 trace 볼륨, 정확한 마운트 집합 및 정리 검증.
- [x] Python/C 정책 v2, 이전 v1 거부, 선택적 장치 설정과 권한·타입·장치 번호 검증.
- [x] workspace 내부 실행·파일 변경 허용 및 외부 접근 거부 테스트 정합성 검토. 실제 적용은 아래 ABI7 검증에서 확인한다.

## 검증 확인

- [x] 볼륨·마운트 단위 테스트와 WSL 준비 helper의 실제 소유권·권한 검증.
- [x] 정책·장치·초기화·trace 회귀 및 전체 Runner 단위 테스트.
- [x] 새 이미지 빌드 및 실제 준비·컴파일·마운트·ABI 부족 시 시작 금지 확인.
- [ ] ABI7 이상 Linux에서 실제 Landlock 적용과 C/C++·선택 장치 통합 검증.

테스트 명령과 정책 상세는 [파일시스템 정책](security/FILESYSTEM_POLICY.md)을 기준으로 한다. 실제 적용을 확인하지 않은 검증 항목을 통과로 표시하지 않는다.

수행한 검증과 환경 제한은 [v2 구현 검증 결과](security/FILESYSTEM_V2_VERIFICATION.md)에 기록한다.
