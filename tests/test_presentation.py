from datetime import datetime, timedelta, timezone
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.core.config import Settings
from app.main import create_app
from app.modules.observations.models import Observation
from app.modules.presentation.service import snapshot
import pytest

NOW = datetime(2026, 10, 8, 0, 30, tzinfo=timezone.utc)
HEADERS = {"Authorization": "Bearer backyard-test-token"}


@pytest.fixture
def data(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path/"test.sqlite3", storage_root=tmp_path/"data")
    with TestClient(create_app(config)) as client:
        with Session(client.app.state.engine) as session:
            for index, (name, hours, status) in enumerate([
                ("Turdus merula", 0, "human_confirmed"), ("Turdus merula", 1, "auto_accepted"),
                ("Parus major", 48, "human_confirmed"), ("Erithacus rubecula", 800, "auto_accepted"),
                ("Corvus corax", 0, "human_rejected"), ("Pica pica", 0, "pending_review")]):
                session.add(Observation(id=f"test-{index}", source="test", event_id=str(index), ingest_hash="a"*64,
                    domain="bird", scientific_name=name, common_name=name, start_at=NOW-timedelta(hours=hours),
                    end_at=NOW-timedelta(hours=hours), best_confidence=.9, status=status, decision={}, policy={}, clip={}, evidence_kind="permanent"))
            session.commit()
        yield client


def test_periods_rankings_and_empty_bats(data):
    engine = data.app.state.engine
    for period, total in [("today",2), ("24h",2), ("7d",3), ("30d",3), ("all",4)]:
        result = snapshot(engine,"birds",period,now=NOW)
        assert result["stats"]["total_observations"] == total
        assert result["stats"]["today_observations"] == 2  # previous UTC date is still today locally
        assert len(result["rankings"]["random"]) == len(set(result["rankings"]["random"]))
    result = snapshot(engine,"birds","all",now=NOW)
    by_id = {row["species_id"]:row for row in result["species"]}
    assert by_id[result["rankings"]["most"][0]]["scientific_name"] == "Turdus merula"
    assert by_id[result["rankings"]["first"][0]]["scientific_name"] == "Erithacus rubecula"
    assert by_id[result["rankings"]["rarest"][0]]["scientific_name"] == "Erithacus rubecula"
    assert result["stats"]["unique_species"] == 3 and result["stats"]["active_days"] == 3
    assert snapshot(engine,"birds","today",now=NOW)["stats"]["new_species"] == 1
    bats = snapshot(engine,"bats","all",now=NOW)
    assert bats["species"] == [] and bats["stats"]["total_observations"] == 0 and bats["stats"]["last_activity"] is None


def test_corrections_are_immediate_and_original_evidence_unchanged(data):
    engine = data.app.state.engine
    with Session(engine) as session:
        row = session.get(Observation,"test-0")
        row.review = {"scientific_name_override":"Gallus gallus", "identity_override":"otje"}
        session.get(Observation,"test-1").status = "human_rejected"
        session.commit()
    result = snapshot(engine,"birds","all",now=NOW)
    assert result["stats"]["total_observations"] == 3
    assert "Turdus merula" not in {row["scientific_name"] for row in result["species"]}
    otje = next(row for row in result["species"] if row["identity"] == "otje")
    assert otje["name"] == "Otje" and otje["assets"]["perched"] == "otje_perched"
    with Session(engine) as session:
        assert session.get(Observation,"test-0").scientific_name == "Turdus merula"


def test_auth_validation_and_fixed_timezone(data):
    assert data.get("/api/presentation/bats").status_code == 401
    assert data.get("/api/presentation/bats",headers=HEADERS).json()["stats"]["total_observations"] == 0
    for query in ("period=bad", "timezone=bad", "timezone=../../private"):
        assert data.get("/api/presentation/birds?"+query, headers=HEADERS).status_code == 422
    assert data.get("/api/presentation/birds",params={"timezone":"+02:00"}, headers=HEADERS).status_code == 200
    assert data.get("/api/presentation/invalid",headers=HEADERS).status_code == 422


def test_future_bat_data_uses_catalog_and_stays_separate(data):
    from app.modules.species.models import Species
    engine = data.app.state.engine
    with Session(engine) as session:
        session.add(Species(domain="bat", scientific_name="Pipistrellus pipistrellus", common_name_nl="Gewone dwergvleermuis",
            source="test", source_species_id=1, source_url="https://waarneming.nl/species/1/",
            wikipedia_nl_url="https://nl.wikipedia.org/wiki/Gewone_dwergvleermuis", source_metadata={}, imported_at=NOW))
        session.add(Observation(source="test", event_id="bat", ingest_hash="b"*64, domain="bat",
            scientific_name="Pipistrellus pipistrellus", common_name="Common pipistrelle", start_at=NOW, end_at=NOW,
            best_confidence=.8, status="auto_accepted", decision={}, policy={}, clip={}, evidence_kind="permanent"))
        session.commit()
    result = snapshot(engine,"bats","all",now=NOW)
    row, = result["species"]
    assert row["name"] == "Gewone dwergvleermuis" and row["identity"] is None
    assert row["observations_url"] == "https://waarneming.nl/species/1/"
    assert row["wikipedia_url"].startswith("https://nl.wikipedia.org/")
    assert result["stats"]["total_observations"] == 1
    assert snapshot(engine,"birds","all",now=NOW)["stats"]["total_observations"] == 4


def test_explicit_otje_combines_marked_species_only(data):
    engine = data.app.state.engine
    with Session(engine) as session:
        for index, (name, identity, status, hours) in enumerate([
            ("Gallus gallus", "otje", "human_confirmed", 3),
            ("Gallus domesticus", "otje", "human_confirmed", 0),
            ("Gallus gallus", None, "human_confirmed", 0),
            ("Gallus gallus", "otje", "human_rejected", 0)]):
            session.add(Observation(source="otje-test", event_id=str(index), ingest_hash="a"*64,
                domain="bird", scientific_name=name, common_name=name, start_at=NOW-timedelta(hours=hours),
                end_at=NOW, best_confidence=.9, status=status, decision={}, policy={}, clip={}, evidence_kind="permanent",
                review={"identity_override": identity}))
        session.commit()
    result = snapshot(engine, "birds", "all", now=NOW, identity_filter="otje")
    row, = result["species"]
    assert row["name"] == "Otje" and row["count"] == 2
    assert row["first_seen"] == NOW-timedelta(hours=3) and row["last_seen"] == NOW
    assert row["scientific_name"] == "Gallus domesticus" and row["assets"]["perched"] == "otje_perched"
    assert result["identity"] == "otje" and result["stats"]["total_observations"] == 2
    assert result["rankings"]["last"] == [row["species_id"]]
    today = snapshot(engine, "birds", "today", now=NOW, identity_filter="otje")
    assert today["species"][0]["count"] == 1
    assert today["species"][0]["species_id"] == row["species_id"]
    empty = snapshot(engine, "birds", "24h", now=NOW+timedelta(days=2), identity_filter="otje")
    assert empty["species"] == [] and empty["stats"]["total_observations"] == 0
    for target in ("bats?identity=otje", "birds?identity=unknown"):
        assert data.get("/api/presentation/"+target, headers=HEADERS).status_code == 422
    assert data.get("/api/presentation/birds?identity=otje", headers=HEADERS).json()["identity"] == "otje"
