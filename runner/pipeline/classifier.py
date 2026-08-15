from dataclasses import dataclass

from runner.models.result import (
    RunnerReasonCode,
    RunnerStage,
    RunnerStatus,
)


@dataclass(frozen=True)
class Classification:
    status: RunnerStatus
    reason_code: RunnerReasonCode | None
    stage: RunnerStage | None
    error_message: str | None


def classify_execution(result) -> Classification:
    """Execution 증거를 우선순위에 따라 대표 결과 하나로 변환한다."""

    if result.system_error:
        return Classification(
            status=RunnerStatus.ERROR,
            reason_code=RunnerReasonCode.INTERNAL_ERROR,
            stage=RunnerStage.EXECUTE,
            error_message="프로그램 실행 환경에서 오류가 발생했습니다.",
        )
    if result.output_limit_exceeded:
        return Classification(
            status=RunnerStatus.BLOCKED,
            reason_code=RunnerReasonCode.OUTPUT_LIMIT,
            stage=RunnerStage.EXECUTE,
            error_message="출력 크기 제한을 초과했습니다.",
        )
    if result.timed_out:
        return Classification(
            status=RunnerStatus.BLOCKED,
            reason_code=RunnerReasonCode.TIME_LIMIT,
            stage=RunnerStage.EXECUTE,
            error_message="실행 시간 제한을 초과했습니다.",
        )
    if result.oom_killed:
        return Classification(
            status=RunnerStatus.BLOCKED,
            reason_code=RunnerReasonCode.MEMORY_LIMIT,
            stage=RunnerStage.EXECUTE,
            error_message="메모리 제한을 초과했습니다.",
        )
    if result.pids_limit_hit:
        return Classification(
            status=RunnerStatus.BLOCKED,
            reason_code=RunnerReasonCode.PROCESS_LIMIT,
            stage=RunnerStage.EXECUTE,
            error_message="프로세스 수 제한을 초과했습니다.",
        )
    if result.exit_code == 0:
        return Classification(
            status=RunnerStatus.SUCCESS,
            reason_code=None,
            stage=None,
            error_message=None,
        )
    return Classification(
        status=RunnerStatus.ERROR,
        reason_code=RunnerReasonCode.RUNTIME_ERROR,
        stage=RunnerStage.EXECUTE,
        error_message="프로그램 실행 중 오류가 발생했습니다.",
    )
