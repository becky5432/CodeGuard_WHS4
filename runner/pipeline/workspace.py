import shutil
from pathlib import Path
from uuid import UUID

from runner.config import settings
from runner.exceptions import CleanupError, WorkspaceError


SOURCE_FILENAMES = {
    "C": "main.c",
    "CPP": "main.cpp",
}


def create_workspace(job_id: UUID) -> Path:
    """Job 전용 임시 Workspace를 생성한다."""
    workspace = settings.workspace_root / str(job_id)

    try:
        settings.workspace_root.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(exist_ok=False)
        return workspace

    except FileExistsError as exc:
        raise WorkspaceError(
            "이미 존재하는 Job Workspace입니다.",
            details={
                "job_id": str(job_id),
                "path": str(workspace),
            },
        ) from exc

    except OSError as exc:
        raise WorkspaceError(
            "Workspace 생성에 실패했습니다.",
            details={
                "job_id": str(job_id),
                "path": str(workspace),
                "reason": str(exc),
            },
        ) from exc


def write_source(
    workspace: Path,
    language: str,
    code: str,
) -> Path:
    """사용자 코드를 Workspace에 UTF-8 소스 파일로 저장한다."""
    language_value = getattr(language, "value", language)
    filename = SOURCE_FILENAMES.get(language_value)

    if filename is None:
        raise WorkspaceError(
            "지원하지 않는 언어입니다.",
            details={"language": str(language_value)},
        )

    source_path = workspace / filename

    try:
        source_path.write_text(code, encoding="utf-8")
        return source_path

    except OSError as exc:
        raise WorkspaceError(
            "소스 코드 저장에 실패했습니다.",
            details={
                "path": str(source_path),
                "reason": str(exc),
            },
        ) from exc


def remove_workspace(workspace: Path) -> None:
    """허용된 Job Workspace와 내부 파일을 모두 삭제한다."""
    root = settings.workspace_root.resolve()
    target = workspace.resolve()

    if target.parent != root:
        raise CleanupError(
            "삭제할 수 없는 Workspace 경로입니다.",
            details={"path": str(target)},
        )

    try:
        if target.exists():
            shutil.rmtree(target)

    except OSError as exc:
        raise CleanupError(
            "Workspace 삭제에 실패했습니다.",
            details={
                "path": str(target),
                "reason": str(exc),
            },
        ) from exc
