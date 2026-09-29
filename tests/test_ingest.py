import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import wave
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.birds.models import BirdAudio


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        _env_file=None, database_path=tmp_path / "db.sqlite3",
        storage_root=tmp_path / "storage", max_audio_bytes=16384,
    )
    with TestClient(create_app(settings)) as api:
        yield api


def payload(**changes):
    return dict(
        event_id="event-1", detected_at="2026-09-29T12:00:00+02:00",
        scientific_name="Columba livia", common_name="Rotsduif",
        confidence=0.944, source="test-producer", source_version="1",
        model_version="acoustic-3", raw_metadata={"window": 6, "scores": [0.944]},
    ) | changes


def wav_bytes(value=0):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(bytes([value, 0]) * 800)
    return stream.getvalue()


def create(client, **changes):
    response = client.post("/api/birds/detections", json=payload(**changes))
    assert response.status_code == 201, response.text
    return response.json()


def upload(client, identity, data=None):
    return client.put(
        f"/api/birds/detections/{identity}/audio",
        content=wav_bytes() if data is None else data,
        headers={"Content-Type": "audio/wav"},
    )


def test_ingest_get_latest_and_health(client):
    assert client.get("/api/birds/latest").status_code == 404
    item = create(client)
    assert item["detected_at"] == item["timestamp"] == "2026-09-29T10:00:00Z"
    assert item["verification_status"] == "unreviewed"
    assert item["audio"] is None and item["audio_url"] is None
    assert item["raw_metadata"] == payload()["raw_metadata"]
    assert client.get(f'/api/birds/detections/{item["id"]}').json() == item
    assert client.get("/api/birds/latest").json() == item
    assert client.get(f'/api/birds/detections/{item["id"]}/audio').status_code == 404
    assert client.get("/api/health").json()["database"] == "ok"


@pytest.mark.parametrize("change", [
    {"confidence": -0.01}, {"confidence": 1.01}, {"confidence": "0.9"},
    {"confidence": True}, {"detected_at": "2026-09-29T10:00:00"},
    {"detected_at": "not-a-date"}, {"detected_at": 1790676000},
    {"scientific_name": " "}, {"source": ""}, {"event_id": ""},
    {"event_id": "x" * 256}, {"verification_status": "confirmed"},
    {"audio_reference": "../../private.wav"},
])
def test_invalid_detection(client, change):
    assert client.post("/api/birds/detections", json=payload(**change)).status_code == 422
    assert client.get("/api/birds/detections").json() == []


@pytest.mark.parametrize("data", [b"{", b'{"confidence":NaN}', b"[]"])
def test_malformed_json(client, data):
    assert client.post("/api/birds/detections", content=data,
                       headers={"content-type": "application/json"}).status_code == 422


def test_nonfinite_metadata(client):
    body = json.dumps(payload(raw_metadata={"bad": float("nan")}))
    assert client.post("/api/birds/detections", content=body,
                       headers={"content-type": "application/json"}).status_code == 422


def test_idempotency_conflict_and_distinct_events(client):
    item = create(client)
    retry = client.post("/api/birds/detections", json=payload(
        detected_at="2026-09-29T10:00:00Z"
    ))
    assert retry.status_code == 200 and retry.json() == item
    assert client.post("/api/birds/detections", json=payload(confidence=0.8)).status_code == 409
    create(client, event_id="event-2")
    create(client, source="another-producer")
    assert len(client.get("/api/birds/detections").json()) == 3


def test_parallel_retries(client):
    with ThreadPoolExecutor(max_workers=4) as pool:
        replies = list(pool.map(
            lambda _: client.post("/api/birds/detections", json=payload()), range(4)
        ))
    assert sorted(r.status_code for r in replies) == [200, 200, 200, 201]
    assert len({r.json()["id"] for r in replies}) == 1


