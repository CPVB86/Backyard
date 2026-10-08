from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.main import create_app
from app.modules.birds.storage import audio_path
from app.modules.observations.models import Observation
from app.modules.species.models import Species
from generator.scheduler import Scheduler
from generator.store import AssetStore, species_key

AUTH = {"Authorization": "Bearer backyard-test-token"}


class Generator:
    def __init__(self, poses, identities=True):
        self.poses = poses
        self.identities = identities

    def lookup(self, domain, scientific_name):
        assets = {}
        for pose in self.poses.get(scientific_name, ()):
            assets[pose] = {
                "path": Path("unused.png"), "file": "unused.png", "content_type": "image/png",
                "dimensions": [420, 300], "mask": {"w": 2, "h": 2, "bits": "8A=="},
                "pose": pose, "sha256": "a" * 64,
            }
        if scientific_name == "Gallus gallus" and self.identities:
            for asset_id, pose in (("otje_perched", "perched"), ("otje_flight", "flight")):
                assets[asset_id] = {
                    "path": Path("unused.png"), "file": asset_id + ".png", "content_type": "image/png",
                    "dimensions": [500, 410], "mask": {"w": 3, "h": 2, "bits": "/A=="},
                    "pose": pose, "sha256": "b" * 64,
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
            "Gallus gallus": ("perched", "flight"),
        })
        yield api


def add(client, scientific_name, common_name, start_at, *, status="auto_accepted", domain="bird", event=None,
        review=None, confidence=.9, audio=False):
    identity = event or f"{scientific_name}-{status}-{start_at.isoformat()}"
    storage_key = (f"birds/audio/{start_at:%Y/%m/%d}/00000000-0000-0000-0000-000000000001.wav"
                   if audio else None)
    with Session(client.app.state.engine) as session:
        record = Observation(
            source="test", event_id=identity, ingest_hash="0" * 64, domain=domain,
            scientific_name=scientific_name, common_name=common_name,
            start_at=start_at, end_at=start_at + timedelta(seconds=3), best_confidence=confidence,
            status=status, decision={}, policy={}, clip={}, evidence_kind="permanent",
            audio={"status": "available"} if audio else None,
            storage_key=storage_key,
            review=review, created_at=start_at,
        )
        session.add(record)
        session.commit()
        record_id = record.id
    if audio:
        path = audio_path(client.app.state.settings.resolved_storage_root, storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"otje-audio")
    return record_id


