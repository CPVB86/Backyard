"""Authentication at the API boundary and across the production clients."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from detector.monitor_http import HTTPTransport, HTTPFailure
from operations.health import check_health
from operations.status import collect, main as status_main
from operations.wait_api import wait
from observations.policy import Policy


@pytest.fixture
def api(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path / "db.sqlite3",
                      storage_root=tmp_path / "audio")
    with TestClient(create_app(config)) as client:
        yield client


@pytest.mark.parametrize("header", [None, "", "Basic backyard-test-token", "Bearer wrong",
                                    "Bearer", "Bearer ", "Bearer backyard-test-token extra"])
def test_health_rejects_missing_or_invalid_credentials(api, header):
    headers = {} if header is None else {"Authorization": header}
    response = api.get("/api/health", headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json() == {"detail": "Unauthorized"}


def test_every_api_route_is_protected_before_validation(api):
    routes = {
        "/api/health": ["GET"],
        "/api/birds/detections": ["GET", "POST"],
        "/api/birds/latest": ["GET"],
        "/api/birds/detections/not-an-id": ["GET"],
        "/api/birds/detections/not-an-id/audio": ["GET", "PUT"],
        "/api/observations": ["GET", "POST"],
        "/api/observations/policy": ["GET"],
        "/api/observations/review": ["GET"],
        "/api/observations/not-an-id": ["GET"],
        "/api/observations/not-an-id/audio": ["GET", "PUT"],
        "/api/observations/not-an-id/confirm": ["POST"],
        "/api/observations/not-an-id/reject": ["POST"],
    }
    for path, methods in routes.items():
        for method in methods:
            assert api.request(method, path).status_code == 401, path
    assert api.get("/api/not-a-route").status_code == 401
    assert api.options("/api/health").status_code == 401


def test_correct_bearer_and_duplicate_header(api):
    assert api.get("/api/health", headers={"Authorization": "bEaReR backyard-test-token"}).json()["status"] == "ok"
    assert api.get("/api/health", headers=[("Authorization", "Bearer backyard-test-token"),
                                           ("Authorization", "Bearer wrong")]).status_code == 401
    assert api.get("/docs").status_code == 200
    schema = api.get("/openapi.json").json()
    assert schema["paths"]["/api/health"]["get"]["security"] == [{"HTTPBearer": []}]


@pytest.mark.parametrize("token", ["", " ", "bad\ntoken", "non-ascii-\u00e9"])
def test_api_fails_closed_without_valid_config(tmp_path, token):
    config = Settings(_env_file=None, api_token=token, database_path=tmp_path/"never.sqlite3")
    with pytest.raises(RuntimeError, match="BACKYARD_API_TOKEN"):
        with TestClient(create_app(config)):
            pass
    assert not config.resolved_database_path.exists()
    assert "backyard-test-token" not in repr(Settings(_env_file=None))


@pytest.fixture
def http_api():
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != "Bearer backyard-test-token":
                self.send_response(401); self.end_headers(); self.wfile.write(b'{}'); return
            import json
            body = ({"fingerprint": Policy.load().fingerprint} if self.path.endswith("/policy")
                    else {"status": "ok", "service": "backyard", "database": "ok"})
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(body).encode())
        do_POST = do_GET
        do_PUT = do_GET
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown(); server.server_close(); thread.join()


def test_real_transport_health_readiness_and_policy_send_bearer(http_api):
    url, seen = http_api
    assert check_health(url)["status"] == "ok"
    assert wait(url, timeout=2, check_policy=True)
    for method in ("POST", "PUT"):
        HTTPTransport(url, 2).request(method, "/api/observations", b"", "application/json")
    assert all(header == "Bearer backyard-test-token" for _, _, header in seen)
    assert {method for method, _, _ in seen} == {"GET", "POST", "PUT"}
    with pytest.raises(HTTPFailure, match="401") as failure:
        HTTPTransport(url, 2, api_token="wrong").request("GET", "/api/health", None, "application/json")
    assert not failure.value.retryable


def test_status_uses_service_file_token_without_leaking_it(tmp_path, capsys, monkeypatch):
    env = tmp_path/"service.env"
    env.write_text("BACKYARD_API_TOKEN=service-file-private-token\n")
    monkeypatch.setenv("BACKYARD_API_TOKEN", "different-shell-token")
    def report(settings, environment, hours, check_db):
        with patch("operations.status.service_status", return_value={}), \
             patch("operations.status.check_health", return_value={"status":"ok"}) as health, \
             patch("operations.status.read_journal", return_value={}), \
             patch("operations.status.database_inventory", return_value={}), \
             patch("operations.status.audio_inventory", return_value={}), \
             patch("operations.status.system_inventory", return_value={}):
            result = collect(settings, environment, hours, check_db)
        assert health.call_args.kwargs["api_token"] == "service-file-private-token"
        return result
    with patch("operations.status.collect", side_effect=report):
        assert status_main(["--environment-file", str(env), "--json"]) == 1
    assert "service-file-private-token" not in capsys.readouterr().out


def test_transport_rejects_missing_token_before_network(monkeypatch):
    monkeypatch.delenv("BACKYARD_API_TOKEN")
    with pytest.raises(ValueError, match="BACKYARD_API_TOKEN"):
        HTTPTransport("http://127.0.0.1:8010", 2)
