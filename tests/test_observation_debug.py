import json
from dataclasses import replace
from threading import Event
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from app.core.config import Settings
from app.main import create_app
from detector.debug_observation import main, run
from detector.monitor_http import HTTPFailure, Uploader
from detector.stream import MonitorConfig, Metrics, Upload
from observations.policy import Policy, validate_event
from test_observations_api import raw


class APITransport:
    def __init__(self, client):
        self.client, self.calls = client, []
    def request(self, method, path, body, content_type):
        self.calls.append((method, path, body))
        response = self.client.request(method, path, content=body, headers={"Content-Type": content_type})
        if response.status_code not in (200, 201):
            raise HTTPFailure(response.status_code)
        return response.json()


@pytest.mark.parametrize("case,status,evidence,supports", [
    ("bird-auto", "auto_accepted", "permanent", 1),
    ("bird-review", "pending_review", "review", 1),
    ("bird-unusual", "review_recommended", "permanent", 1),
    ("bird-overlap", "auto_accepted", "permanent", 3),
    ("bat", "auto_accepted", "permanent", 1),
    ("chimpanzee", "discarded", None, 0),
])
def test_explicit_debug_cases_through_real_api(tmp_path, case, status, evidence, supports):
    settings = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3", storage_root=tmp_path/"audio")
    with TestClient(create_app(settings)) as client:
        transport = APITransport(client)
        item = run(case, Policy(), transport)
        assert item["status"] == status
        if supports:
            assert item["evidence_kind"] == evidence
            assert item["supporting_candidate_count"] == supports
            assert all(c["metadata"]["synthetic"] for c in item["candidates"])
            assert client.get(item["audio_url"]).status_code == 200
            assert len(list(settings.resolved_storage_root.rglob("*.wav"))) == 1
        else:
            assert client.get("/api/observations").json() == []
            assert not list(settings.resolved_storage_root.rglob("*.wav"))
            assert [c[0] for c in transport.calls] == ["POST"]


def test_debug_default_is_dry_run_without_network(capsys):
    with patch("detector.debug_observation.HTTPTransport", side_effect=AssertionError("no HTTP")):
        assert main(["--case", "bird-review"]) == 0
    item = json.loads(capsys.readouterr().out)
    assert not item["sent"] and item["decision"]["status"] == "pending_review"


def test_sample_range_cannot_count_twice_under_another_window_id():
    first = raw()
    with pytest.raises(ValueError, match="Duplicate"):
        validate_event([first, replace(first, candidate_id="other", window_id="other")], Policy())


def test_supports_must_be_in_monotonic_sample_order():
    with pytest.raises(ValueError, match="ordered"):
        validate_event([raw(0), raw(12000), raw(8000)], Policy())


def test_policy_file_changes_fingerprint_and_unknown_fields_fail(tmp_path):
    path = tmp_path/"policy.json"
    path.write_text('{"review_lower":0.65}', encoding="utf-8")
    assert Policy.load(path).review_lower == .65
    assert Policy.load(path).fingerprint != Policy().fingerprint
    path.write_text('{"misspelled":1}', encoding="utf-8")
    with pytest.raises(TypeError):
        Policy.load(path)


def test_new_observation_uploader_retries_identical_post_and_put(tmp_path):
    from detector.debug_observation import build, silence
    settings = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3", storage_root=tmp_path/"audio")
    with TestClient(create_app(settings)) as client:
        base = APITransport(client)
        lost = set()
        class LostReplies:
            def request(self, method, path, body, content_type):
                result = base.request(method, path, body, content_type)
                if method not in lost:
                    lost.add(method)
                    raise OSError("response lost after server commit")
                return result
        payload, _ = build("bird-auto", Policy())
        metrics = Metrics()
        stop = Event()
        with patch.object(stop, "wait", return_value=False):
            assert Uploader(MonitorConfig(), metrics, stop, LostReplies()).send(Upload(payload, silence(payload)))
        posts = [c[2] for c in base.calls if c[0] == "POST"]
        puts = [c[2] for c in base.calls if c[0] == "PUT"]
        assert len(posts) == len(puts) == 2
        assert posts[0] == posts[1] and puts[0] == puts[1]
        assert len(client.get("/api/observations").json()) == 1
        assert len(list(settings.resolved_storage_root.rglob("*.wav"))) == 1
        assert metrics.snapshot()["permanent_clips"] == 1


def test_swagger_nested_candidate_schema_resolves(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3", storage_root=tmp_path/"audio")
    with TestClient(create_app(settings)) as client:
        spec = client.get("/openapi.json").json()
        schema = spec["paths"]["/api/observations"]["post"]["requestBody"]["content"]["application/json"]["schema"]
        assert schema["properties"]["candidates"]["items"]["properties"]["domain"]["enum"] == ["bird","bat","unsupported","unknown"]
        assert "#/$defs/" not in json.dumps(spec)

def test_low_support_does_not_boost_auto_acceptance():
    from observations.policy import decision
    outcome = decision([raw(score=.88), raw(start=12000, score=.1)], Policy())
    assert outcome["status"] == "pending_review"
    assert outcome["supporting_windows"] == 1
