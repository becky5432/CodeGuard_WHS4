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
    # 실행 컨테이너의 네트워크 설정.
    #  "none"  = 네트워크 완전 차단 (기본값, 가장 안전)
    #  "<이름>" = 커스텀 도커 네트워크에 연결 (egress 규칙이 적용된 네트워크)
    # 환경변수 EXECUTION_NETWORK 로도 지정 가능 (예: EXECUTION_NETWORK=lab-net)
    execution_network: str = "none"
    execution_cgroup_enabled: bool = True
    execution_cgroup_root: Path = Path("/sys/fs/cgroup/codeguard")

    model_config = SettingsConfigDict(  # Pydantic 설정
        case_sensitive=False,
    )


settings = Settings()
