# 작성자: yjm

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    runner_host: str = "0.0.0.0"
    runner_port: int = Field(default=8001, ge=1, le=65535)

    log_level: Literal[
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    ] = "INFO"

    volume_name_prefix: str = "codeguard-job-"
    cpp_image: str = "codeguard-cpp:dev"
    execution_cgroup_enabled: bool = True
    execution_cgroup_root: Path = Path("/sys/fs/cgroup/codeguard")
    task_tracker_enabled: bool = True
    task_tracker_socket: Path = Path("/run/codeguard/task-tracker.sock")
    task_tracker_timeout_seconds: float = Field(default=0.2, gt=0)

    model_config = SettingsConfigDict(  # Pydantic 설정
        case_sensitive=False,
    )


settings = Settings()
