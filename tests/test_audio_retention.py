from datetime import datetime, timedelta, timezone
import io
from pathlib import Path
from unittest.mock import patch
import wave

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.observations.models import Observation
from app.modules.observations.retention import reconcile, disk_bytes
from observations.policy import Policy, RawCandidate, observation_payload


@pytest.fixture
def api(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "db.sqlite3",
                        storage_root=tmp_path / "audio", audio_retention_enabled=True,
                        audio_min_free_bytes=0)
    with TestClient(create_app(settings), headers={"Authorization": "Bearer backyard-test-token"}) as client:
        yield client


def create(api, identity, *, when=None, score=.94, name="Parus major", domain="bird"):
    when = when or datetime.now(timezone.utc)
    raw = RawCandidate(identity, identity, "retention-test", identity, domain,
                       name, name, score, 8000, 0, 24000, when.isoformat(),
                       "synthetic", {"state": "normal", "provider": "test"}, {})
    response = api.post("/api/observations", json=observation_payload([raw], Policy()))
    assert response.status_code == 201, response.text
    return response.json()


def audio(item):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\1\0" * (item["clip"]["end_sample"] - item["clip"]["start_sample"]))
    return output.getvalue()


def upload(api, item):
    result = api.put(f"/api/observations/{item['id']}/audio", content=audio(item),
                     headers={"Content-Type": "audio/wav"})
    assert result.status_code in (200, 201), result.text
    return result.json()


def detail(api, item):
    return api.get(f"/api/observations/{item['id']}").json()


def test_newer_replaces_old_without_losing_events_or_resurrection(api):
    first = upload(api, create(api, "old", when=datetime.now(timezone.utc) - timedelta(minutes=1)))
    second = upload(api, create(api, "new"))
    old = detail(api, first)
    assert old["status"] == "auto_accepted"
    assert old["audio_available"] is False and old["audio_url"] is None
    assert old["audio_retention"]["reason"] == "superseded"
    assert old["audio_retention"]["terminal"] is True
    assert api.get(second["audio_url"]).content == audio(second)
    assert api.get("/api/observations/count").json()["count"] == 2
    assert upload(api, first)["audio_available"] is False
    assert len(list(api.app.state.settings.resolved_storage_root.rglob("*.wav"))) == 1


def test_human_confirmation_beats_new_auto_and_delayed_import(api):
    first = upload(api, create(api, "human"))
    response = api.post(f"/api/observations/{first['id']}/confirm",
                        json={"expected_status": "auto_accepted"})
    assert response.status_code == 200, response.text
    assert upload(api, create(api, "auto"))["audio_retention"]["reason"] == "superseded"
    assert detail(api, first)["audio_available"] is True


def test_review_separate_from_accepted_and_expires_without_rejection(api):
    accepted = upload(api, create(api, "accepted"))
    review = upload(api, create(api, "review", score=.68))
    assert detail(api, accepted)["audio_available"] and review["audio_available"]
    now = datetime.now(timezone.utc) + timedelta(days=15)
    settings = api.app.state.settings
    plan = reconcile(api.app.state.engine, settings, now=now)
    assert any(a["reason"] == "expired" for a in plan["actions"])
    assert detail(api, review)["audio_available"]  # dry run
    reconcile(api.app.state.engine, settings, apply=True, now=now)
    expired = detail(api, review)
    assert expired["status"] == "pending_review"
    assert expired["audio_retention"]["reason"] == "expired"
    assert detail(api, accepted)["audio_available"]


def test_review_dedup_uses_time_not_import_order_and_keeps_domains_separate(api):
    newest = upload(api, create(api, "latest", score=.68))
    older = upload(api, create(api, "delayed", score=.68,
                              when=datetime.now(timezone.utc) - timedelta(hours=1)))
    assert older["audio_retention"]["reason"] == "superseded"
    assert detail(api, newest)["audio_available"]
    bat = upload(api, create(api, "bat", score=.68, domain="bat", name="Pipistrellus pipistrellus"))
    assert bat["audio_available"]


def test_rejection_hides_audio_immediately_and_expires_after_grace(api):
    item = upload(api, create(api, "reject", score=.68))
    result = api.post(f"/api/observations/{item['id']}/reject", json={"expected_status": "pending_review"})
    assert result.status_code == 200, result.text
    assert result.json()["audio_retention"]["state"] == "trash"
    assert result.json()["audio_url"] is None
    assert api.get(item["audio_url"]).status_code == 404
    assert len(list(api.app.state.settings.resolved_storage_root.rglob("*.wav"))) == 1
    reconcile(api.app.state.engine, api.app.state.settings, apply=True,
              now=datetime.now(timezone.utc) + timedelta(hours=25))
    assert detail(api, item)["status"] == "human_rejected"
    assert not list(api.app.state.settings.resolved_storage_root.rglob("*.wav"))


