class RunnerError(Exception):
    """Runner에서 발생하는 모든 예외의 기본 클래스."""


class ConfigurationError(RunnerError):
    """Runner 설정이 잘못된 경우."""


class WorkspaceError(RunnerError):
    """작업 공간 생성 또는 정리 과정에서 발생한 오류."""


class CompilationError(RunnerError):
    """컴파일 과정에서 발생한 오류."""


class ContainerError(RunnerError):
    """컨테이너 생성 또는 실행 과정에서 발생한 오류."""


class CleanupError(RunnerError):
    """실행 환경 정리 과정에서 발생한 오류."""
