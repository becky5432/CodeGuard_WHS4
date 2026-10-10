"""Runner evidence classification survives real DB storage and the lookup API."""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.executions import router
from app.clients.runner_client import HttpRunnerClient
from app.db import repository
from app.db.models import Execution
from app.db.database import Base, get_db
from app.schemas.execution_schema import ExecutionCreateRequest, ExecutionReasonCode
from app.schemas.runner_schema import RunnerResponse as BackendRunnerResponse
from app.services.execution_service import ExecutionService
from runner.models.result import (
    RunnerReasonCode, RunnerResponse, RunnerStage, RunnerStatus, StageSummary,
)


@pytest.mark.parametrize('violation_names', [
    [], ['FILESYSTEM_LIMIT'], ['NETWORK_BLOCKED'],
    ['FILESYSTEM_LIMIT', 'NETWORK_BLOCKED'],
])
def test_policy_violations_runner_to_db_to_lookup_api(violation_names):
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
            job_id=created.job_id, run_id=uuid4(), status=RunnerStatus.SUCCESS,
            reason_code=None, exit_code=0,
            policy_violations=[RunnerReasonCode(name) for name in violation_names],
            stage_summary=StageSummary(succeeded=[RunnerStage.EXECUTE]),
            finished_at=datetime.now(timezone.utc),
        )
        runner_client.execute.return_value = BackendRunnerResponse.model_validate(
            response.model_dump(mode='json'),
        )
        with patch('app.services.execution_service.SessionLocal', sessions):
            service.process_execution(request)
        with sessions() as db:
            saved = repository.get_execution(db, str(created.job_id))
            assert saved.status == 'SUCCESS'
            assert saved.reason_code is None
            assert saved.policy_violations == violation_names
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
        assert payload['status'] == 'SUCCESS'
        assert payload['reason_code'] is None
        assert payload['policy_violations'] == violation_names
        assert payload['exit_code'] == 0
        assert payload['stage_summary']['succeeded'] == ['EXECUTE']
    finally:
        engine.dispose()


def test_http_runner_client_preserves_policy_violations():
    response = RunnerResponse(
        job_id=uuid4(), run_id=uuid4(), status=RunnerStatus.SUCCESS,
        policy_violations=[RunnerReasonCode.NETWORK_BLOCKED, RunnerReasonCode.FILESYSTEM_LIMIT],
        exit_code=0, stage_summary=StageSummary(), finished_at=datetime.now(timezone.utc),
    )
    http_response = Mock()
    http_response.json.return_value = response.model_dump(mode='json')
    with patch('app.clients.runner_client.httpx.post', return_value=http_response):
        result = HttpRunnerClient('http://runner').execute(Mock(
            model_dump=Mock(return_value={}),
        ))
    assert [value.value for value in result.policy_violations] == [
        'NETWORK_BLOCKED', 'FILESYSTEM_LIMIT',
    ]


def test_security_verification_reason_round_trips_string_column():
    assert Execution.__table__.c.reason_code.type.length == 32
    reason = ExecutionReasonCode.SECURITY_VERIFICATION_FAILED.value
    assert len(reason) <= 32
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    job_id = str(uuid4())
    limits = {
        "timeout_ms": 3000, "memory_limit_mb": 128,
        "pids_limit": 32, "cpu_bandwidth": 1.0,
        "cpu_time_limit_ms": 2000,
        "output_limit_bytes": 1024,
    }
    try:
        with sessions() as db:
            repository.create_execution(
                db, job_id, "C", "int main(void){return 0;}", "", limits,
            )
            repository.save_result(
                db, job_id, status="ERROR", reason_code=reason,
            )
            saved = repository.get_execution(db, job_id)
            assert saved.reason_code == reason
    finally:
        engine.dispose()
