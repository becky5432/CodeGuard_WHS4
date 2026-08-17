from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.executions import router as executions_router
from app.db import models          # 모델 등록용 (이 import가 있어야 테이블이 인식됩니다)
from app.db.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="CodeGuard Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(executions_router)


@app.get("/health")
def health_check():
    return {"status": "ok"}