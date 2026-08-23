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

 
# 정책 프리셋 추가 (값은 확정된 거 아니고 임시로 작성)
POLICY_PRESETS = {
    "basic": {
        "timeout_ms": 1000,
        "memory_limit_mb": 64,
        "pids_limit": 32,
        "cpu_limit": 1.0,
    },
    "strict": {
        "timeout_ms": 500,
        "memory_limit_mb": 32,
        "pids_limit": 8,
        "cpu_limit": 0.5,
    },
    "relaxed": {
        "timeout_ms": 3000, 
        "memory_limit_mb": 256,
        "pids_limit": 64,
        "cpu_limit": 2.0,
    },
}