def test_filters_and_latest(client):
    first = create(client)
    second = create(client, event_id="later", detected_at="2026-09-29T11:00:00Z",
                    scientific_name="Turdus merula")
    assert client.get("/api/birds/latest").json()["id"] == second["id"]
    assert client.get("/api/birds/detections", params={
        "since": "2026-09-29T10:00:00Z", "until": "2026-09-29T11:00:00Z",
    }).json()[0]["id"] == first["id"]
    assert len(client.get("/api/birds/detections", params={"limit": 1}).json()) == 1
    assert client.get("/api/birds/detections", params={
        "scientific_name": "Turdus merula",
    }).json()[0]["id"] == second["id"]
    for query in (
        {"since": "2026-09-29T10:00:00"},
        {"since": "bad"},
        {"since": "2026-09-30T00:00:00Z", "until": "2026-09-29T00:00:00Z"},
    ):
        assert client.get("/api/birds/detections", params=query).status_code == 422


def test_audio_roundtrip_metadata_retry_and_range(client):
    item = create(client)
    data = wav_bytes()
    reply = upload(client, item["id"], data)
    assert reply.status_code == 201, reply.text
    result = reply.json()
    assert result["audio"]["sha256"] == hashlib.sha256(data).hexdigest()
    assert result["audio"]["size_bytes"] == len(data)
    assert result["audio"]["sample_rate"] == 8000
    assert result["audio"]["channels"] == 1
    assert result["audio"]["duration_seconds"] == 0.1
    assert result["audio"]["codec"] == "pcm"
    assert "storage_key" not in reply.text
    assert str(client.app.state.settings.resolved_storage_root) not in reply.text
    response = client.get(result["audio_url"])
    assert response.content == data and response.headers["content-type"] == "audio/wav"
    partial = client.get(result["audio_url"], headers={"Range": "bytes=0-11"})
    assert partial.status_code == 206 and partial.content == data[:12]
    assert upload(client, item["id"], data).status_code == 200
    assert upload(client, item["id"], wav_bytes(1)).status_code == 409
    # Detection retry remains valid after the second step.
    assert client.post("/api/birds/detections", json=payload()).status_code == 200
    files = list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))
    assert len(files) == 1 and files[0].read_bytes() == data
    assert not list(client.app.state.settings.resolved_storage_root.rglob("*.tmp"))


def test_parallel_audio_retries(client):
    identity = create(client)["id"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(pool.map(lambda _: upload(client, identity), range(3)))
    assert sorted(r.status_code for r in replies) == [200, 200, 201]
    assert len(list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))) == 1


@pytest.mark.parametrize("data", [b"", b"not wave", b"#!/bin/sh\necho unsafe", wav_bytes()[:-1]],
                         ids=["empty", "not-wav", "script", "truncated-wav"])
def test_invalid_audio(client, data):
    identity = create(client)["id"]
    assert upload(client, identity, data).status_code == 422
    assert client.get(f"/api/birds/detections/{identity}").json()["audio"] is None
    assert not list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))


def test_wrong_type_size_and_unknown_detection(client):
    assert client.post("/api/birds/detections", content="{}").status_code == 415
    assert client.post("/api/birds/detections", content=b"x" * 65537,
                       headers={"content-type": "application/json"}).status_code == 413
    identity = create(client)["id"]
    assert client.put(f"/api/birds/detections/{identity}/audio", content=wav_bytes(),
                      headers={"content-type": "text/plain"}).status_code == 415
    assert upload(client, identity, b"x" * 16385).status_code == 413
    unknown = uuid4()
    assert client.get(f"/api/birds/detections/{unknown}").status_code == 404
    assert upload(client, unknown).status_code == 404


def test_storage_failure_leaves_detection_without_audio(client):
    identity = create(client)["id"]
    with patch("app.modules.birds.storage.os.link", side_effect=OSError("private/path")):
        response = upload(client, identity)
    assert response.status_code == 503 and "private" not in response.text
    assert client.get(f"/api/birds/detections/{identity}").json()["audio"] is None
    root = client.app.state.settings.resolved_storage_root
    assert not list(root.rglob("*.wav")) and not list(root.rglob("*.tmp"))


def test_database_commit_failure_removes_published_audio(client):
    identity = create(client)["id"]
    with patch.object(Session, "commit", side_effect=OperationalError("test", {}, Exception())):
        response = upload(client, identity)
    assert response.status_code == 503
    assert client.get(f"/api/birds/detections/{identity}").json()["audio"] is None
    assert not list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))
    assert upload(client, identity).status_code == 201


