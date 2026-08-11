from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from runner.api.routes import router
from runner.config import settings
from runner.exceptions import RunnerError
from runner.logging_config import configure_logging


logger = configure_logging()


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info(
        "event=startup host=%s port=%s",
        settings.runner_host,
        settings.runner_port,
    )
    yield
    logger.info("event=shutdown")


app = FastAPI(
    title="CodeGuard Runner",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.exception_handler(RunnerError)
async def runner_exception_handler(
    request: Request,
    exc: RunnerError,
) -> JSONResponse:
    logger.error(
        "event=runner_error code=%s path=%s message=%s",
        exc.error_code,
        request.url.path,
        exc.message,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status": "error",
            "error_code": exc.error_code,
            "message": exc.message,
            "details": exc.details,
        },
    )
