from datetime import datetime, timezone
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.executions import router
from app.db.database import Base, get_db
from app.db import repository
from app.schemas.execution_schema import (
    ExecutionCreateRequest,
    Language,
    MemoryUsageSample,
    PolicyLimits,
)
from app.schemas.runner_schema import (
    ResourceUsage,
    RunnerResponse,
    RunnerStatus,
    StageSummary,
)
from app.services.execution_service import ExecutionService


def test_memory_samples_round_trip_runner_db_and_lookup_api():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    runner_client = Mock()
    service = ExecutionService(runner_client)

    try:
        with sessions() as db:
            created, request = service.submit(
                ExecutionCreateRequest(
                    language=Language.C,
                    code="int main(void) { return 0; }",
                    policy=PolicyLimits(
                        timeout_ms=3000,
                        memory_limit_mb=128,
                        pids_limit=32,
                        cpu_bandwidth=1.0,
                        cpu_time_limit_ms=2000,
                        output_limit_bytes=65536,
                    ),
                ),
                db,
            )

        runner_client.execute.return_value = RunnerResponse(
            job_id=created.job_id,
            run_id=uuid4(),
            status=RunnerStatus.SUCCESS,
            resource_usage=ResourceUsage(
                memory_peak_bytes=52_428_800,
                memory_usage_samples=[
                    MemoryUsageSample(
                        elapsed_ms=100,
                        memory_bytes=10_485_760,
                    ),
                    MemoryUsageSample(
                        elapsed_ms=200,
                        memory_bytes=20_971_520,
                    ),
                ],
            ),
            stage_summary=StageSummary(),
            finished_at=datetime.now(timezone.utc),
        )

        with patch("app.services.execution_service.SessionLocal", sessions):
            service.process_execution(request)

        with sessions() as db:
            saved = repository.get_execution(db, str(created.job_id))
            assert saved.memory_peak_bytes == 52_428_800
            assert saved.memory_usage_samples == [
                {"elapsed_ms": 100, "memory_bytes": 10_485_760},
                {"elapsed_ms": 200, "memory_bytes": 20_971_520},
            ]

        def override_db():
            with sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = override_db
        with TestClient(app) as client:
            response = client.get(f"/executions/{created.job_id}")

        assert response.status_code == 200
        assert response.json()["resource_usage"] == {
            "wall_time_ms": None,
            "cpu_time_ms": None,
            "memory_peak_bytes": 52_428_800,
            "pids_peak": None,
            "output_bytes": None,
            "cpu_usage_samples": None,
            "memory_usage_samples": [
                {"elapsed_ms": 100, "memory_bytes": 10_485_760},
                {"elapsed_ms": 200, "memory_bytes": 20_971_520},
            ],
            "user_task_peak": None,
            "process_at_user_task_peak": None,
            "thread_at_user_task_peak": None,
        }
    finally:
        engine.dispose()