def test_same_audio_crash_orphan_is_reused(client):
    item = create(client)
    key = f'birds/audio/2026/09/29/{item["id"]}.wav'
    path = client.app.state.settings.resolved_storage_root / key
    path.parent.mkdir(parents=True)
    path.write_bytes(wav_bytes())
    with patch("app.modules.birds.storage.os.link", side_effect=AssertionError("must reuse")):
        assert upload(client, item["id"]).status_code == 201


def test_different_crash_orphan_not_overwritten(client):
    item = create(client)
    path = client.app.state.settings.resolved_storage_root / f'birds/audio/2026/09/29/{item["id"]}.wav'
    path.parent.mkdir(parents=True)
    path.write_bytes(b"preserve")
    assert upload(client, item["id"]).status_code == 409
    assert path.read_bytes() == b"preserve"


@pytest.mark.parametrize("key", ["../secret.wav", "/tmp/secret.wav", "C:/secret.wav",
                                  "birds/audio/2026/09/29/../../secret.wav"])
def test_stored_path_traversal_rejected(client, key):
    identity = create(client)["id"]
    assert upload(client, identity).status_code == 201
    with Session(client.app.state.engine) as session:
        session.get(BirdAudio, identity).storage_key = key
        session.commit()
    assert client.get(f"/api/birds/detections/{identity}/audio").status_code == 404


def test_original_filename_not_used(client):
    item = create(client)
    result = client.put(f'/api/birds/detections/{item["id"]}/audio',
                        content=wav_bytes(), headers={
                            "content-type": "audio/wav",
                            "content-disposition": 'attachment; filename="../../private.wav"',
                        })
    assert result.status_code == 201
    assert "../../" not in result.text
    files = list(client.app.state.settings.resolved_storage_root.rglob("*.wav"))
    assert [p.name for p in files] == [item["id"] + ".wav"]


def test_missing_file_is_404_and_not_silently_recreated(client):
    identity = create(client)["id"]
    assert upload(client, identity).status_code == 201
    next(client.app.state.settings.resolved_storage_root.rglob("*.wav")).unlink()
    assert client.get(f"/api/birds/detections/{identity}/audio").status_code == 404
    assert upload(client, identity).status_code == 409


@pytest.mark.parametrize("confidence", [0, 1])
def test_confidence_boundaries_allowed(client, confidence):
    assert create(client, confidence=confidence)["confidence"] == confidence


@pytest.mark.parametrize("offset,value,width", [(20, 3, 2), (22, 3, 2), (24, 1, 4)])
def test_unsupported_wav_parameters_rejected(client, offset, value, width):
    identity = create(client)["id"]
    data = bytearray(wav_bytes())
    data[offset:offset + width] = value.to_bytes(width, "little")
    assert upload(client, identity, bytes(data)).status_code == 422


def test_corrupted_registered_file_not_accepted_as_retry(client):
    identity = create(client)["id"]
    assert upload(client, identity).status_code == 201
    path = next(client.app.state.settings.resolved_storage_root.rglob("*.wav"))
    path.write_bytes(wav_bytes(1))
    assert upload(client, identity).status_code == 409
    assert path.read_bytes() == wav_bytes(1)


def test_storage_can_move_without_database_changes(client, tmp_path):
    import shutil
    identity = create(client)["id"]
    assert upload(client, identity).status_code == 201
    previous = client.app.state.settings.resolved_storage_root
    destination = tmp_path / "nvme-simulation"
    shutil.copytree(previous, destination)
    client.app.state.settings.storage_root = destination
    assert client.get(f"/api/birds/detections/{identity}/audio").content == wav_bytes()
    with Session(client.app.state.engine) as session:
        assert session.get(BirdAudio, identity).storage_key.startswith("birds/audio/")


def test_stream_size_limit_without_content_length():
    import asyncio
    from fastapi import HTTPException
    from app.modules.birds.router import bounded_body
    class StreamRequest:
        async def stream(self):
            yield b"1234"
            yield b"5678"
            raise AssertionError("must stop consuming after the limit")
    with pytest.raises(HTTPException) as error:
        asyncio.run(bounded_body(StreamRequest(), 7))
    assert error.value.status_code == 413
