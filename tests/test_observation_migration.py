from datetime import datetime, timezone
import io
import sqlite3
from contextlib import closing
from unittest.mock import patch
import wave
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.core.migrations import migrate
from app.core.observation_migration import STATEMENTS
from app.main import create_app
from app.modules.birds.models import BirdDetection, BirdAudio
from app.modules.birds.schemas import DetectionInput
from app.modules.birds.service import ingest, attach_audio


def existing_v1(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "old.sqlite3", storage_root=tmp_path / "audio")
    engine = create_database(settings)
    with engine.begin() as connection:
        BirdDetection.__table__.create(connection)
        BirdAudio.__table__.create(connection)
        connection.exec_driver_sql("PRAGMA user_version=1")
    payload = DetectionInput(
        event_id="historical-chimp", detected_at="2026-09-30T12:00:00Z",
        scientific_name="Pan troglodytes", common_name="Chimpanzee",
        confidence=.68, source="backyard-birdnet-monitor",
    )
    record, _ = ingest(engine, payload)
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(8000)
        output.writeframes(bytes(1600))
    audio = stream.getvalue()
    attach_audio(engine, settings, record.id, audio)
    return engine, settings, record.id, audio


def test_upgrade_v1_preserves_historical_chimp_record_audio_and_backup(tmp_path):
    engine, settings, identity, audio = existing_v1(tmp_path)
    try:
        with pytest.raises(RuntimeError, match="migration"):
            initialize_database(engine)
        before_files = {str(p): p.read_bytes() for p in settings.resolved_storage_root.rglob("*.wav")}
        backup = migrate(engine, settings.resolved_database_path)
        assert backup.is_file()
        with closing(sqlite3.connect(backup)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            assert connection.execute("SELECT scientific_name FROM bird_detections").fetchone()[0] == "Pan troglodytes"
        assert "observations" in inspect(engine).get_table_names()
        assert migrate(engine, settings.resolved_database_path) is None
        assert {str(p): p.read_bytes() for p in settings.resolved_storage_root.rglob("*.wav")} == before_files
    finally:
        engine.dispose()
    with TestClient(create_app(settings)) as client:
        record = client.get(f"/api/birds/detections/{identity}").json()
        assert record["scientific_name"] == "Pan troglodytes"
        assert client.get(record["audio_url"]).content == audio
        assert client.get("/api/observations").json() == []


def test_v1_to_v2_ddl_failure_rolls_back_without_losing_old_audio(tmp_path):
    engine, settings, identity, audio = existing_v1(tmp_path)
    def fail(connection):
        connection.exec_driver_sql(STATEMENTS[0])
        raise RuntimeError("injected migration interruption")
    try:
        with patch("app.core.migrations.upgrade_1_to_2", side_effect=fail):
            with pytest.raises(RuntimeError, match="interruption"):
                migrate(engine, settings.resolved_database_path)
        assert set(inspect(engine).get_table_names()) == {"bird_detections", "bird_audio"}
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 1
        assert next(settings.resolved_storage_root.rglob("*.wav")).read_bytes() == audio
        assert migrate(engine, settings.resolved_database_path)
    finally:
        engine.dispose()
