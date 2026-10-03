"""Versioned SQLite migrations. No deletion/recreation of existing databases."""
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import inspect

from app.core.observation_migration import upgrade_1_to_2, validate_v1

SCHEMA_VERSION = 3
FOUNDATION_COLUMNS = {
    "id", "timestamp", "scientific_name", "common_name", "confidence",
    "source", "audio_reference", "model_version", "raw_metadata", "created_at",
}


def version(connection):
    return connection.exec_driver_sql("PRAGMA user_version").scalar_one()


def validate_foundation(connection):
    tables = set(inspect(connection).get_table_names())
    if tables != {"bird_detections"}:
        raise RuntimeError("Unknown database schema; migration refused without changing data")
    columns = {c["name"] for c in inspect(connection).get_columns("bird_detections")}
    if tables != {"bird_detections"} or columns != FOUNDATION_COLUMNS:
        raise RuntimeError("Unknown database schema; migration refused without changing data")


def upgrade_0_to_1(connection):
    # Fixed migration SQL: do not change when future model versions are added.
    for definition in (
        "event_id VARCHAR(255)",
        "source_version VARCHAR(100)",
        "ingest_hash VARCHAR(64)",
        "verification_status VARCHAR(20) NOT NULL DEFAULT 'unreviewed' "
        "CHECK (verification_status IN ('unreviewed', 'confirmed', 'rejected'))",
    ):
        connection.exec_driver_sql("ALTER TABLE bird_detections ADD COLUMN " + definition)
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX uq_birds_source_event ON bird_detections (source, event_id)"
    )
    connection.exec_driver_sql("""
        CREATE TABLE bird_audio (
            detection_id VARCHAR(36) NOT NULL PRIMARY KEY
                REFERENCES bird_detections(id),
            storage_key VARCHAR(255) NOT NULL UNIQUE,
            content_type VARCHAR(100) NOT NULL,
            codec VARCHAR(30) NOT NULL,
            sample_rate INTEGER NOT NULL,
            channels INTEGER NOT NULL,
            sample_width INTEGER NOT NULL,
            duration_seconds FLOAT NOT NULL,
            size_bytes INTEGER NOT NULL,
            sha256 VARCHAR(64) NOT NULL,
            created_at DATETIME NOT NULL,
            status VARCHAR(20) NOT NULL DEFAULT 'available'
        )
    """)


def upgrade_2_to_3(connection):
    connection.exec_driver_sql("""
        CREATE TABLE species_catalog (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            domain VARCHAR(16) NOT NULL CHECK (domain IN ('bird','bat')),
            scientific_name VARCHAR(255) NOT NULL,
            common_name_nl VARCHAR(255), authority VARCHAR(255), family VARCHAR(255),
            taxon_type VARCHAR(100), taxon_group VARCHAR(100),
            parent_source_species_id INTEGER, source VARCHAR(50) NOT NULL,
            source_species_id INTEGER NOT NULL, source_url VARCHAR(2048),
            rarity VARCHAR(100), status VARCHAR(100), obscurity VARCHAR(255),
            source_metadata JSON NOT NULL,
            wikipedia_nl_url VARCHAR(2048), summary_nl TEXT, fact_nl TEXT,
            encyclopedia_source VARCHAR(255), imported_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """)
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX uq_species_identity ON species_catalog (domain, scientific_name)")
    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX uq_species_source_id ON species_catalog (source, source_species_id)")


def initialize_or_check(engine):
    from app.core.database import Base
    from app.modules.birds import models  # noqa: F401
    from app.modules.observations import models as observation_models  # noqa: F401
    from app.modules.species import models as species_models  # noqa: F401
    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            current = version(connection)
            tables = inspect(connection).get_table_names()
            if current == 0 and not tables:
                Base.metadata.create_all(connection)
                connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif current != SCHEMA_VERSION:
                raise RuntimeError(
                    "Database migration required: stop the API and run python -m app.core.migrate"
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def migrate(engine, database_path: Path):
    """Back up a known legacy DB, then atomically apply all pending schema versions."""
    with engine.connect() as connection:
        current = version(connection)
        if current == SCHEMA_VERSION:
            return None
        if current not in (0, 1, 2):
            raise RuntimeError("Unsupported database version; refusing migration")
        if not inspect(connection).get_table_names():
            connection.rollback()
            initialize_or_check(engine)
            return None
        if current == 0:
            validate_foundation(connection)
        elif current == 1:
            validate_v1(connection)

    # API/producers must be stopped. SQLite backup also handles committed WAL data.
    suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = database_path.with_name(database_path.name + f".backup-{suffix}-{uuid4().hex}")
    with closing(sqlite3.connect(database_path)) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)

    with engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            if version(connection) != current:
                raise RuntimeError("Database changed during migration; retry after stopping writers")
            if current == 0:
                validate_foundation(connection)
                upgrade_0_to_1(connection)
            if current in (0, 1):
                upgrade_1_to_2(connection)
            upgrade_2_to_3(connection)
            connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    return backup
