# 9/25 Memory + Wall Time 사용량 수집

## 목적

Memory Usage + Wall Time 수집

## 기준 develop

`a276ad4da6c90790c763984546ca6bbd497bd1ba`

## 작업 branch

`feature/memory-walltime-usage`

## 구현

- cgroup v2 `memory.current` 기반 Memory Usage 시계열
- `time.monotonic()` 기반 기존 Wall Time 측정 검증 및 전달
- Runner → Backend → DB JSON → 조회 API 연결

Memory sample의 `elapsed_ms`를 Wall Time 그래프의 시간축으로 함께 사용한다.
별도의 중복 Wall Time sample 배열은 저장하지 않는다.

## 제외

- CPU Usage 구현 및 변경
- Frontend 그래프 구현

## 상태

- Memory Usage 수집 및 API 전달: 완료
- Wall Time 측정 및 API 전달: 완료
- Docker Runner 실제 통합 테스트: Docker daemon 부재로 미실행

테스트 프로그램은 `test_programs/`에 보관한다.
