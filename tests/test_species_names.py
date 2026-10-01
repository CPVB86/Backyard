from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.species_names import TAXONOMY_PATH, localized_name, _read_names
from app.main import create_app
from app.modules.birds.models import BirdDetection
from app.modules.observations.models import Observation
from observations.policy import Policy, RawCandidate, observation_payload


@pytest.fixture
def taxonomy(tmp_path, monkeypatch):
    monkeypatch.setenv("BIRDNET_APP_DATA", str(tmp_path / "model-cache"))
    path = tmp_path / "model-cache" / TAXONOMY_PATH
    path.parent.mkdir(parents=True)
    # Real sci_name/NL pair verified against the BirdNET 1.1.1 source CSV.
    path.write_text("sci_name,common_name_nl,common_name_de\nGallinago gallinago,Watersnip,Bekassine\nMissing translation,,\n", encoding="utf-8")
    return path


@pytest.mark.parametrize("scientific,english,expected", [
    ("Gallinago gallinago", "Common Snipe", "Watersnip"),
    ("Gallinago gallinago", "Different stored name", "Watersnip"),
    ("Unknown species", "Common Snipe", "Common Snipe"),
    ("Missing translation", "Original English", "Original English"),
    ("Unknown species", None, None),
])
def test_lookup_uses_scientific_key_and_exact_english_fallback(taxonomy, scientific, english, expected):
    assert localized_name(scientific, english, "nl") == expected
    assert localized_name(scientific, english, "de") == ("Bekassine" if scientific == "Gallinago gallinago" else english)


def test_missing_cache_then_arrival_and_update(taxonomy):
    original = taxonomy.read_bytes()
    taxonomy.unlink()
    assert localized_name("Gallinago gallinago", "Common Snipe", "nl") == "Common Snipe"
    taxonomy.write_bytes(original)
    assert localized_name("Gallinago gallinago", "Common Snipe", "nl") == "Watersnip"
    taxonomy.write_text("sci_name,common_name_nl\nGallinago gallinago,\n")
    assert localized_name("Gallinago gallinago", "Common Snipe", "nl") == "Common Snipe"


def test_unreadable_or_incomplete_taxonomy_falls_back(taxonomy):
    _read_names.cache_clear()
    with patch("pathlib.Path.open", side_effect=PermissionError("unreadable")):
        assert localized_name("Gallinago gallinago", "Common Snipe", "nl") == "Common Snipe"
    taxonomy.write_bytes(b"\xff")
    assert localized_name("Gallinago gallinago", "Common Snipe", "nl") == "Common Snipe"
    taxonomy.write_text("sci_name,another_column\nGallinago gallinago,value\n")
    assert localized_name("Gallinago gallinago", "Common Snipe", "nl") == "Common Snipe"


@pytest.mark.parametrize("scientific,english,expected", [
    ("Gallinago gallinago", "Common Snipe", "Watersnip"),
    ("Unknown species", "Untranslated bird", "Untranslated bird"),
])
def test_both_api_outputs_enriched_without_changing_stored_names(taxonomy, tmp_path, scientific, english, expected):
    config = Settings(_env_file=None, database_path=tmp_path/"db.sqlite3", storage_root=tmp_path/"audio")
    with TestClient(create_app(config), headers={"Authorization":"Bearer backyard-test-token"}) as client:
        payload = {"event_id":"nl-test", "source":"test", "detected_at":"2026-10-01T00:00:00Z",
                   "scientific_name":scientific, "common_name":english, "confidence":.94}
        created = client.post("/api/birds/detections", json=payload)
        assert created.status_code == 201
        bird = created.json()
        candidate = RawCandidate("candidate", "window", "test", "stream", "bird", scientific, english,
                                 .94, 8000, 0, 24000, "2026-10-01T00:00:00+00:00", "test",
                                 {"state":"normal", "provider":"test"}, {})
        created = client.post("/api/observations", json=observation_payload([candidate], Policy()))
        assert created.status_code == 201
        observation = created.json()
        results = [bird, observation,
                   client.get("/api/birds/detections").json()[0],
                   client.get("/api/birds/latest").json(),
                   client.get("/api/birds/detections/" + bird["id"]).json(),
                   client.get("/api/observations").json()[0],
                   client.get("/api/observations/" + observation["id"]).json()]
        for result in results:
            assert result["common_name_nl"] == expected
            assert result["common_name_de"] == ("Bekassine" if scientific == "Gallinago gallinago" else english)
            assert result["common_name"] == english
            assert result["scientific_name"] == scientific
        assert "common_name_nl" not in observation["candidates"][0]
        with Session(client.app.state.engine) as session:
            for model, identity in [(BirdDetection, bird["id"]), (Observation, observation["id"])]:
                record = session.get(model, identity)
                assert record.common_name == english and record.scientific_name == scientific


def test_language_fallbacks_are_independent(taxonomy):
    taxonomy.write_text("sci_name,common_name_nl,common_name_de\nGallinago gallinago,Watersnip,\n")
    assert localized_name("Gallinago gallinago", "Common Snipe", "nl") == "Watersnip"
    assert localized_name("Gallinago gallinago", "Common Snipe", "de") == "Common Snipe"
