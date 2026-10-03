from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.observations.models import Observation
from generator.scheduler import Scheduler
from generator.store import AssetStore, species_key

AUTH = {"Authorization": "Bearer backyard-test-token"}


class Generator:
    def __init__(self, poses):
        self.poses = poses

    def lookup(self, domain, scientific_name):
        assets = {}
        for pose in self.poses.get(scientific_name, ()):
            assets[pose] = {
                "path": Path("unused.png"), "file": "unused.png", "content_type": "image/png",
                "dimensions": [420, 300], "mask": {"w": 2, "h": 2, "bits": "8A=="},
                "pose": pose, "sha256": "a" * 64,
            }
        missing = [pose for pose in ("perched", "flight") if pose not in assets]
        return {
            "domain": domain, "scientific_name": scientific_name,
            "species_key": species_key(scientific_name), "status": "ready" if not missing else "partial" if assets else "missing",
            "assets": assets, "missing_assets": missing,
            "generation": {"status": "complete" if not missing else "incomplete", "reason": None},
        }


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Scheduler, "start", lambda self: self.jobs.initialize())
    config = Settings(_env_file=None, database_path=tmp_path / "db.sqlite3", storage_root=tmp_path)
    with TestClient(create_app(config), headers=AUTH) as api:
        api.app.state.generator = Generator({
            "Parus major": ("perched", "flight"),
            "Flightus only": ("flight",),
            "Perchedus only": ("perched",),
            "Assetless bird": (),
        })
        yield api


def add(client, scientific_name, common_name, start_at, *, status="auto_accepted", domain="bird", event=None):
    identity = event or f"{scientific_name}-{status}-{start_at.isoformat()}"
    with Session(client.app.state.engine) as session:
        session.add(Observation(
            source="test", event_id=identity, ingest_hash="0" * 64, domain=domain,
            scientific_name=scientific_name, common_name=common_name,
            start_at=start_at, end_at=start_at + timedelta(seconds=3), best_confidence=.9,
            status=status, decision={}, policy={}, clip={}, evidence_kind="permanent",
            created_at=start_at,
        ))
        session.commit()


