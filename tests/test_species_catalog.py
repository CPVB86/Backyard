from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.core.migrations import migrate
from app.main import create_app
from app.modules.observations.models import Observation
from app.modules.species.importer import ImportFormatError, import_csv
from app.modules.species.models import Species

HEAD = "id,name,scientific name,type,parent species id,family,group,authority,rarity,status,obscurity,link\n"


@pytest.fixture
def api(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "db.sqlite3", storage_root=tmp_path)
    with TestClient(create_app(settings), headers={"Authorization": "Bearer backyard-test-token"}) as client:
        yield client


def csv_file(tmp_path, rows):
    path = tmp_path / "species.csv"
    path.write_text(HEAD + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def observation(scientific_name, status, at, confidence=.8, *, audio=False):
    identity = "00000000-0000-0000-0000-" + f"{int(at.timestamp()) % 10**12:012d}"
    return Observation(id=identity, source="test", event_id=identity, ingest_hash="a"*64,
        domain="bird", scientific_name=scientific_name, common_name="Great Tit", start_at=at,
        end_at=at+timedelta(seconds=1), best_confidence=confidence, status=status,
        decision={}, policy={}, clip={}, evidence_kind="permanent" if audio else "review",
        audio={"status":"available","size_bytes":1,"sha256":"x"} if audio else None,
        storage_key=f"birds/audio/{at:%Y/%m/%d}/{identity}.wav" if audio else None,
        created_at=at)


def test_csv_import_utf8_idempotent_update_nulls_and_lookup(api, tmp_path):
    path = csv_file(tmp_path, [
        '1,Koolmees,Parus major,soort,,Paridae (Mezen),Vogels,"Linnaeus, 1758",algemeen,inheems,,https://waarneming.nl/species/1/',
        '2,Één vogel,Testus utf8,soort,1,,Vogels,,,,,https://waarneming.nl/species/2/',
    ])
    first = import_csv(api.app.state.engine, path, domain="bird")
    assert first == {"inserted": 2, "updated": 0, "unchanged": 0, "rejected": 0, "errors": []}
    assert import_csv(api.app.state.engine, path, domain="bird")["unchanged"] == 2
    with Session(api.app.state.engine) as session:
        utf8 = session.query(Species).filter_by(scientific_name="Testus utf8").one()
        assert utf8.common_name_nl == "Één vogel" and utf8.family is None
        assert utf8.parent_source_species_id == 1
    path.write_text(path.read_text(encoding="utf-8").replace("algemeen,inheems", "zeldzaam,exoot"), encoding="utf-8")
    changed = import_csv(api.app.state.engine, path, domain="bird")
    assert changed["updated"] == 1 and changed["unchanged"] == 1
    item = api.get("/api/species/bird/Parus%20major?locale=nl").json()
    assert item["identity"]["common_name"] == "Koolmees"
    assert item["waarneming"]["rarity"] == "zeldzaam" and item["waarneming"]["status"] == "exoot"
    assert item["encyclopedia"] == {"wikipedia_nl_url": None, "summary_nl": None, "fact_nl": None, "source": None}


def test_duplicate_conflicts_and_bad_format_are_deterministic(api, tmp_path):
    duplicate = csv_file(tmp_path, [
        "1,A,Same bird,soort,,,Vogels,,,,,https://waarneming.nl/species/1/",
        "2,B,Same bird,soort,,,Vogels,,,,,https://waarneming.nl/species/2/",
    ])
    report = import_csv(api.app.state.engine, duplicate, domain="bird")
    assert report["rejected"] == 1 and report["inserted"] == 0
    bad = tmp_path / "bad.csv"; bad.write_text("name,scientific name\nA,B\n", encoding="utf-8")
    with pytest.raises(ImportFormatError, match="Unexpected CSV columns"):
        import_csv(api.app.state.engine, bad, domain="bird")


def test_bat_filter_selects_taxonomic_families(api, tmp_path):
    path = csv_file(tmp_path, [
        "1,Gewone Dwergvleermuis,Pipistrellus pipistrellus,soort,,Gladneuzen (Vespertilionidae),Zoogdieren,,algemeen,inheems,,x",
        "2,Aardmuis,Microtus agrestis,soort,,Cricetidae,Zoogdieren,,algemeen,inheems,,x",
        "3,Meervleermuis,Myotis dasycneme,soort,,Gladneuzen (Vespertilionidae),Zoogdieren,,zeldzaam,inheems,,x",
    ])
    assert import_csv(api.app.state.engine, path, domain="bat", only_bats=True)["inserted"] == 2


def test_detail_accepted_stats_audio_generator_and_no_generation(api, tmp_path):
    path = csv_file(tmp_path, ["1,Koolmees,Parus major,soort,,Paridae,Vogels,,algemeen,inheems,,x"])
    import_csv(api.app.state.engine, path, domain="bird")
    now = datetime.now(timezone.utc)
    rows = [observation("Parus major", "auto_accepted", now-timedelta(hours=1), .82, audio=True),
            observation("Parus major", "human_confirmed", now-timedelta(days=3), .96),
            observation("Parus major", "pending_review", now-timedelta(minutes=5), .99),
            observation("Parus major", "human_rejected", now-timedelta(minutes=3), 1.0)]
    audio_id, audio_key = rows[0].id, rows[0].storage_key
    with Session(api.app.state.engine) as session:
        session.add_all(rows); session.commit()
    audio_path = api.app.state.settings.resolved_storage_root / audio_key
    audio_path.parent.mkdir(parents=True); audio_path.write_bytes(b"x")
    with patch.object(api.app.state.generator, "lookup", wraps=api.app.state.generator.lookup) as lookup, \
         patch.object(api.app.state.generator, "ensure", side_effect=AssertionError("GET generated")):
        result = api.get("/api/species/bird/Parus%20major?locale=en")
    assert result.status_code == 200 and lookup.call_count == 1
    body = result.json()
    assert body["observations"]["total"] == 2 and body["observations"]["today"] == 1
    assert body["observations"]["last_7_days"] == 2 and body["observations"]["highest_confidence"] == .96
    assert body["observations"]["first_observed_at"] < body["observations"]["last_observed_at"]
    assert body["audio"]["observation_id"] == audio_id and body["audio"]["availability"] is True
    assert body["generator"]["status"] in ("ready", "missing", "partial")


def test_catalog_only_missing_assets_unknown_and_locale_fallback(api, tmp_path):
    path = csv_file(tmp_path, ["4,Nieuwe vogel,Novus avis,soort,,,,,zeldzaam,inheems,,x"])
    import_csv(api.app.state.engine, path, domain="bird")
    item = api.get("/api/species/bird/Novus%20avis?locale=de").json()
    assert item["identity"]["common_name"] == "Novus avis"
    assert item["identity"]["common_name_nl"] == "Nieuwe vogel"
    assert item["observations"]["total"] == 0 and item["audio"] is None
    assert item["generator"]["assets"] == {}
    assert api.get("/api/species/bird/Unknown%20bird").status_code == 404


def test_schema_v2_migrates_catalog_without_touching_observations(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path / "v2.sqlite3", storage_root=tmp_path)
    engine = create_database(settings)
    initialize_database(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE species_catalog")
        connection.exec_driver_sql("PRAGMA user_version=2")
    backup = migrate(engine, settings.resolved_database_path)
    assert backup.is_file()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 3
        assert connection.exec_driver_sql("SELECT count(*) FROM observations").scalar_one() == 0
        assert connection.exec_driver_sql("SELECT count(*) FROM species_catalog").scalar_one() == 0
    engine.dispose()
