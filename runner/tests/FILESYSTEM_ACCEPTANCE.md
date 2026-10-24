# VM 파일시스템 정책 자동 검증

경로와 작업별 허용·차단 결과를 한 번에 확인하고, 보고서에 사용할 화면과 원본 증거를 저장한다.
운영 코드를 수정하지 않고 기존 작업 공간 준비 → 컴파일 → 실행 → 정리 파이프라인을 호출한다.

## 실행 준비

저장소 루트에서 실행한다. Linux amd64 Docker Engine, cgroup v2와 실행 이미지가 필요하다.
기존 파일시스템 Docker 통합 테스트와 동일하게 Runner의 cgroup 생성·제거 권한이 있어야 한다.

```bash
source .venv-test/bin/activate
python -m pip install -r runner/requirements.txt

docker build -f runner/container/cpp/Dockerfile -t codeguard-cpp:dev .
```

## 전체 실행과 캡처

```bash
CPP_IMAGE=codeguard-cpp:dev \
python -m runner.tests.filesystem_acceptance --ebpf off
```

실행할 때마다 경로, 작업, 기대 정책, 실제 결과 및 PASS/FAIL이 터미널에 출력된다.
완료하면 `artifacts/filesystem-acceptance-<시각>-<고유값>/`에 다음 파일이 생성된다.

| 파일 | 용도 |
| --- | --- |
| `report.html` | VM 브라우저에서 열어 범위별 결과를 캡처하는 화면 |
| `results.md` | 보고서에 옮길 수 있는 경로별 결과 표 |
| `results.json` | 실행 환경·정책·전체 판정 원본 |
| `evidence/<시험 ID>/trace.log` | 해당 실행에서 Runner가 수집한 strace 파일 |
| `evidence/<시험 ID>/filesystem.status` | 정책 ID·실제 ABI·APPLIED 확인 기록 |
| `evidence/<시험 ID>/result.json` | 실제 반환값·Runner 분류·실행 ID·eBPF 이벤트 |
| `evidence/<시험 ID>/stdout.txt`, `stderr.txt` | 제출 진단 프로그램의 출력 |

터미널 마지막에 출력된 HTML 경로를 VM 브라우저에서 연다.
상단의 `/workspace`, `외부 허용목록`, `그 밖의 경로`, `strace`, `eBPF` 링크로 이동해 각 표를 캡처하면 된다.
브라우저 인쇄 기능을 사용하면 범위별로 나뉜 PDF로도 저장할 수 있다.
스크린샷과 함께 `results.json`과 `evidence/`를 보관하면 캡처에 보이는 판정을 다시 확인할 수 있다.

## 검증 항목

| 범위 | 수행 내용 |
| --- | --- |
| 환경 | RootFS RO, `/workspace` RW, 실제 Landlock ABI와 적용 기록 |
| `/workspace` | 읽기, 목록 조회, 일반 파일·디렉터리 생성, 덮어쓰기, 추가 쓰기, truncate, 삭제, 이름 변경·이동, C/C++17 프로그램 실행 |
| 외부 허용목록 | 준비 컨테이너에서 확인한 실제 로더·라이브러리·캐시 경로별 읽기와 변경 차단, 로더 실행 |
| 선택 장치 | 설정된 장치만 실제 정책에 따라 읽기·쓰기 검사 |
| 그 밖의 경로 | `/etc`, `/tmp`, `/dev/shm`, `/proc`, 내부 추적 영역 접근 및 상대 경로·symlink·dirfd 검사 |
| strace | 실제 권한 거부 호출·경로·errno와 Runner의 `FILESYSTEM_LIMIT` 비교, 정상 접근·가짜 stderr 오탐 확인 |
| eBPF | Landlock 거부 이벤트 수집, 정상 접근 오탐, 이벤트와 실행 `run_id` 대조 |

외부 허용목록은 문서에 적힌 후보 경로를 하드코딩하지 않고, 실제 실행 이미지에서 생성한 정책 규칙을 사용한다.
기본 설정 `FILESYSTEM_DEVICE_PATHS=()`에서는 장치 허용 시험을 만들지 않는다.
서비스에서 장치를 허용하는 경우에는 같은 설정을 전달해 검증한다. 예를 들어 `/dev/null`만 허용한다면:

```bash
FILESYSTEM_DEVICE_PATHS='["/dev/null"]' \
python -m runner.tests.filesystem_acceptance --ebpf off
```

## eBPF 파일시스템 감지 검증