def add_gallus_catalog(client, now):
    with Session(client.app.state.engine) as session:
        session.add(Species(
            domain="bird", scientific_name="Gallus gallus", common_name_nl="Bankivahoen",
            authority="Linnaeus, 1758", family="Phasianidae (Fazantachtigen)",
            source="waarneming.nl", source_species_id=777, source_url="https://waarneming.nl/species/777/",
            rarity="algemeen", status="inheems", source_metadata={},
            wikipedia_nl_url="https://nl.wikipedia.org/wiki/Bankivahoen",
            wikipedia_title_nl="Bankivahoen", wikipedia_match_status="matched",
            wikipedia_en_url="https://en.wikipedia.org/wiki/Red_junglefowl",
            summary_nl="Onderliggende soorttekst.", fact_nl="Onderliggend soortfeit.",
            encyclopedia_source="wikipedia", imported_at=now, updated_at=now,
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
    images = [path.name for path in static.rglob("*") if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp", ".json"}]
    assert images == ["nest.webp"]
    assert client.get("/api/avian-visitors/recent", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_atlas_search_uses_full_catalog_names_and_real_observation_counts(client):
    now = datetime.now(timezone.utc)
    with Session(client.app.state.engine) as session:
        session.add_all([
            Species(domain="bird", scientific_name="Corvus corone", common_name_nl="Zwarte kraai",
                    source="waarneming.nl", source_species_id=901, source_url="https://waarneming.nl/species/901/",
                    source_metadata={}, wikipedia_title_en="Carrion Crow", imported_at=now, updated_at=now),
            Species(domain="bird", scientific_name="Corvus splendens", common_name_nl="Huiskraai",
                    source="waarneming.nl", source_species_id=902, source_url="https://waarneming.nl/species/902/",
                    source_metadata={}, imported_at=now, updated_at=now),
            Species(domain="bird", scientific_name="Corvus ossifragus", common_name_nl="Viskraai",
                    source="waarneming.nl", source_species_id=903, source_url="https://waarneming.nl/species/903/",
                    source_metadata={}, imported_at=now, updated_at=now),
        ])
        session.commit()
    add(client, "Corvus corone", "Carrion Crow", now - timedelta(minutes=5), event="crow")

    found = client.get("/api/avian-visitors/search?q=KRAAI").json()["results"]
    assert {item["common_name_nl"] for item in found} == {"Zwarte kraai", "Huiskraai", "Viskraai"}
    assert next(item for item in found if item["scientific_name"] == "Corvus corone")["observation_count"] == 1
    assert client.get("/api/avian-visitors/search?q=corone&limit=1").json()["results"][0]["scientific_name"] == "Corvus corone"

    detail = client.get("/api/avian-visitors/detail/Corvus%20splendens?locale=nl").json()
    assert detail["identity"]["common_name"] == "Huiskraai"
    assert detail["observations"]["total"] == 0
    assert "presentation" not in detail


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


def test_explicit_otje_identity_is_local_presentation_over_unchanged_species_data(client, monkeypatch):
    now = datetime.now(timezone.utc)
    monkeypatch.setattr("app.modules.avian_visitors.service.localized_name",
                        lambda sci, english, locale: "Bankivahoen" if sci == "Gallus gallus" and locale == "nl" else english)
    add_gallus_catalog(client, now)
    normal_at = now - timedelta(hours=2)
    otje_at = now - timedelta(hours=1)
    add(client, "Gallus gallus", "Red Junglefowl", normal_at, event="normal-gallus")
    add(client, "Gallus gallus", "Red Junglefowl", otje_at, status="human_confirmed",
        event="otje", review={"action": "confirm", "identity_override": "otje"},
        confidence=.972, audio=True)
    add(client, "Parus major", "Great Tit", now - timedelta(minutes=10), event="other-bird",
        review={"action": "confirm", "identity_override": "otje"})

    recent = client.get("/api/avian-visitors/recent?hours=24&locale=nl").json()
    gallus = [item for item in recent["species"] if item["scientific_name"] == "Gallus gallus"]
    assert len(gallus) == 2
    normal = next(item for item in gallus if item["identity_id"] is None)
    otje = next(item for item in gallus if item["identity_id"] == "otje")
    assert normal["common_name"] == "Bankivahoen" and normal["count"] == 1
    assert otje["common_name"] == "Otje" and otje["subtitle"] == "Barnevelder" and otje["count"] == 1
    assert otje["scientific_name"] == "Gallus gallus" and otje["common_name_en"] == "Red Junglefowl"
    assert set(otje["assets"]) == {"perched", "flight"}
    assert {pose: data["url"].rsplit("/", 1)[-1] for pose, data in otje["assets"].items()} == {
        "perched": "otje_perched", "flight": "otje_flight"}
    assert otje["assets"]["perched"]["mask"] == {"w": 3, "h": 2, "bits": "/A=="}
    assert {pose: data["url"].rsplit("/", 1)[-1] for pose, data in normal["assets"].items()} == {
        "perched": "perched", "flight": "flight"}
    assert next(item for item in recent["species"] if item["scientific_name"] == "Parus major")["common_name"] == "Great Tit"

    stats = client.get("/api/avian-visitors/stats?hours=24&locale=nl").json()
    stats_gallus = [item for item in stats["species"] if item["scientific_name"] == "Gallus gallus"]
    assert {(item["identity_id"], item["common_name"], item["count"]) for item in stats_gallus} == {
        (None, "Bankivahoen", 1), ("otje", "Otje", 1)}
    assert {item["identity_id"] for item in stats["hourly_species"]
            if item["scientific_name"] == "Gallus gallus"} == {None, "otje"}
    life_gallus = [item for item in client.get("/api/avian-visitors/lifelist?locale=nl").json()["species"]
                   if item["scientific_name"] == "Gallus gallus"]
    assert {(item["identity_id"], item["common_name"]) for item in life_gallus} == {
        (None, "Bankivahoen"), ("otje", "Otje")}

    response = client.get("/api/avian-visitors/detail/Gallus%20gallus?locale=nl&identity=otje")
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["presentation"] == {"id": "otje", "display_name": "Otje", "subtitle": "Barnevelder",
                                      "images": {"perched": "otje.png", "flight": "otje-2.png"}}
    assert detail["local_content"]["summary_nl"].startswith("De barnevelder is een middelzwaar kippenras")
    assert detail["local_content"]["fact_nl"].startswith("Otje is één van de drie kippen")
    assert detail["identity"]["scientific_name"] == "Gallus gallus"
    assert detail["identity"]["common_name"] == "Bankivahoen"
    assert detail["encyclopedia"]["summary_nl"] == "Onderliggende soorttekst."
    assert detail["encyclopedia"]["wikipedia_nl_url"] == "https://nl.wikipedia.org/wiki/Bankivahoen"
    assert detail["waarneming"]["url"] == "https://waarneming.nl/species/777/"
    assert detail["observations"]["total"] == 1
    assert detail["observations"]["highest_confidence"] == .972
    assert datetime.fromisoformat(detail["observations"]["first_observed_at"]) == otje_at
    assert detail["audio"]["url"].startswith("/api/observations/")
    assert datetime.fromisoformat(detail["audio"]["timestamp"]) == otje_at
    assert detail["generator"]["assets"]["perched"]["url"].endswith("/otje_perched")
    assert detail["generator"]["assets"]["flight"]["url"].endswith("/otje_flight")

    fallback = client.get("/api/avian-visitors/detail/Gallus%20gallus?locale=nl&identity=unknown").json()
    assert "presentation" not in fallback and "local_content" not in fallback
    assert fallback["identity"]["common_name"] == "Bankivahoen"
    assert fallback["observations"]["total"] == 2
    assert fallback["encyclopedia"]["wikipedia_nl_url"] == detail["encyclopedia"]["wikipedia_nl_url"]

    client.app.state.generator = Generator({"Gallus gallus": ("perched", "flight")}, identities=False)
    safe_fallback = client.get("/api/avian-visitors/recent?hours=24&locale=nl").json()
    fallback_otje = next(item for item in safe_fallback["species"] if item["identity_id"] == "otje")
    assert {pose: data["url"].rsplit("/", 1)[-1] for pose, data in fallback_otje["assets"].items()} == {
        "perched": "perched", "flight": "flight"}


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
