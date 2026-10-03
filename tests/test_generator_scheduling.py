from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock
import hashlib
import time
import urllib.error

import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from generator.store import AssetStore
from generator.scheduler import Scheduler
from generator.domains.birds.adapter import Birds
from generator.domains.birds import openai_images
from generator.errors import GenerationNotConfigured
from test_generator import png
from test_observations_api import body, upload

NAME = "Testus example"
AUTH = {"Authorization": "Bearer backyard-test-token"}


def accepted(name=NAME, status="auto_accepted", domain="bird"):
    return dict(domain=domain, scientific_name=name, common_name="Example", status=status)


@pytest.fixture
def scheduler(tmp_path):
    worker = Scheduler(AssetStore(tmp_path / "generator"))
    worker.jobs.initialize()
    return worker


@pytest.fixture
def api(tmp_path, monkeypatch):
    # Deterministic API scheduling tests; actual thread execution is tested below.
    monkeypatch.setattr(Scheduler, "start", lambda self: self.jobs.initialize())
    config = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3", storage_root=tmp_path)
    with TestClient(create_app(config), headers=AUTH) as client:
        yield client


def test_new_auto_accepted_schedules_after_commit(api):
    response = api.post("/api/observations", json=body(name=NAME))
    assert response.status_code == 201
    assert response.json()["status"] == "auto_accepted"
    assert api.app.state.generator.jobs.get("bird", NAME)["status"] == "generation_pending"
    assert api.get(f"/api/observations/{response.json()['id']}").status_code == 200


def test_confirm_schedules_and_rejection_does_not(api):
    item = api.post("/api/observations", json=body(name=NAME, score=.68)).json()
    assert api.app.state.generator.jobs.get("bird", NAME) is None
    item, audio = upload(api, item)
    response = api.post(f"/api/observations/{item['id']}/confirm", json={"expected_status":"pending_review"})
    assert response.json()["status"] == "human_confirmed"
    assert api.app.state.generator.jobs.get("bird", NAME)["status"] == "generation_pending"
    assert api.get(response.json()["audio_url"]).content == audio
    other = api.post("/api/observations", json=body(name="Testus rejected", score=.68, stream="other")).json()
    assert api.post(f"/api/observations/{other['id']}/reject", json={"expected_status":"pending_review"}).status_code == 200
    assert api.app.state.generator.jobs.get("bird", "Testus rejected") is None


@pytest.mark.parametrize("status", ["pending_review", "review_recommended", "human_rejected", "discarded"])
def test_nonaccepted_never_scheduled(scheduler, status):
    scheduler.accepted(accepted(status=status))
    assert scheduler.jobs.get("bird", NAME) is None


def test_complete_species_no_job_or_overwrite(scheduler, monkeypatch):
    monkeypatch.setattr(openai_images, "generate_png", Mock(side_effect=AssertionError("Unexpected generation")))
    before = scheduler.store.lookup("bird", "Turdus migratorius")["assets"]
    hashes = {k:hashlib.sha256(v["path"].read_bytes()).hexdigest() for k,v in before.items()}
    scheduler.accepted(accepted("Turdus migratorius"))
    assert scheduler.jobs.get("bird", "Turdus migratorius") is None
    assert not scheduler.run_one()
    assert hashes == {k:hashlib.sha256(v["path"].read_bytes()).hexdigest() for k,v in before.items()}


def test_deduplicated_durable_job_only_missing_pose(scheduler, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-provider-key")
    original = Birds.bundled
    monkeypatch.setattr(Birds, "bundled", lambda self,name: {k:v for k,v in original(self,name).items() if k != "flight"})
    paid = Mock(return_value=png())
    monkeypatch.setattr(openai_images, "generate_png", paid)
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(lambda _: scheduler.accepted(accepted("Turdus migratorius")), range(10)))
    restarted = Scheduler(AssetStore(scheduler.store.root))
    restarted.jobs.recover()
    assert restarted.run_one()
    assert not restarted.run_one()
    paid.assert_called_once()
    assert "in flight with wings spread" in paid.call_args.args[1]
    assert restarted.store.lookup("bird", "Turdus migratorius")["generation"]["status"] == "complete"


def test_failure_retains_observation_and_does_not_retry(api, monkeypatch, caplog):
    key = "private-test-secret"
    monkeypatch.setenv("OPENAI_API_KEY", key)
    paid = Mock(side_effect=RuntimeError(key))
    monkeypatch.setattr(openai_images, "generate_png", paid)
    item = api.post("/api/observations", json=body(name=NAME)).json()
    worker = api.app.state.generator_scheduler
    assert worker.run_one()
    assert worker.jobs.get("bird", NAME)["status"] == "generation_failed"
    assert api.get(f"/api/observations/{item['id']}").json()["status"] == "auto_accepted"
    assert api.post("/api/observations", json=body(name=NAME, stream="second")).status_code == 201
    assert not worker.run_one()
    paid.assert_called_once()
    status = api.get("/api/generator/bird/species", params={"scientific_name":NAME})
    assert key not in status.text and key not in caplog.text