def test_storage_pressure_keeps_existing_audio_and_acknowledges_terminal_outcome(api):
    first = upload(api, create(api, "first"))
    settings = api.app.state.settings
    settings.audio_retention_max_bytes = disk_bytes(settings.resolved_storage_root) + 1
    refused = upload(api, create(api, "next"))
    assert refused["audio_retention"]["reason"] == "storage_pressure"
    assert refused["audio_retention"]["terminal"]
    assert detail(api, first)["audio_available"]
    assert api.get("/api/observations/count").json()["count"] == 2


def test_unlink_failure_leaves_retryable_tombstone(api):
    first = upload(api, create(api, "first"))
    with patch.object(Path, "unlink", side_effect=PermissionError("synthetic busy file")):
        # Avoid mocking storage.publish's temporary unlink; create the record
        # without upload and mark the first rejected directly for cleanup.
        with Session(api.app.state.engine) as session:
            row = session.get(Observation, first["id"])
            row.status = "human_rejected"
            row.review = {"at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()}
            session.commit()
        result = reconcile(api.app.state.engine, api.app.state.settings, apply=True)
    assert result["unlink_failures"] == 1
    assert detail(api, first)["audio_retention"]["state"] == "deleting"
    reconcile(api.app.state.engine, api.app.state.settings, apply=True)
    assert detail(api, first)["audio_retention"]["state"] == "deleted"
    assert not list(api.app.state.settings.resolved_storage_root.rglob("*.wav"))


def correction(api, item, action, **extra):
    from uuid import uuid4
    result = api.post(f"/api/observations/{item['id']}/correct", json={
        "expected_status": item['status'], "expected_version": item['review_version'],
        "request_id": str(uuid4()), "action": action, **extra})
    assert result.status_code == 200, result.text
    return result.json()


def test_versioned_reject_and_restore_keep_own_audio(api):
    item = upload(api, create(api, "confirmed"))
    item = correction(api, item, "confirm")
    item = correction(api, item, "reject")
    assert item["audio_retention"]["state"] == "trash"
    item = correction(api, item, "confirm")
    assert item["audio_available"]
    assert api.get(item["audio_url"]).content == audio(item)


def test_reject_expired_record_does_not_resurrect_missing_audio(api):
    item = upload(api, create(api, "review", score=.68))
    reconcile(api.app.state.engine, api.app.state.settings, apply=True,
              now=datetime.now(timezone.utc) + timedelta(days=15))
    item = detail(api, item)
    result = api.post(f"/api/observations/{item['id']}/reject", json={"expected_status": item['status']})
    assert result.status_code == 200, result.text
    assert not result.json()["audio_available"]
    assert result.json()["evidence_kind"] == "deleted"
    assert reconcile(api.app.state.engine, api.app.state.settings, apply=True)["actions"] == []


def test_promotion_reserves_space_and_keeps_review_on_failure(api):
    item = upload(api, create(api, "review", score=.68))
    settings = api.app.state.settings
    settings.audio_retention_max_bytes = disk_bytes(settings.resolved_storage_root) + 1
    result = api.post(f"/api/observations/{item['id']}/confirm", json={"expected_status": item['status']})
    assert result.status_code == 507
    assert detail(api, item)["status"] == "pending_review"
    assert api.get(item["audio_url"]).content == audio(item)


def test_trash_quota_purges_oldest_before_grace(api):
    api.app.state.settings.audio_trash_max_bytes = 0
    item = upload(api, create(api, "review", score=.68))
    result = api.post(f"/api/observations/{item['id']}/reject", json={"expected_status": item['status']})
    assert result.status_code == 200
    assert result.json()["audio_retention"]["state"] == "deleted"
    assert not list(api.app.state.settings.resolved_storage_root.rglob("*.wav"))


def test_species_example_is_explicit_and_does_not_relabel_old_evidence(api):
    old = upload(api, create(api, "old"))
    new = upload(api, create(api, "new"))
    result = api.get("/api/observations/species-audio?domain=bird&scientific_name=Parus%20major")
    assert result.status_code == 200
    assert result.json()["accepted"]["observation_id"] == new["id"]
    assert detail(api, old)["audio_url"] is None


def test_species_correction_recalculates_both_groups_and_preserves_counts(api):
    from app.modules.species.models import Species
    now = datetime.now(timezone.utc)
    with Session(api.app.state.engine) as session:
        session.add(Species(domain="bird", scientific_name="Turdus merula", source="test",
                            source_species_id=99, source_metadata={}, imported_at=now, updated_at=now))
        session.commit()
    first = upload(api, create(api, "first", name="Parus major"))
    other = upload(api, create(api, "other", name="Turdus merula"))
    corrected = correction(api, first, "confirm", scientific_name_override="Turdus merula")
    assert corrected["scientific_name"] == "Parus major"  # original model data
    assert corrected["effective_identity"]["scientific_name"] == "Turdus merula"
    assert detail(api, other)["audio_retention"]["reason"] == "superseded"
    assert api.get("/api/observations/count").json()["count"] == 2
    examples = api.get("/api/observations/species-audio?domain=bird&scientific_name=Turdus%20merula").json()
    assert examples["accepted"]["observation_id"] == first["id"]
    assert api.get("/api/observations/species-audio?domain=bird&scientific_name=Parus%20major").json()["accepted"] is None


def test_legacy_audio_participates_in_same_species_selection(api):
    from app.modules.birds.models import BirdDetection, BirdAudio
    from app.modules.birds import storage
    from uuid import uuid4
    settings = api.app.state.settings
    now, identity = datetime.now(timezone.utc), str(uuid4())
    item = create(api, "observation")
    data = audio(item)
    info = storage.inspect_wav(data)
    key = f"birds/audio/{now:%Y/%m/%d}/{identity}.wav"
    storage.publish(settings.resolved_storage_root, key, data, info["sha256"])
    with Session(api.app.state.engine) as session:
        legacy = BirdDetection(id=identity, timestamp=now - timedelta(minutes=1),
                               scientific_name="Parus major", confidence=.9, source="legacy",
                               verification_status="confirmed")
        legacy.audio = BirdAudio(detection_id=identity, storage_key=key, **info)
        session.add(legacy)
        session.commit()
    # A human-confirmed legacy example outranks a new automatic event.
    assert upload(api, item)["audio_retention"]["reason"] == "superseded"
    assert (settings.resolved_storage_root / key).exists()
    example = api.get("/api/observations/species-audio?domain=bird&scientific_name=Parus%20major").json()
    assert example["accepted"]["observation_id"] == identity
    assert example["accepted"]["source_kind"] == "legacy"
    with Session(api.app.state.engine) as session:
        session.get(BirdDetection, identity).verification_status = "rejected"
        session.commit()
    reconcile(api.app.state.engine, settings, apply=True)
    assert not (settings.resolved_storage_root / key).exists()
    with Session(api.app.state.engine) as session:
        assert session.get(BirdDetection, identity).audio.status == "rejected"


def test_readonly_database_supports_dry_run(api):
    import sqlite3
    from sqlalchemy import create_engine
    item = upload(api, create(api, "review", score=.68))
    settings = api.app.state.settings
    uri = settings.resolved_database_path.as_uri() + "?mode=ro"
    engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
    try:
        result = reconcile(engine, settings, now=datetime.now(timezone.utc) + timedelta(days=15))
        assert result["actions"][0]["reason"] == "expired"
    finally:
        engine.dispose()
    assert detail(api, item)["audio_available"]


def test_failed_publication_keeps_existing_example(api):
    first = upload(api, create(api, "first"))
    second = create(api, "second")
    with patch("app.modules.birds.storage.publish", side_effect=OSError("synthetic write failure")):
        result = api.put(f"/api/observations/{second['id']}/audio", content=audio(second),
                         headers={"Content-Type": "audio/wav"})
    assert result.status_code == 503
    assert detail(api, first)["audio_available"]
    assert not detail(api, second)["audio_available"]
    assert len(list(api.app.state.settings.resolved_storage_root.rglob("*.wav"))) == 1


def test_concurrent_uploads_respect_combined_budget(api):
    from concurrent.futures import ThreadPoolExecutor
    first = create(api, "one", name="Parus major")
    second = create(api, "two", name="Turdus merula")
    settings = api.app.state.settings
    settings.audio_retention_max_bytes = len(audio(first)) + 100
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(upload, api, item) for item in (first, second)]
        results = [future.result(timeout=10) for future in futures]
    assert sum(item["audio_available"] for item in results) == 1
    assert sum(item["audio_retention"]["reason"] == "storage_pressure" for item in results) == 1
    assert disk_bytes(settings.resolved_storage_root) <= settings.audio_retention_max_bytes
