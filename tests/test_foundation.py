from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, StatementError
from sqlalchemy.orm import Session
from app.core.config import ROOT, Settings
from app.main import create_app
from app.modules.birds.models import BirdDetection


def settings(tmp_path):
    return Settings(_env_file=None, database_path=tmp_path / "db" / "test.sqlite3")


def detection(**overrides):
    values = dict(timestamp=datetime(2026, 9, 29, 12, tzinfo=timezone(timedelta(hours=2))),
                  scientific_name="Columba livia", common_name="Rotsduif",
                  confidence=0.944, source="test", raw_metadata={"window_seconds": 6})
    return BirdDetection(**(values | overrides))


def test_start_empty_and_restart_persistence(tmp_path):
    config = settings(tmp_path)
    app = create_app(config)
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {
            "status": "ok", "service": "backyard", "database": "ok"}
        assert client.get("/api/birds/detections").json() == []
        with Session(app.state.engine) as session:
            row = detection()
            session.add(row)
            session.commit()
            identity = row.id
    with TestClient(create_app(config)) as client:
        result = client.get("/api/birds/detections").json()
        assert len(result) == 1
        assert result[0]["id"] == identity
        assert result[0]["timestamp"] == "2026-09-29T10:00:00Z"
        assert result[0]["confidence"] == 0.944
        with Session(client.app.state.engine) as session:
            assert session.get(BirdDetection, identity).raw_metadata == {"window_seconds": 6}


@pytest.mark.parametrize("confidence", [-0.1, 1.1])
def test_database_confidence_constraint(tmp_path, confidence):
    with TestClient(create_app(settings(tmp_path))) as client:
        with Session(client.app.state.engine) as session:
            session.add(detection(confidence=confidence))
            with pytest.raises(IntegrityError):
                session.commit()


def test_naive_timestamp_rejected(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        with Session(client.app.state.engine) as session:
            session.add(detection(timestamp=datetime(2026, 9, 29)))
            with pytest.raises(StatementError):
                session.commit()


def test_limit_and_health_failure(tmp_path):
    with TestClient(create_app(settings(tmp_path))) as client:
        for limit in (0, 101):
            assert client.get(f"/api/birds/detections?limit={limit}").status_code == 422
        with patch.object(client.app.state.engine, "connect",
                          side_effect=OperationalError("test", {}, Exception("private"))):
            response = client.get("/api/health")
            assert response.status_code == 503
            assert "private" not in response.text


def test_settings_precedence_and_validation(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("BACKYARD_LOG_LEVEL=WARNING\n", encoding="utf-8")
    assert Settings(_env_file=env).log_level == "WARNING"
    monkeypatch.setenv("BACKYARD_LOG_LEVEL", "ERROR")
    assert Settings(_env_file=env).log_level == "ERROR"
    assert Settings(_env_file=None).resolved_database_path == ROOT / "data/backyard.sqlite3"
    monkeypatch.setenv("BACKYARD_LOG_LEVEL", "INVALID")
    with pytest.raises(ValidationError):
        Settings(_env_file=env)