@pytest.mark.parametrize("key", [None, "", "bad key", "bad\nkey"])
def test_missing_or_malformed_key_keeps_observation(api, monkeypatch, key):
    if key is not None: monkeypatch.setenv("OPENAI_API_KEY", key)
    paid = Mock(side_effect=AssertionError("No provider call allowed"))
    monkeypatch.setattr(openai_images, "generate_png", paid)
    item = api.post("/api/observations", json=body(name=NAME)).json()
    assert api.app.state.generator_scheduler.run_one()
    result = api.get("/api/generator/bird/species", params={"scientific_name":NAME}).json()
    assert result["generation"]["status"] == "generation_not_configured"
    assert result["missing_assets"] == ["perched", "flight"]
    assert api.get(f"/api/observations/{item['id']}").status_code == 200
    assert result["attempts"] == {}
    paid.assert_not_called()


def test_credentials_rejected_status_and_no_retry(scheduler, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_images, "generate_png", Mock(side_effect=GenerationNotConfigured("hidden")))
    scheduler.accepted(accepted())
    assert scheduler.run_one()
    assert scheduler.jobs.get("bird", NAME) == {"status":"generation_not_configured", "reason":"provider_credentials_rejected"}
    scheduler.jobs.recover()
    assert not scheduler.run_one()


@pytest.mark.parametrize("code", [401,403])
def test_http_credentials_rejection_is_sanitized(code):
    def opener(*args, **kwargs):
        raise urllib.error.HTTPError("https://example.invalid", code, "private secret", {}, None)
    with pytest.raises(GenerationNotConfigured, match="Provider credentials unavailable"):
        openai_images.generate_png("test-key", "prompt", opener=opener)


def test_restart_resumes_pending_but_not_interrupted_paid_call(scheduler):
    scheduler.accepted(accepted())
    scheduler.jobs.claim()
    scheduler.jobs.recover()
    assert scheduler.jobs.get("bird", NAME)["reason"] == "interrupted_check_provider_usage"
    assert not scheduler.run_one()


def test_configured_after_restart_resumes_without_old_attempt(scheduler, monkeypatch):
    scheduler.accepted(accepted())
    scheduler.run_one()
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    paid = Mock(return_value=png())
    monkeypatch.setattr(openai_images, "generate_png", paid)
    scheduler.jobs.recover()
    assert scheduler.run_one()
    assert paid.call_count == 2
    assert scheduler.store.lookup("bird", NAME)["generation"]["status"] == "complete"


def test_worker_generation_does_not_block_observation_api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    entered, release = Event(), Event()
    def generate(*args):
        entered.set()
        assert release.wait(5)
        return png()
    monkeypatch.setattr(openai_images, "generate_png", generate)
    config = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3", storage_root=tmp_path)
    with TestClient(create_app(config), headers=AUTH) as client:
        try:
            first = client.post("/api/observations", json=body(name=NAME))
            assert first.status_code == 201
            assert entered.wait(3)
            assert client.get("/api/health").status_code == 200
            assert client.post("/api/observations", json=body(name=NAME, stream="parallel")).status_code == 201
            assert client.app.state.generator.jobs.get("bird", NAME)["status"] == "generation_running"
        finally:
            release.set()
        deadline = time.monotonic() + 5
        while client.app.state.generator.jobs.get("bird", NAME)["status"] != "complete" and time.monotonic() < deadline:
            time.sleep(.01)
        assert client.app.state.generator.jobs.get("bird", NAME)["status"] == "complete"


def test_scheduler_failure_does_not_rollback_observation(api, monkeypatch):
    monkeypatch.setattr(api.app.state.generator.jobs, "enqueue", Mock(side_effect=OSError("private")))
    item = api.post("/api/observations", json=body(name=NAME))
    assert item.status_code == 201
    assert api.get(f"/api/observations/{item.json()['id']}").status_code == 200


def test_bat_adapter_owns_requirements_and_generation(scheduler):
    class Bat:
        required_assets = ("roost_diagram",)
        def bundled(self, name): return {}
        def validate_species(self, name): pass
        def valid(self, path, metadata): return path.exists()
        def generate(self, name, common, asset_id, directory, options):
            assert asset_id == "roost_diagram"
            (directory/"roost.txt").write_text("bat")
            return {"file":"roost.txt", "content_type":"text/plain"}
    scheduler.store.adapters["bat"] = Bat()
    scheduler.accepted(accepted(domain="bat"))
    assert scheduler.run_one()
    assert scheduler.store.lookup("bat", NAME)["generation"]["status"] == "complete"
    assert scheduler.jobs.get("bird", NAME) is None


def test_completed_job_cannot_hide_missing_assets(scheduler, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    scheduler.accepted(accepted())
    scheduler.jobs.set("bird", NAME, "complete")
    assert scheduler.store.lookup("bird", NAME)["generation"]["status"] == "incomplete"
    scheduler.accepted(accepted())
    assert scheduler.jobs.get("bird", NAME)["status"] == "generation_pending"


def test_local_failed_attempt_visible_without_queue_job(scheduler, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(openai_images, "generate_png", Mock(side_effect=RuntimeError("failed")))
    with pytest.raises(RuntimeError):
        scheduler.store.ensure("bird", NAME, "Example")
    assert scheduler.store.lookup("bird", NAME)["generation"]["status"] == "generation_failed"