def test_recent_filters_window_aggregates_and_attaches_all_asset_states(client, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("app.modules.avian_visitors.service.localized_name",
                        lambda sci, english, locale: {("Parus major", "nl"): "Koolmees"}.get((sci, locale), english))
    add(client, "Parus major", "Great Tit", now - timedelta(hours=3), event="accepted-1")
    add(client, "Parus major", "Great Tit", now - timedelta(hours=1), status="human_confirmed", event="accepted-2")
    add(client, "Parus major", "Great Tit", now - timedelta(hours=2), status="pending_review", event="pending")
    add(client, "Parus major", "Great Tit", now - timedelta(hours=2), status="human_rejected", event="rejected")
    add(client, "Parus major", "Great Tit", now - timedelta(hours=25), event="old")
    add(client, "Pipistrellus pipistrellus", "Common Pipistrelle", now - timedelta(hours=1), domain="bat")
    add(client, "Flightus only", "Flight only", now - timedelta(minutes=50))
    add(client, "Perchedus only", "Perched only", now - timedelta(minutes=40))
    add(client, "Assetless bird", "Assetless", now - timedelta(minutes=30))

    response = client.get("/api/avian-visitors/recent?hours=24")
    assert response.status_code == 200
    result = response.json()
    assert result["locale"] == "nl" and result["hours"] == 24
    assert result["observation_count"] == 5
    species = {item["scientific_name"]: item for item in result["species"]}
    tit = species["Parus major"]
    assert tit["common_name"] == "Koolmees" and tit["count"] == 2
    assert datetime.fromisoformat(tit["first_observed_at"]) == now - timedelta(hours=3)
    assert datetime.fromisoformat(tit["last_observed_at"]) == now - timedelta(hours=1)
    assert set(tit["assets"]) == {"perched", "flight"}
    assert tit["assets"]["perched"]["dimensions"] == [420, 300]
    assert tit["assets"]["perched"]["mask"] == {"w": 2, "h": 2, "bits": "8A=="}
    assert tit["assets"]["perched"]["url"].startswith("/api/generator/bird/assets/")
    assert species["Flightus only"]["missing_assets"] == ["perched"]
    assert set(species["Flightus only"]["assets"]) == {"flight"}
    assert species["Perchedus only"]["missing_assets"] == ["flight"]
    assert set(species["Perchedus only"]["assets"]) == {"perched"}
    assert species["Assetless bird"]["generator_status"] == "missing"
    assert species["Assetless bird"]["assets"] == {}


@pytest.mark.parametrize("locale,expected", [("nl", "Koolmees"), ("en", "Great Tit"), ("de", "Kohlmeise")])
def test_locale_selection(client, monkeypatch, locale, expected):
    monkeypatch.setattr("app.modules.avian_visitors.service.localized_name",
                        lambda sci, english, language: {"nl": "Koolmees", "de": "Kohlmeise"}.get(language))
    add(client, "Parus major", "Great Tit", datetime.now(timezone.utc) - timedelta(minutes=1))
    item = client.get("/api/avian-visitors/recent", params={"locale": locale}).json()["species"][0]
    assert item["common_name"] == expected


def test_name_fallback_and_validation(client, monkeypatch):
    monkeypatch.setattr("app.modules.avian_visitors.service.localized_name", lambda *args: None)
    now = datetime.now(timezone.utc)
    add(client, "English fallback", "Existing English", now - timedelta(minutes=2))
    add(client, "Scientific fallback", "", now - timedelta(minutes=1))
    values = {item["scientific_name"]: item["common_name"]
              for item in client.get("/api/avian-visitors/recent?locale=de").json()["species"]}
    assert values == {"English fallback": "Existing English", "Scientific fallback": "Scientific fallback"}
    assert client.get("/api/avian-visitors/recent?locale=fr").status_code == 422
    assert client.get("/api/avian-visitors/recent?hours=0").status_code == 422


def test_collage_is_served_and_contains_no_generator_asset_copies(client):
    page = client.get("/avian-visitors/")
    assert page.status_code == 200 and "Recent gehoord" in page.text
    static = Path(__file__).parents[1] / "app/modules/avian_visitors/static"
    assert not [path for path in static.rglob("*") if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".json"}]
    assert client.get("/api/avian-visitors/recent", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_presentation_uses_original_view_geometry_and_mask_packer(client):
    root = Path(__file__).parents[1] / "app/modules/avian_visitors/static"
    html = (root / "index.html").read_text(encoding="utf-8")
    css = (root / "avian-visitors.css").read_text(encoding="utf-8")
    js = (root / "avian-visitors.js").read_text(encoding="utf-8")

    assert 'class="static-head"' in html
    assert 'class="view" id="v0"' in html
    assert "flex:0 0 100%" in css
    assert "width:300%" not in css and "33.333333" not in js
    assert 'translateX(-"+(state.view*100)+"%)' in js
    assert "function maskPack(" in js and "function tuning(" in js
    assert "x=-99999" in js
    assert "var columns=" not in js
    assert "radial-gradient" not in css


def test_existing_bundled_generator_assets_are_exposed(client):
    client.app.state.generator = AssetStore(client.app.state.settings.resolved_storage_root / "generator-real")
    add(client, "Turdus migratorius", "American Robin", datetime.now(timezone.utc) - timedelta(minutes=1))
    item = client.get("/api/avian-visitors/recent?locale=en").json()["species"][0]
    assert item["generator_status"] == "ready"
    assert set(item["assets"]) == {"perched", "flight"}
    for pose in ("perched", "flight"):
        assert item["assets"][pose]["dimensions"]
        assert item["assets"][pose]["mask"]["bits"]
        assert item["assets"][pose]["url"].endswith("/" + pose)


def test_stats_and_lifelist_only_use_accepted_birds_and_group_by_identity(client, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("app.modules.avian_visitors.service.localized_name",
                        lambda sci, english, locale: "Koolmees" if sci == "Parus major" and locale == "nl" else english)
    add(client, "Parus major", "Great Tit", now - timedelta(hours=2), event="tit-1")
    add(client, "Parus major", "Great Tit", now - timedelta(hours=1), status="human_confirmed", event="tit-2")
    add(client, "Flightus only", "Flight only", now - timedelta(hours=30), event="old-flight")
    add(client, "Perchedus only", "Perched only", now - timedelta(minutes=20), status="pending_review")
    add(client, "Assetless bird", "Assetless", now - timedelta(minutes=10), status="human_rejected")
    add(client, "Pipistrellus pipistrellus", "Common Pipistrelle", now - timedelta(minutes=5), domain="bat")

    stats = client.get("/api/avian-visitors/stats?hours=24&locale=nl").json()
    assert stats["observation_count"] == 2 and stats["species_count"] == 1
    assert stats["species"][0]["scientific_name"] == "Parus major"
    assert stats["species"][0]["common_name"] == "Koolmees" and stats["species"][0]["count"] == 2
    assert stats["all_time_observation_count"] == 3 and stats["all_time_species_count"] == 2
    assert sum(item["count"] for item in stats["timeline"]) == 2
    assert sum(stats["rhythm"]) == 2
    assert stats["hourly_species"][0]["scientific_name"] == "Parus major"
    assert sum(stats["hourly_species"][0]["counts"]) == 2

    life = client.get("/api/avian-visitors/lifelist?locale=nl").json()
    assert life["observation_count"] == 3 and life["species_count"] == 2
    species = {item["scientific_name"]: item for item in life["species"]}
    assert set(species) == {"Parus major", "Flightus only"}
    assert species["Parus major"]["count"] == 2 and species["Parus major"]["common_name"] == "Koolmees"
    assert set(species["Flightus only"]["assets"]) == {"flight"}

    recent = client.get("/api/avian-visitors/recent?hours=24&locale=nl").json()
    assert recent["observation_count"] == 2 and recent["species"][0]["count"] == 2


def test_stats_periods_and_empty_public_views(client):
    empty_stats = client.get("/api/avian-visitors/stats?hours=24").json()
    empty_life = client.get("/api/avian-visitors/lifelist").json()
    assert empty_stats["observation_count"] == 0 and empty_stats["species"] == []
    assert sum(item["count"] for item in empty_stats["timeline"]) == 0
    assert empty_stats["hourly_species"] == []
    assert empty_life["observation_count"] == 0 and empty_life["species"] == []

    now = datetime.now(timezone.utc)
    add(client, "Parus major", "Great Tit", now - timedelta(hours=2), event="inside")
    add(client, "Flightus only", "Flight only", now - timedelta(hours=26), event="outside")
    day = client.get("/api/avian-visitors/stats?hours=24").json()
    week = client.get("/api/avian-visitors/stats?hours=168").json()
    assert day["observation_count"] == 1 and day["timeline_granularity"] == "hour"
    assert week["observation_count"] == 2 and week["timeline_granularity"] == "day"
    assert client.get("/api/avian-visitors/stats?locale=fr").status_code == 422
    assert client.get("/api/avian-visitors/lifelist?locale=fr").status_code == 422
