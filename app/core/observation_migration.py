"""Frozen additive schema migration 1 -> 2. Never edits historical detections."""
from sqlalchemy import inspect

STATEMENTS = (
    """CREATE TABLE observations (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        source VARCHAR(255) NOT NULL, event_id VARCHAR(255) NOT NULL,
        ingest_hash VARCHAR(64) NOT NULL,
        domain VARCHAR(16) NOT NULL CHECK(domain IN ('bird','bat')),
        scientific_name VARCHAR(255) NOT NULL, common_name VARCHAR(255) NOT NULL,
        start_at DATETIME NOT NULL, end_at DATETIME NOT NULL,
        best_confidence FLOAT NOT NULL,
        status VARCHAR(32) NOT NULL CHECK(status IN
            ('auto_accepted','pending_review','review_recommended','human_confirmed','human_rejected')),
        decision JSON NOT NULL, policy JSON NOT NULL, clip JSON NOT NULL,
        evidence_kind VARCHAR(32) NOT NULL CHECK(evidence_kind IN
            ('permanent','review','delete_pending','deleted')),
        audio JSON, storage_key VARCHAR(255), stale_key VARCHAR(255),
        review_due_at DATETIME, cleanup_after DATETIME, review JSON, created_at DATETIME NOT NULL
    )""",
    "CREATE UNIQUE INDEX uq_observation_source_event ON observations(source,event_id)",
    "CREATE INDEX ix_observations_review ON observations(status,start_at)",
    "CREATE INDEX ix_observations_domain_time ON observations(domain,start_at)",
    """CREATE TABLE observation_candidates (
        id VARCHAR(36) NOT NULL PRIMARY KEY,
        observation_id VARCHAR(36) NOT NULL REFERENCES observations(id),
        source VARCHAR(255) NOT NULL, candidate_id VARCHAR(255) NOT NULL,
        ordinal INTEGER NOT NULL, raw JSON NOT NULL
    )""",
    "CREATE UNIQUE INDEX uq_candidate_source_event ON observation_candidates(source,candidate_id)",
    "CREATE INDEX ix_observation_candidates_observation_id ON observation_candidates(observation_id)",
)


def validate_v1(connection):
    tables = set(inspect(connection).get_table_names())
    if tables != {"bird_detections", "bird_audio"}:
        raise RuntimeError("Unknown version-1 database tables; migration refused")
    expected = {
        "bird_detections": {"id","timestamp","scientific_name","common_name","confidence","source",
            "audio_reference","model_version","raw_metadata","created_at","event_id","source_version",
            "ingest_hash","verification_status"},
        "bird_audio": {"detection_id","storage_key","content_type","codec","sample_rate","channels",
            "sample_width","duration_seconds","size_bytes","sha256","created_at","status"},
    }
    for name, columns in expected.items():
        if {c["name"] for c in inspect(connection).get_columns(name)} != columns:
            raise RuntimeError("Unknown version-1 columns; migration refused")


def upgrade_1_to_2(connection):
    validate_v1(connection)
    for statement in STATEMENTS:
        connection.exec_driver_sql(statement)
