import io
import json
from urllib.error import URLError
from unittest.mock import Mock
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.core.config import Settings
from app import system_status as status

AUTH = {"Authorization": "Bearer backyard-test-token"}


def test_auth_and_unconfigured_status(tmp_path, monkeypatch):
    opener = Mock()
    monkeypatch.setattr(status, "build_opener", opener)
    with TestClient(create_app(Settings(_env_file=None, database_path=tmp_path/"db",
                                       storage_root=tmp_path))) as client:
        assert client.get("/api/system/status").status_code == 401
        response = client.get("/api/system/status", headers=AUTH)
        assert response.status_code == 200
        data = response.json()["components"]
        assert data["backyard"]["online"] is True
        assert data["backyard_remote"]["online"] is None
        assert data["bird_mic"]["online"] is None
        assert response.headers["cache-control"] == "no-store"
    opener.assert_not_called()


@pytest.mark.parametrize("state,reason", [
    ("online", "audio_transmitting"), ("offline", "audio_stalled"),
    ("offline", "capture_failed"), ("unknown", "no_active_receiver")])
def test_probe_contract_without_credentials(monkeypatch, state, reason):
    payload = dict(schema_version=1, service="byremote-audio", sample_rate=384000,
                   status=state, reason=reason, last_audio_at=None)
    opener = Mock()
    opener.open.return_value = io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setattr(status, "build_opener", lambda *a: opener)
    value, reachable = status.probe("http://remote:8086/health", 384000)
    assert reachable is True
    assert value["status"] == state
    request = opener.open.call_args.args[0]
    assert not request.has_header("Authorization")
    assert request.full_url.endswith("/health")
    assert opener.open.call_args.kwargs["timeout"] == 2


def test_bad_payload_and_connection_failure(monkeypatch):
    opener = Mock()
    monkeypatch.setattr(status, "build_opener", lambda *a: opener)
    opener.open.return_value = io.BytesIO(b'{"status":"online"}')
    assert status.probe("http://remote/health", 48000)[0]["online"] is None
    opener.open.side_effect = URLError("private network details")
    result, reachable = status.probe("http://remote/health", 48000)
    assert reachable is False
    assert result["reason"] == "remote_unreachable"
    assert result["online"] is None


@pytest.mark.parametrize("reachable,expected", [(False, "offline"), (True, "online")])
def test_remote_reachability_is_distinct_from_mic(tmp_path, monkeypatch, reachable, expected):
    monkeypatch.setattr(status, "probe", lambda *a: (status.item("unknown", "no_active_receiver"), reachable))
    with TestClient(create_app(Settings(_env_file=None, database_path=tmp_path/"db",
                                       storage_root=tmp_path))) as client:
        data = client.get("/api/system/status", headers=AUTH).json()["components"]
        assert data["backyard_remote"]["status"] == expected
        assert data["bird_mic"]["online"] is None
