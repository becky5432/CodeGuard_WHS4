from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "sqlite:///./codeguard.db"

    RUNNER_URL: str = "http://localhost:8001"
    RUNNER_TIMEOUT_SECONDS: float = 30.0 #임시값

    MAX_CODE_SIZE_BYTES: int = 65536 #임시값

    class Config:
        env_file = ".env"


settings = Settings()