# 작성자: yjm

from pathlib import Path
from tempfile import gettempdir
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

    workspace_root: Path = Path(gettempdir()) / "codeguard-runner"  # 임시 코드 보관 장소
    volume_name_prefix: str = "codeguard-job-"
    cpp_image: str = "codeguard-cpp:dev"
    compile_timeout_seconds: int = Field(default=10, gt=0, le=60)  # 컴파일 제한 시간
    compile_log_limit_bytes: int = Field(default=65_536, gt=0)

    model_config = SettingsConfigDict(  # Pydantic 설정
        case_sensitive=False,
    )


settings = Settings()
