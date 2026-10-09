# 작성자: yjm

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from runner.policies.filesystem import validate_device_paths


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
    filesystem_device_paths: tuple[str, ...] = ()

    @field_validator("filesystem_device_paths", mode="before")
    @classmethod
    def validate_filesystem_devices(cls, value):
        return validate_device_paths(value)

    filesystem_policy_profile: Literal["cpp-amd64-v1"] = "cpp-amd64-v1"
    filesystem_startup_timeout_seconds: float = Field(default=5.0, gt=0, lt=15)
    execution_network: str = "bridge"  # 프리셋 미지정 시 fallback (기존 동작)
    # 프리셋 -> Docker 네트워크 (net-setup.sh 가 만든 이름). 환경변수로 override 가능.
    # 프리셋은 2단계: none=완전 차단(P0), web=확장 허용(P1, 공인 80/443/53).
    execution_network_none: str = "cg-net-p0"   # P0 완전 차단 (egress 0)
    execution_network_web: str = "cg-net-p1"    # P1 확장 허용 (공인 80/443/53)
    # 프리셋별 컨테이너 DNS.
    #   P0(none): 내장 resolver(127.0.0.11)를 제거(resolv.conf=127.0.0.1)하여
    #             이름 해석 자체를 실패시킨다 → DNS 포워딩 경로까지 egress 0.
    #   그 외: None → Docker 기본(내장 resolver) 유지(53 허용과 함께 정상 해석).
    execution_dns_none: list[str] = Field(default_factory=lambda: ["127.0.0.1"])
    execution_cgroup_enabled: bool = True
    execution_cgroup_root: Path = Path("/sys/fs/cgroup/codeguard")
    task_tracker_enabled: bool = True
    task_tracker_socket: Path = Path("/run/codeguard/task-tracker.sock")
    task_tracker_timeout_seconds: float = Field(default=0.2, gt=0)

    def network_for_preset(self, preset) -> str:
        """네트워크 프리셋 이름을 실제 Docker 네트워크 이름으로 변환한다.

        preset 이 None 이면 기존 동작(execution_network)을 그대로 쓴다.
        프리셋 2단계: none → 완전 차단(P0), 그 외 → 확장 허용(P1, 80/443/53).
        """
        if preset is None:
            return self.execution_network
        value = getattr(preset, "value", preset)
        if value == "none":
            return self.execution_network_none
        return self.execution_network_web

    def dns_for_preset(self, preset) -> list[str] | None:
        """프리셋별 컨테이너 DNS를 돌려준다.

        None = Docker 기본(내장 resolver) 유지.
        리스트 = resolv.conf 를 그 값으로 고정(내장 resolver 제거).
        P0(none)만 ["127.0.0.1"] 로 고정하여 DNS 해석 자체를 차단한다.
        """
        value = getattr(preset, "value", preset)
        if value == "none":
            return list(self.execution_dns_none)
        return None

    model_config = SettingsConfigDict(  # Pydantic 설정
        case_sensitive=False,
    )


settings = Settings()