`develop`의 기존 eBPF Task 추적과 파일시스템 Landlock 거부 감지는 구분한다.
파일시스템 감지 코드가 포함된 브랜치와 그 코드로 빌드한 호스트 데몬을 준비한 환경에서 실행한다.
Runner가 사용하는 `TASK_TRACKER_SOCKET`에 접근할 수 있어야 한다.

```bash
CPP_IMAGE=codeguard-cpp:dev \
python -m runner.tests.filesystem_acceptance --ebpf require
```

- `off`: 파일시스템 접근과 strace 기준으로 검사하고 eBPF 항목은 SKIP으로 표시한다.
- `auto` (기본값): 파일시스템 감지 필드와 서비스 소켓이 준비된 경우 수집기를 연결한다. 미지원·미준비 또는 데몬이 감지 훅 비활성을 응답하면 eBPF 항목은 SKIP이다.
- `require`: eBPF 감지 미지원·소켓 미준비도 FAIL로 기록한다.

eBPF의 `LANDLOCK_EBPF`와 `LANDLOCK_EBPF_FENTRY` 증거를 모두 처리한다.
이벤트의 실행 ID와 소스를 검증하며, 정상 접근에 위반 이벤트가 붙는지도 별도로 확인한다.
실행별 실제 스냅샷의 `active` 상태도 저장한다. 수집 활성 여부를 확인하지 못한 경우에는 빈 이벤트를 정상으로 판정하지 않고 FAIL로 표시한다.
커널이 RO 마운트에서 먼저 거부한 `EROFS` 시험만으로 eBPF 검증을 통과시키지 않으며,
Landlock 거부를 확인하기 위해 `/proc/self/status` 읽기와 `/dev/shm` 쓰기 시험을 포함한다.

## 일부 항목만 재실행

```bash
python -m runner.tests.filesystem_acceptance --group workspace --ebpf off
python -m runner.tests.filesystem_acceptance --group allowlist --ebpf off
python -m runner.tests.filesystem_acceptance --group denied --ebpf off
python -m runner.tests.filesystem_acceptance --group strace --ebpf off
python -m runner.tests.filesystem_acceptance --group ebpf --ebpf require
```

`--output-dir`로 저장 위치를 지정할 수도 있다. 기존 증거를 덮어쓰지 않도록 비어 있는 폴더만 받는다.

```bash
python -m runner.tests.filesystem_acceptance --ebpf off --output-dir artifacts/fs-report-01
```

## 판정 기준과 실행 방식

- 허용 시험은 시스템 호출 성공과 실제 내용·파일 상태 변화까지 확인한다.
- 차단 시험은 대상 존재 또는 준비 조건과 `EACCES`·`EPERM`·`EROFS`를 확인한다. `ENOENT`·`EXDEV`는 차단 성공으로 세지 않는다.
- 경로 접근 결과와 strace/eBPF 감지 결과는 별도 행에 기록한다. 파일이 차단됐어도 감지가 실패하면 감지 행은 FAIL이다.
- eBPF가 없는 상태는 SKIP이며 PASS 수에 포함하지 않는다.
- 종료 코드는 FAIL/ERROR가 있으면 1, 모든 항목이 SKIP이면 2, 그 외에는 0이다. 결과 표의 SKIP도 함께 확인한다.
- C 진단 코드는 시험용 Volume에 한 번 컴파일하고, 각 항목마다 새 실행 컨테이너·cgroup·run_id를 사용한다. C++17 실행 시험은 마지막에 따로 컴파일한다.
- 쓰기 시험은 시험용 컨테이너와 Volume 안에서 수행한다. 컨테이너·cgroup·Volume은 실행 종료나 예외 발생 후 정리한다.
- 정상 작업별 결과를 비교하기 위한 시험이므로 같은 시험 Volume을 순차 사용한다. Job 간 데이터 잔존 검증과 HTTP/Backend 저장 검증은 별도 테스트를 사용한다.
- SIGINT로 중단한 경우 부분 결과와 중단 상태를 보고서에 남긴다.

## 검증 도구 자체의 테스트

```bash
python -m pytest runner/tests/test_filesystem_acceptance.py -q

CODEGUARD_TEST_NATIVE=1 \
python -m unittest runner.tests.test_filesystem_acceptance.NativeAcceptanceProbeTests -v
```

첫 명령은 판정·보고서 형식을 검사한다. 두 번째는 Linux gcc/g++로 실제 진단 프로그램을 컴파일하고
일반 파일 작업과 결과 검증을 확인한다. 파일시스템 제한이 적용된 실제 결과는 위 Docker 실행 명령으로 확인한다.
