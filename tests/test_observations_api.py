from dataclasses import replace
from datetime import datetime, timedelta, timezone
import io
from unittest.mock import patch
import wave

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.observations.models import Observation
from app.modules.observations.service import cleanup
from observations.policy import Policy, RawCandidate, observation_payload

RATE = 8000


def raw(start=0, score=.94, state="normal", domain="bird", name="Parus major", stream="test", identity=None):
    return RawCandidate(identity or f"{stream}-{start}", f"{stream}-w-{start}", "synthetic-test", stream,
                        domain, name, "Test species", score, RATE, start, start + 24000,
                        "2026-10-01T00:00:00+00:00", "synthetic",
                        {"state": state, "provider": "test"}, {"synthetic": True})


@pytest.fixture
def client(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "db.sqlite3", storage_root=tmp_path / "audio")
    with TestClient(create_app(config), headers={"Authorization": "Bearer backyard-test-token"}) as api:
        yield api


def body(**kwargs):
    return observation_payload([raw(**kwargs)], Policy())


def create(client, **kwargs):
    response = client.post("/api/observations", json=body(**kwargs))
    assert response.status_code == 201, response.text
    return response.json()


def wav(item):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1); output.setsampwidth(2); output.setframerate(RATE)
        output.writeframes(b"\x01\x00" * (item["clip"]["end_sample"] - item["clip"]["start_sample"]))
    return stream.getvalue()


def upload(client, item):
    audio = wav(item)
    response = client.put(f"/api/observations/{item['id']}/audio", content=audio,
                          headers={"Content-Type": "audio/wav"})
    assert response.status_code == 201, response.text
    return response.json(), audio


def test_auto_accepted_permanent_evidence_without_human_action(client):
    item = create(client)
    assert item["status"] == "auto_accepted" and item["evidence_kind"] == "permanent"
    item, audio = upload(client, item)
    assert client.get(item["audio_url"]).content == audio
    assert client.get(item["audio_url"], headers={"Range": "bytes=0-11"}).status_code == 206
    assert client.get("/api/observations/review").json() == []
    assert len(list(client.app.state.settings.resolved_storage_root.glob("birds/audio/2026/10/01/*.wav"))) == 1


def test_review_audio_survives_due_date_then_confirm_promotes_one_file(client):
    item, audio = upload(client, create(client, score=.68))
    assert item["status"] == "pending_review" and item["evidence_kind"] == "review"
    assert client.get("/api/observations/review").json()[0]["id"] == item["id"]
    future = datetime.now(timezone.utc) + timedelta(days=365)
    assert cleanup(client.app.state.engine, client.app.state.settings, apply=True, now=future) == []
    assert client.get(item["audio_url"]).content == audio
    request = {"expected_status": "pending_review", "note": "Beluisterd"}
    result = client.post(f"/api/observations/{item['id']}/confirm", json=request)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "human_confirmed"
    assert result.json()["evidence_kind"] == "permanent"
    assert client.post(f"/api/observations/{item['id']}/confirm", json=request).json() == result.json()
    assert client.get(result.json()["audio_url"]).content == audio
    files = list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))
    assert len(files) == 1 and "review" not in str(files[0].relative_to(client.app.state.settings.resolved_storage_root))
    assert client.get("/api/observations/review").json() == []


def test_reject_lifecycle_dry_run_and_explicit_cleanup(client):
    item, audio = upload(client, create(client, score=.68))
    response = client.post(f"/api/observations/{item['id']}/reject", json={"expected_status": "pending_review"})
    assert response.status_code == 200
    assert response.json()["evidence_kind"] == "delete_pending"
    assert client.get(item["audio_url"]).content == audio
    future = datetime.now(timezone.utc) + timedelta(days=15)
    assert len(cleanup(client.app.state.engine, client.app.state.settings, now=future)) == 1
    assert client.get(item["audio_url"]).status_code == 200
    assert len(cleanup(client.app.state.engine, client.app.state.settings, apply=True, now=future)) == 1
    assert client.get(item["audio_url"]).status_code == 404
    assert client.get(f"/api/observations/{item['id']}").json()["audio"]["status"] == "deleted"
    assert client.get(f"/api/observations/{item['id']}").json()["candidates"]
    assert client.put(item["audio_url"], content=audio, headers={"Content-Type": "audio/wav"}).status_code == 409


