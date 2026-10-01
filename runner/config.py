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
    execution_network: str = "bridge"  # 프리셋 미지정 시 fallback (기존 동작)
    # 프리셋 -> Docker 네트워크 (net-setup.sh 가 만든 이름). 환경변수로 override 가능.
    execution_network_none: str = "cg-net-p0"   # P0 완전 차단
    execution_network_https: str = "cg-net-p1"  # P1 HTTPS 전용
    execution_network_web: str = "cg-net-p2"    # P2 웹 개방
    execution_cgroup_enabled: bool = True
    execution_cgroup_root: Path = Path("/sys/fs/cgroup/codeguard")
    task_tracker_enabled: bool = True
    task_tracker_socket: Path = Path("/run/codeguard/task-tracker.sock")
    task_tracker_timeout_seconds: float = Field(default=0.2, gt=0)

    def network_for_preset(self, preset) -> str:
        """네트워크 프리셋 이름을 실제 Docker 네트워크 이름으로 변환한다.

        preset 이 None 이면 기존 동작(execution_network)을 그대로 쓴다.
        """
        if preset is None:
            return self.execution_network
        value = getattr(preset, "value", preset)
        mapping = {
            "none": self.execution_network_none,
            "https": self.execution_network_https,
            "web": self.execution_network_web,
        }
        return mapping.get(value, self.execution_network)

    model_config = SettingsConfigDict(  # Pydantic 설정
        case_sensitive=False,
    )


settings = Settings()
