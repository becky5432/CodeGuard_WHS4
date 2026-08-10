from fastapi import FastAPI

from runner.config import settings


app = FastAPI(
    title="CodeGuard Runner",
    version="0.1.0",
)


@app.get("/health")
def health_check():
    return {
        "status": "ok",
        "service": "runner",
    }
