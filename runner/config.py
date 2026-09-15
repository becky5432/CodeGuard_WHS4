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
    execution_cgroup_root: Path = Path("/sys/fs/cgroup/codeguard")

    model_config = SettingsConfigDict(  # Pydantic 설정
        case_sensitive=False,
    )


settings = Settings()
