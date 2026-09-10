from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./codeguard.db"

    RUNNER_URL: str = "http://localhost:8001"
    RUNNER_TIMEOUT_SECONDS: float = 30.0 #임시값

    MAX_CODE_SIZE_BYTES: int = 65536 #임시값
    MAX_SAVED_OUTPUT_BYTES: int = 65536  # DB 저장용 출력 상한

    class Config:
        env_file = ".env"


settings = Settings()

 
# 기본 정책 설정 (임시값이고 구체적인 값은 안 정해짐)
DEFAULT_POLICY = {
    "timeout_ms": 2000,
    "memory_limit_mb": 128,
    "pids_limit": 32,
    "cpu_limit": 1.0,
    "output_limit_bytes": 1048576,
}