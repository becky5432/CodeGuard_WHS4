from fastapi import FastAPI

from app.api.executions import router as executions_router


app = FastAPI(
    title="CodeGuard Backend",
    version="0.1.0",
)

app.include_router(executions_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}