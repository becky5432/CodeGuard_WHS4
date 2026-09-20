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

 
DEFAULT_POLICY = {
    "timeout_ms": 2000,
    "memory_limit_mb": 128,
    "pids_limit": 32,
    "cpu_bandwidth": 1.0,
    "cpu_time_limit_ms": 1000,
    "output_limit_bytes": 1048576,
}