def test_strong_unusual_is_permanent_but_review_recommended(client):
    item, _ = upload(client, create(client, score=.96, state="unusual"))
    assert item["status"] == "review_recommended"
    assert item["evidence_kind"] == "permanent"
    assert "strong_unusual_preserve" in item["decision"]["reasons"]
    assert client.get("/api/observations/review").json()[0]["id"] == item["id"]


@pytest.mark.parametrize("domain,name,score,state", [
    ("unsupported", "Pan troglodytes", .68, "normal"),
    ("unsupported", "Canis lupus", .99, "normal"),
    ("bird", "Parus major", .59, "normal"),
    ("bird", "Parus major", .68, "unusual"),
])
def test_discarded_taxa_and_low_scores_never_create_rows_or_files(client, domain, name, score, state):
    response = client.post("/api/observations", json=body(domain=domain, name=name, score=score, state=state))
    assert response.status_code == 200 and response.json()["status"] == "discarded"
    assert response.json()["id"] is None
    assert client.get("/api/observations").json() == []
    assert list(client.app.state.settings.resolved_storage_root.rglob("*.wav")) == []


def test_synthetic_bat_full_flow(client):
    item, audio = upload(client, create(client, domain="bat", name="Pipistrellus pipistrellus"))
    assert item["domain"] == "bat" and item["status"] == "auto_accepted"
    assert client.get("/api/observations?domain=bird").json() == []
    assert client.get("/api/observations?domain=bat").json()[0]["id"] == item["id"]
    assert client.get(item["audio_url"]).content == audio
    assert len(list(client.app.state.settings.resolved_storage_root.glob("bats/audio/2026/10/01/*.wav"))) == 1


def test_idempotency_after_review_does_not_reset_status_or_duplicate_audio(client):
    item, audio = upload(client, create(client, score=.68))
    client.post(f"/api/observations/{item['id']}/confirm", json={"expected_status": "pending_review"})
    retry = client.post("/api/observations", json=body(score=.68))
    assert retry.status_code == 200 and retry.json()["status"] == "human_confirmed"
    assert retry.json()["id"] == item["id"]
    assert client.put(retry.json()["audio_url"], content=audio, headers={"Content-Type": "audio/wav"}).status_code == 200
    assert client.post("/api/observations", json=body(score=.69)).status_code == 409
    assert len(client.get("/api/observations").json()) == 1


def test_supporting_candidates_are_traceable_and_cannot_be_reused(client):
    supports = [raw(score=.71), raw(start=12000, score=.91), raw(start=24000, score=.84)]
    payload = observation_payload(supports, Policy())
    response = client.post("/api/observations", json=payload)
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["supporting_candidate_count"] == 3 and item["best_confidence"] == .91
    assert datetime.fromisoformat(item["end_at"]) == datetime(2026,10,1,0,0,6,tzinfo=timezone.utc)
    assert [c["candidate_id"] for c in item["candidates"]] == [c.candidate_id for c in supports]
    payload["event_id"] = "another-observation"
    assert client.post("/api/observations", json=payload).status_code == 409


def test_invalid_policy_supports_audio_and_review_concurrency(client):
    payload = body(); payload["policy_fingerprint"] = "0" * 64
    assert client.post("/api/observations", json=payload).status_code == 409
    payload = body(); payload["candidates"].append(dict(payload["candidates"][0]))
    assert client.post("/api/observations", json=payload).status_code == 422
    item = create(client, score=.68)
    assert client.post(f"/api/observations/{item['id']}/confirm", json={"expected_status": "pending_review"}).status_code == 404
    invalid = dict(item, clip=dict(item["clip"], end_sample=item["clip"]["end_sample"] + RATE))
    assert client.put(f"/api/observations/{item['id']}/audio", content=wav(invalid),
                      headers={"Content-Type": "audio/wav"}).status_code == 422
    item, _ = upload(client, item)
    assert client.post(f"/api/observations/{item['id']}/confirm", json={"expected_status": "auto_accepted"}).status_code == 409


