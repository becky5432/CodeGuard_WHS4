# 요청 계약에 출력 필드가 추가되기 전까지 적용하는 Runner 안전 상한이다.
# stdout과 stderr의 raw byte 합계에 적용한다.
EXECUTION_OUTPUT_LIMIT_BYTES = 1024 * 1024

# 사용자 runtime 정책과 분리된 Runner 내부 컴파일 안전 상한이다.
COMPILE_TIMEOUT_SECONDS = 10
