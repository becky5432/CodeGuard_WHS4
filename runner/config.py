import os


class Settings:
    RUNNER_HOST = os.getenv("RUNNER_HOST", "0.0.0.0")
    RUNNER_PORT = int(os.getenv("RUNNER_PORT", "8001"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    WORKSPACE_ROOT = os.getenv(
        "WORKSPACE_ROOT",
        "/tmp/codeguard-runner",
    )

    DOCKER_IMAGE = os.getenv(
        "DOCKER_IMAGE",
        "codeguard-runner:latest",
    )


settings = Settings()