def test_audio_publish_commit_failure_rolls_back_new_file(client):
    item = create(client)
    with patch.object(Session, "commit", side_effect=OperationalError("test", {}, Exception())):
        reply = client.put(f"/api/observations/{item['id']}/audio", content=wav(item),
                           headers={"Content-Type": "audio/wav"})
    assert reply.status_code == 503
    assert list(client.app.state.settings.resolved_storage_root.rglob("*.wav")) == []
    assert client.get(f"/api/observations/{item['id']}").json()["audio"] is None


def test_confirm_commit_failure_preserves_review_file(client):
    item, audio = upload(client, create(client, score=.68))
    with patch.object(Session, "commit", side_effect=OperationalError("test", {}, Exception())):
        response = client.post(f"/api/observations/{item['id']}/confirm", json={"expected_status": "pending_review"})
    assert response.status_code == 503
    assert client.get(item["audio_url"]).content == audio
    assert client.get(f"/api/observations/{item['id']}").json()["evidence_kind"] == "review"
    assert len(list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))) == 1


def test_policy_endpoint_and_review_paths_in_openapi(client):
    policy = client.get("/api/observations/policy").json()
    assert policy["fingerprint"] == Policy().fingerprint
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/observations/review" in paths and "/api/observations/{identity}/confirm" in paths
    assert "/api/birds/detections" in paths

def test_review_stale_copy_cleanup_can_retry_after_filesystem_failure(client):
    from pathlib import Path
    item, audio = upload(client, create(client, score=.68))
    original_unlink = Path.unlink
    def busy_review_copy(path, *args, **kwargs):
        if "review" in path.parts:
            raise OSError("busy review copy")
        return original_unlink(path, *args, **kwargs)
    with patch.object(Path, "unlink", busy_review_copy):
        result = client.post(f"/api/observations/{item['id']}/confirm",
                             json={"expected_status": "pending_review"})
    assert result.status_code == 200
    assert result.json()["evidence_kind"] == "permanent"
    assert client.get(item["audio_url"]).content == audio
    assert len(list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))) == 2
    report = cleanup(client.app.state.engine, client.app.state.settings, apply=True)
    assert report == [{"id": item["id"], "delete_rejected": False, "stale_copy": True}]
    assert len(list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))) == 1


def test_conflicting_human_decisions_cannot_overwrite_each_other(client):
    item, _ = upload(client, create(client, score=.68))
    path = f"/api/observations/{item['id']}"
    assert client.post(path+"/confirm", json={"expected_status":"pending_review"}).status_code == 200
    assert client.post(path+"/reject", json={"expected_status":"pending_review"}).status_code == 409
    assert client.post(path+"/reject", json={"expected_status":"human_confirmed"}).status_code == 409
    assert client.get(path).json()["status"] == "human_confirmed"


def test_oversized_and_nonfinite_metadata_refused_before_storage(client):
    payload = body()
    payload["candidates"][0]["metadata"] = {"large": "x" * (256 * 1024)}
    assert client.post("/api/observations", json=payload).status_code == 413
    import json
    payload = body()
    payload["candidates"][0]["metadata"] = {"bad": float("nan")}
    assert client.post("/api/observations", content=json.dumps(payload),
                       headers={"Content-Type":"application/json"}).status_code == 422
    assert client.get("/api/observations").json() == []


def test_unrepresentable_sample_timestamp_is_validation_error(client):
    payload = body()
    payload["candidates"][0]["start_sample"] = 10**100
    payload["candidates"][0]["end_sample"] = 10**100 + 24000
    assert client.post("/api/observations", json=payload).status_code == 422
    assert client.get("/api/observations").json() == []
