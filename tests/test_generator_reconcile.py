from datetime import datetime, timezone
import hashlib
import json
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.modules.observations.models import Observation
from generator.reconcile import reconcile, main
from generator.scheduler import Scheduler
from generator.store import AssetStore
from generator.domains.birds import openai_images
from test_generator import png


@pytest.fixture
def setup(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path/"observations.sqlite3", storage_root=tmp_path)
    engine = create_database(settings)
    initialize_database(engine)
    scheduler = Scheduler(AssetStore(tmp_path/"generator"))
    yield engine, scheduler, settings
    engine.dispose()


def add(engine, name="Columba palumbus", status="auto_accepted", domain="bird", event="one"):
    with Session(engine) as session:
        now = datetime.now(timezone.utc)
        row = Observation(domain=domain, scientific_name=name, common_name="Houtduif",
                          source="test", event_id=event, ingest_hash="0"*64, status=status,
                          start_at=now, end_at=now, best_confidence=.95, decision={}, policy={},
                          clip={}, evidence_kind="permanent")
        session.add(row)
        session.commit()


@pytest.mark.parametrize("status", ["auto_accepted", "human_confirmed"])
def test_existing_accepted_missing_is_pending_without_mutation(setup, status):
    engine, worker, settings = setup
    add(engine, status=status)
    before = settings.resolved_database_path.read_bytes()
    result = reconcile(engine, worker, domain="bird", scientific_name="Columba palumbus")
    assert result[0]["generation"]["status"] == "generation_pending"
    assert before == settings.resolved_database_path.read_bytes()
    assert worker.thread is None


def test_ready_species_no_job(setup):
    engine, worker, _ = setup
    add(engine, name="Turdus migratorius")
    result = reconcile(engine, worker)
    assert result[0]["action"] == "already_ready"
    assert worker.jobs.get("bird", "Turdus migratorius") is None


@pytest.mark.parametrize("status", ["pending_review", "human_rejected", "review_recommended"])
def test_unaccepted_not_selected(setup, status):
    engine, worker, _ = setup
    add(engine, status=status)
    assert reconcile(engine, worker) == []
    assert worker.jobs.get("bird", "Columba palumbus") is None
    with pytest.raises(ValueError, match="No existing accepted"):
        reconcile(engine, worker, domain="bird", scientific_name="Columba palumbus")


def test_duplicates_and_repeat_use_one_existing_queue_job(setup, monkeypatch):
    engine, worker, _ = setup
    add(engine)
    add(engine, event="two", status="human_confirmed")
    paid = Mock(return_value=png())
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_images, "generate_png", paid)
    for _ in range(2):
        assert len(reconcile(engine, worker)) == 1
    paid.assert_not_called()
    with worker.jobs.connect() as db:
        assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert worker.run_one()
    assert paid.call_count == 2  # One species job; two missing bird poses.
    assert reconcile(engine, worker)[0]["action"] == "already_ready"
    assert not worker.run_one()
    assert paid.call_count == 2


def test_existing_paid_attempt_not_reset_or_retried(setup, monkeypatch):
    engine, worker, _ = setup
    add(engine)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paid = Mock(side_effect=RuntimeError("failed"))
    monkeypatch.setattr(openai_images, "generate_png", paid)
    with pytest.raises(RuntimeError):
        worker.store.ensure("bird", "Columba palumbus", "Houtduif")
    attempt = worker.store.directory("bird", "Columba palumbus")/"state.json"
    before = attempt.read_bytes()
    reconcile(engine, worker)
    worker.run_one()
    reconcile(engine, worker)
    assert not worker.run_one()
    assert attempt.read_bytes() == before
    paid.assert_called_once()
    assert worker.jobs.get("bird", "Columba palumbus")["status"] == "generation_failed"


def test_reconcile_does_not_recover_or_reset_live_job(setup, monkeypatch):
    engine, worker, _ = setup
    add(engine)
    reconcile(engine, worker)
    worker.jobs.claim()
    monkeypatch.setattr(worker.jobs, "recover", Mock(side_effect=AssertionError("Must not recover live worker")))
    reconcile(engine, worker)
    assert worker.jobs.get("bird", "Columba palumbus")["status"] == "generation_running"


def test_targeted_cli_only_woodpigeon_readonly_database(setup, monkeypatch, capsys, tmp_path):
    engine, worker, settings = setup
    add(engine)
    add(engine, name="Testus other", event="other")
    env = tmp_path/"production.env"
    env.write_text(f"BACKYARD_DATABASE_PATH={settings.resolved_database_path.as_posix()}\n"
                   f"BACKYARD_STORAGE_ROOT={tmp_path.as_posix()}\n", encoding="utf-8")
    monkeypatch.setattr(Scheduler, "start", Mock(side_effect=AssertionError("CLI must not start another worker")))
    monkeypatch.setattr(openai_images, "generate_png", Mock(side_effect=AssertionError("CLI must not generate")))
    before = hashlib.sha256(settings.resolved_database_path.read_bytes()).digest()
    main(["--environment-file", str(env), "--domain", "bird", "--scientific-name", "Columba palumbus"])
    result = json.loads(capsys.readouterr().out)
    assert [row["scientific_name"] for row in result["species"]] == ["Columba palumbus"]
    assert worker.jobs.get("bird", "Columba palumbus")["status"] == "generation_pending"
    assert worker.jobs.get("bird", "Testus other") is None
    assert hashlib.sha256(settings.resolved_database_path.read_bytes()).digest() == before


def test_domain_separation(setup):
    engine, worker, _ = setup
    add(engine)
    add(engine, domain="bat", event="bat")
    reconcile(engine, worker, domain="bird", scientific_name="Columba palumbus")
    assert worker.jobs.get("bat", "Columba palumbus") is None


def test_scheduling_failure_reported_without_observation_mutation(setup, monkeypatch):
    engine, worker, settings = setup
    add(engine)
    monkeypatch.setattr(worker.jobs, "enqueue", Mock(side_effect=OSError("failure")))
    before = settings.resolved_database_path.read_bytes()
    with pytest.raises(RuntimeError, match="scheduling failed"):
        reconcile(engine, worker)
    assert settings.resolved_database_path.read_bytes() == before
