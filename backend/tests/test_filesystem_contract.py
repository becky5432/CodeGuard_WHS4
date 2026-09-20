"""Runner evidence classification survives real DB storage and the lookup API."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.executions import router
from app.db import repository
from app.db.database import Base, get_db
from app.schemas.execution_schema import ExecutionCreateRequest, ExecutionReasonCode
from app.schemas.runner_schema import RunnerResponse as BackendRunnerResponse
from app.services.execution_service import ExecutionService
from runner.models.result import (
    RunnerReasonCode, RunnerResponse, RunnerStage, RunnerStatus, StageError, StageSummary,
)


def test_filesystem_limit_runner_to_db_to_lookup_api():
    engine = create_engine(
        'sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    runner_client = Mock()
    service = ExecutionService(runner_client)
    try:
        with sessions() as db:
            created, request = service.submit(
                ExecutionCreateRequest(language='C', code='int main(){return 0;}', policy_profile='basic'),
                db,
            )
        response = RunnerResponse(
            job_id=created.job_id, run_id=uuid4(), status=RunnerStatus.BLOCKED,
            reason_code=RunnerReasonCode.FILESYSTEM_LIMIT, exit_code=0,
            stage_summary=StageSummary(
                failed=[RunnerStage.EXECUTE],
                errors={RunnerStage.EXECUTE: [StageError(
                    reason_code=RunnerReasonCode.FILESYSTEM_LIMIT, message='RO write detected',
                )]},
            ),
            finished_at=datetime.now(timezone.utc),
        )
        runner_client.execute.return_value = BackendRunnerResponse.model_validate(
            response.model_dump(mode='json'),
        )
        with patch('app.services.execution_service.SessionLocal', sessions):
            service.process_execution(request)
        with sessions() as db:
            saved = repository.get_execution(db, str(created.job_id))
            assert saved.reason_code == ExecutionReasonCode.FILESYSTEM_LIMIT.value
            assert saved.exit_code == 0

        def test_db():
            with sessions() as db:
                yield db

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = test_db
        with TestClient(app) as client:
            result = client.get(f'/executions/{created.job_id}')
        assert result.status_code == 200
        payload = result.json()
        assert payload['status'] == 'BLOCKED'
        assert payload['reason_code'] == 'FILESYSTEM_LIMIT'
        assert payload['exit_code'] == 0
        assert payload['stage_summary']['errors']['EXECUTE'][0]['reason_code'] == 'FILESYSTEM_LIMIT'
    finally:
        engine.dispose()
