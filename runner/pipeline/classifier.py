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
