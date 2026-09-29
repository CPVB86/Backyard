import sqlite3
from contextlib import closing
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.core.migrations import migrate
from app.main import create_app
from app.modules.birds.models import BirdDetection

FOUNDATION_SQL = """
CREATE TABLE bird_detections (
    id VARCHAR(36) NOT NULL PRIMARY KEY, timestamp DATETIME NOT NULL,
    scientific_name VARCHAR(255) NOT NULL, common_name VARCHAR(255),
    confidence FLOAT NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
    source VARCHAR(255) NOT NULL, audio_reference VARCHAR(2048),
    model_version VARCHAR(100), raw_metadata JSON, created_at DATETIME NOT NULL
);
CREATE INDEX ix_birds_timestamp ON bird_detections(timestamp);
CREATE INDEX ix_birds_species_timestamp ON bird_detections(scientific_name, timestamp);
"""


def legacy(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    identity = str(uuid4())
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(FOUNDATION_SQL)
        connection.execute(
            "INSERT INTO bird_detections VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (identity, "2026-09-29 10:00:00.000000", "Columba livia", "Rotsduif", 0.944,
             "legacy", "/private/original.wav", "v3", '{"original": true}',
             "2026-09-29 11:00:00.000000"),
        )
        connection.commit()
    config = Settings(_env_file=None, database_path=path, storage_root=tmp_path / "audio")
    return config, identity


def test_legacy_requires_explicit_migration_then_preserves_data(tmp_path):
    config, identity = legacy(tmp_path)
    engine = create_database(config)
    try:
        with pytest.raises(RuntimeError, match="migration"):
            initialize_database(engine)
        backup = migrate(engine, config.resolved_database_path)
        assert backup.is_file()
        with closing(sqlite3.connect(backup)) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
            assert connection.execute("SELECT id FROM bird_detections").fetchone()[0] == identity
        assert migrate(engine, config.resolved_database_path) is None
        with Session(engine) as session:
            record = session.get(BirdDetection, identity)
            assert record.audio_reference == "/private/original.wav"
            assert record.raw_metadata == {"original": True}
            assert record.verification_status == "unreviewed"
            assert record.event_id is None
    finally:
        engine.dispose()
    with TestClient(create_app(config)) as client:
        item = client.get(f"/api/birds/detections/{identity}").json()
        assert item["id"] == identity and item["audio_url"] is None
        assert "/private" not in str(item)
        assert client.get("/api/health").status_code == 200


def test_migration_ddl_rolls_back_on_failure(tmp_path):
    config, _ = legacy(tmp_path)
    engine = create_database(config)
    def broken(connection):
        connection.exec_driver_sql("ALTER TABLE bird_detections ADD COLUMN accidental TEXT")
        raise RuntimeError("simulate interrupted migration")
    try:
        with patch("app.core.migrations.upgrade_0_to_1", side_effect=broken):
            with pytest.raises(RuntimeError, match="interrupted"):
                migrate(engine, config.resolved_database_path)
        assert "accidental" not in {c["name"] for c in inspect(engine).get_columns("bird_detections")}
        with engine.connect() as connection:
            assert connection.execute(text("PRAGMA user_version")).scalar_one() == 0
        assert migrate(engine, config.resolved_database_path) is not None
    finally:
        engine.dispose()


def test_unknown_schema_and_future_version_refused(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "unknown.sqlite3")
    engine = create_database(config)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE important_data (id INTEGER)")
        with pytest.raises(Exception):
            migrate(engine, config.resolved_database_path)
        assert inspect(engine).has_table("important_data")
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version = 999")
        with pytest.raises(RuntimeError):
            initialize_database(engine)
        with pytest.raises(RuntimeError):
            migrate(engine, config.resolved_database_path)
    finally:
        engine.dispose()


def test_empty_database_migration(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "new.sqlite3")
    engine = create_database(config)
    try:
        assert migrate(engine, config.resolved_database_path) is None
        assert inspect(engine).has_table("bird_audio")
        initialize_database(engine)
    finally:
        engine.dispose()
