from typing import Any


class RunnerError(Exception):
    """Runner 내부에서 발생하는 모든 사용자 정의 예외의 부모 클래스."""

    error_code = "RUNNER_ERROR"
    status_code = 500

    def __init__(
        self,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class WorkspaceError(RunnerError):
    """임시 작업공간 생성, 파일 저장 또는 삭제 실패."""

    error_code = "WORKSPACE_ERROR"
    status_code = 500


class DockerUnavailableError(RunnerError):
    """Docker Engine에 연결할 수 없는 경우."""

    error_code = "DOCKER_UNAVAILABLE"
    status_code = 503


class ContainerExecutionError(RunnerError):
    """컨테이너 생성 또는 실행에 실패한 경우."""

    error_code = "CONTAINER_EXECUTION_ERROR"
    status_code = 500


class CleanupError(RunnerError):
    """컨테이너 또는 작업공간 정리에 실패한 경우."""

    error_code = "CLEANUP_ERROR"
    status_code = 500


class CgroupScopeError(RunnerError):
    """Execution 전용 cgroup의 생성 또는 정리에 실패한 경우."""

    error_code = "CGROUP_SCOPE_ERROR"
    status_code = 500
