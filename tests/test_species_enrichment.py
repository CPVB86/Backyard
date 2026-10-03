import csv
from datetime import datetime, timezone
import json
import sqlite3
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, inspect, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.core.migrations import migrate, upgrade_2_to_3
from app.main import create_app
from app.modules.species.models import Species
from app.modules.species.enrichment import FIELDS, REQUIRED, import_enrichment
from app.modules.species.import_enrichment import main

STAMP = datetime(2026, 1, 1, tzinfo=timezone.utc)
LATER = datetime(2026, 2, 1, tzinfo=timezone.utc)


def data_rows():
    rows = []
    for domain, count in (("bird",1610),("bat",37)):
        for index in range(count):
            name = f"Synthetic {domain} {index:04}"
            if index == 0:
                name = "Columba palumbus" if domain == "bird" else "Myotis mystacinus"
            status = ("exact", "probable", "ambiguous", "not_found")[index % 4]
            rows.append(dict(domain=domain, scientific_name=name,
                wikipedia_nl_url="https://nl.wikipedia.org/wiki/Houtduif" if status != "not_found" else "",
                wikipedia_title_nl="Houtduif" if status != "not_found" else "",
                wikipedia_match_status=status, wikipedia_match_note="Notitie – éénduidig?",
                wikipedia_en_url="https://en.wikipedia.org/wiki/Common_wood_pigeon" if status == "not_found" else "",
                wikipedia_title_en="Common wood pigeon" if status == "not_found" else "",
                summary_nl="Een synthetische samenvatting.", fact_nl="Een feit." if index % 2 == 0 else "",
                encyclopedia_source="wikipedia_nl;sovon", common_name_nl="MUST NOT OVERWRITE",
                source="MUST NOT OVERWRITE", source_species_id="999999", imported_at="MUST NOT OVERWRITE"))
    return rows


def write_csv(path, rows, headers=None):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers or list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def catalog(tmp_path):
    config = Settings(_env_file=None, database_path=tmp_path/"catalog.sqlite3", storage_root=tmp_path)
    engine = create_database(config)
    initialize_database(engine)
    rows = data_rows()
    with engine.begin() as connection:
        connection.execute(Species.__table__.insert(), [dict(
            domain=row["domain"], scientific_name=row["scientific_name"], source="waarneming.nl",
            source_species_id=index+1, common_name_nl="Bestaande naam", authority="Authority",
            family="Existing family", taxon_type="soort", taxon_group="Vogels", parent_source_species_id=7,
            source_url=f"https://waarneming.nl/species/{index+1}/", rarity="algemeen", status="inheems",
            obscurity="none", source_metadata={"original":"één", "index":index}, imported_at=STAMP,
            updated_at=STAMP) for index,row in enumerate(rows)])
    path = write_csv(tmp_path/"enrichment.csv", rows)
    yield engine, config, path, rows
    engine.dispose()


def snapshot(engine):
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(select(Species.__table__).order_by(Species.id)).mappings()]


def test_valid_dry_run_byte_unchanged_and_report(catalog):
    engine, config, path, rows = catalog
    before = config.resolved_database_path.read_bytes()
    result = import_enrichment(engine, path)
    assert result["safe_to_apply"] is True and result["applied"] is False
    assert result["csv_records"] == result["database_matches"] == result["unique_identities"] == 1647
    assert (result["birds"], result["bats"]) == (1610,37)
    assert result["would_update"] == 1647 and result["updated"] == result["unchanged"] == 0
    assert sum(result["wikipedia_nl"].values()) == 1647
    assert result["summary_populated"] == result["encyclopedia_source_populated"] == 1647
    assert result["fact_populated"] > 0 and result["wikipedia_en_fallback_populated"] > 0
    assert config.resolved_database_path.read_bytes() == before


def test_apply_allowlist_source_preserved_and_idempotent(catalog):
    engine, config, path, rows = catalog
    before = snapshot(engine)
    first = import_enrichment(engine, path, apply=True, now=LATER)
    assert first["applied"] and first["updated"] == 1647
    after = snapshot(engine)
    protected = set(before[0]) - set(FIELDS) - {"updated_at"}
    assert len(after) == len(before) == 1647
    for old,new in zip(before,after):
        assert {key:old[key] for key in protected} == {key:new[key] for key in protected}
        assert new["updated_at"] == LATER
    assert after[0]["wikipedia_match_note"] == rows[0]["wikipedia_match_note"]
    assert after[1]["fact_nl"] is None
    second = import_enrichment(engine, path, apply=True)
    assert second["updated"] == second["would_update"] == 0 and second["unchanged"] == 1647
    assert snapshot(engine) == after


@pytest.mark.parametrize("case,reason", [
    ("total","wrong_total"), ("birds","wrong_domain_count"), ("bats","wrong_domain_count"),
    ("duplicate","duplicate_csv_identity"), ("blank_name","invalid_scientific_name"),
    ("blank_domain","invalid_domain"), ("unknown_domain","invalid_domain"),
    ("unknown_status","invalid_match_status"), ("url","invalid_wikipedia_url"),
])
def test_csv_preflight_failures_never_write(catalog, case, reason):
    engine, config, path, rows = catalog
    if case == "total": rows.pop()
    elif case == "birds": rows[0]["domain"] = "bat"
    elif case == "bats": rows[-1]["domain"] = "bird"
    elif case == "duplicate": rows[1]["scientific_name"] = rows[0]["scientific_name"]
    elif case == "blank_name": rows[-1]["scientific_name"] = ""
    elif case == "blank_domain": rows[0]["domain"] = ""
    elif case == "unknown_domain": rows[0]["domain"] = "fish"
    elif case == "unknown_status": rows[-1]["wikipedia_match_status"] = "certain"
    elif case == "url": rows[0]["wikipedia_nl_url"] = "javascript:alert(1)"
    write_csv(path, rows)
    before = config.resolved_database_path.read_bytes()
    result = import_enrichment(engine, path, apply=True)
    assert not result["safe_to_apply"] and result["updated"] == 0
    assert reason in {item["reason"] for item in result["errors"]}
    assert config.resolved_database_path.read_bytes() == before


@pytest.mark.parametrize("case", ["csv_missing", "database_missing", "database_duplicate", "database_extra"])
def test_database_exact_identity_match_required(catalog, case):
    engine, config, path, rows = catalog
    name = rows[0]["scientific_name"]
    if case == "csv_missing":
        rows[0]["scientific_name"] = "Absent identity"
        write_csv(path, rows)
    else:
        with engine.begin() as connection:
            if case == "database_missing":
                connection.execute(Species.__table__.delete().where(Species.id == 1))
            elif case == "database_extra":
                copy = dict(connection.execute(select(Species.__table__).where(Species.id == 1)).mappings().one())
                copy.update(id=2000, scientific_name="Unexpected identity", source_species_id=2000)
                connection.execute(Species.__table__.insert().values(**copy))
            else:
                connection.exec_driver_sql("DROP INDEX uq_species_identity")
                connection.execute(update(Species).where(Species.id == 2).values(scientific_name=name))
    before = config.resolved_database_path.read_bytes()
    result = import_enrichment(engine, path, apply=True)
    assert result["safe_to_apply"] is False
    assert any(error.get("scientific_name") in {name,"Absent identity","Unexpected identity"} for error in result["errors"])
    assert config.resolved_database_path.read_bytes() == before


def test_mid_apply_failure_rolls_back_every_record(catalog):
    engine, _, path, _ = catalog
    before = snapshot(engine)
    calls = []
    def fail(connection, cursor, statement, parameters, context, many):
        if statement.startswith("UPDATE species_catalog"):
            calls.append(statement)
            if len(calls) == 824:
                raise RuntimeError("Simulated storage failure halfway")
    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="halfway"):
            import_enrichment(engine, path, apply=True)
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert len(calls) == 824
    assert snapshot(engine) == before


def test_only_changed_row_gets_updated_timestamp(catalog):
    engine, _, path, rows = catalog
    import_enrichment(engine, path, apply=True, now=STAMP)
    rows[0]["summary_nl"] = "Gewijzigde samenvatting."
    write_csv(path, rows)
    result = import_enrichment(engine, path, apply=True, now=LATER)
    assert result["updated"] == 1 and result["unchanged"] == 1646
    after = snapshot(engine)
    assert after[0]["updated_at"] == LATER
    assert all(row["updated_at"] == STAMP for row in after[1:])


def test_api_nulls_match_statuses_en_fallback_and_no_network(catalog, monkeypatch):
    engine, config, path, rows = catalog
    import_enrichment(engine, path, apply=True)
    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", Mock(side_effect=AssertionError("No external request")))
    with TestClient(create_app(config), headers={"Authorization":"Bearer backyard-test-token"}) as client:
        monkeypatch.setattr(client.app.state.generator, "ensure", Mock(side_effect=AssertionError("No generation")))
        for row in [*rows[:4],rows[-37]]:
            response = client.get(f"/api/species/{row['domain']}/{row['scientific_name']}", params={"locale":"nl"})
            assert response.status_code == 200, response.text
            encyclopedia = response.json()["encyclopedia"]
            for key in FIELDS:
                assert encyclopedia["source" if key == "encyclopedia_source" else key] == (row[key] or None)
            assert response.json()["identity"]["common_name_nl"] == "Bestaande naam"


def test_cli_dry_run_and_failure_exit(catalog, monkeypatch, capsys):
    engine, config, path, rows = catalog
    monkeypatch.setenv("BACKYARD_DATABASE_PATH", str(config.resolved_database_path))
    before = config.resolved_database_path.read_bytes()
    main(["--file", str(path)])
    assert "SAFE TO APPLY: yes" in capsys.readouterr().out
    assert config.resolved_database_path.read_bytes() == before
    rows.pop(); write_csv(path, rows)
    with pytest.raises(SystemExit) as error:
        main(["--file", str(path), "--apply"])
    assert error.value.code != 0
    assert "SAFE TO APPLY: no" in capsys.readouterr().out
    assert config.resolved_database_path.read_bytes() == before


def test_cli_never_creates_missing_database(catalog, tmp_path, monkeypatch, capsys):
    _, _, path, _ = catalog
    absent = tmp_path/"absent.sqlite3"
    monkeypatch.setenv("BACKYARD_DATABASE_PATH", str(absent))
    for args in ([],["--apply"]):
        with pytest.raises(SystemExit): main(["--file",str(path),*args])
        assert "SAFE TO APPLY: no" in capsys.readouterr().out
        assert not absent.exists()


def test_v3_migration_preserves_catalog_and_old_enrichment(tmp_path):
    settings = Settings(_env_file=None, database_path=tmp_path/"v3.sqlite3")
    engine = create_database(settings)
    with engine.begin() as connection:
        upgrade_2_to_3(connection)
        connection.exec_driver_sql("PRAGMA user_version=3")
        connection.exec_driver_sql("INSERT INTO species_catalog "
            "(domain,scientific_name,source,source_species_id,source_metadata,imported_at,updated_at,summary_nl) "
            "VALUES ('bird','Columba palumbus','waarneming.nl',1,'{}','2026-01-01','2026-01-01','Existing summary')")
    with engine.connect() as connection:
        before = dict(connection.exec_driver_sql("SELECT * FROM species_catalog").mappings().one())
    backup = migrate(engine, settings.resolved_database_path)
    assert backup.is_file()
    with engine.connect() as connection:
        after = dict(connection.exec_driver_sql("SELECT * FROM species_catalog").mappings().one())
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 4
    assert {key:after[key] for key in before} == before
    assert set(after)-set(before) == {"wikipedia_title_nl","wikipedia_match_status","wikipedia_match_note","wikipedia_en_url","wikipedia_title_en"}
    assert all(after[key] is None for key in set(after)-set(before))
    assert migrate(engine, settings.resolved_database_path) is None
    engine.dispose()


def test_v3_migration_rollback(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, database_path=tmp_path/"v3.sqlite3")
    engine = create_database(settings)
    with engine.begin() as connection:
        upgrade_2_to_3(connection)
        connection.exec_driver_sql("PRAGMA user_version=3")
    def broken(connection):
        connection.exec_driver_sql("ALTER TABLE species_catalog ADD COLUMN wikipedia_title_nl VARCHAR(255)")
        raise RuntimeError("interrupted")
    monkeypatch.setattr("app.core.migrations.upgrade_3_to_4", broken)
    with pytest.raises(RuntimeError): migrate(engine, settings.resolved_database_path)
    assert "wikipedia_title_nl" not in {c["name"] for c in inspect(engine).get_columns("species_catalog")}
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 3
    engine.dispose()


def test_observations_assets_and_audio_untouched(catalog, tmp_path):
    from test_species_catalog import observation
    engine, config, path, _ = catalog
    with Session(engine) as session:
        session.add(observation("Columba palumbus", "auto_accepted", STAMP))
        session.commit()
    def others():
        with engine.connect() as connection:
            return {name: connection.exec_driver_sql('SELECT * FROM "' + name + '"').fetchall()
                    for name in inspect(connection).get_table_names() if name != "species_catalog"}
    before = others()
    audio = tmp_path/"evidence.wav"; audio.write_bytes(b"existing audio sentinel")
    asset = tmp_path/"illustration.png"; asset.write_bytes(b"existing image sentinel")
    import_enrichment(engine, path, apply=True)
    assert others() == before
    assert audio.read_bytes() == b"existing audio sentinel"
    assert asset.read_bytes() == b"existing image sentinel"


@pytest.mark.parametrize("mode", ["missing_column", "duplicate_column", "extra_value"])
def test_malformed_csv_refused(catalog, mode):
    engine, config, path, rows = catalog
    if mode == "missing_column":
        for row in rows: row.pop("fact_nl")
        write_csv(path, rows)
    elif mode == "duplicate_column":
        write_csv(path, rows, [*rows[0],"fact_nl"])
    else:
        with path.open("a", encoding="utf-8") as f:
            f.write(",".join(["extra"]*40)+"\n")
    before = config.resolved_database_path.read_bytes()
    assert not import_enrichment(engine, path, apply=True)["safe_to_apply"]
    assert config.resolved_database_path.read_bytes() == before


def test_migration_cli_uses_production_environment(tmp_path, monkeypatch, capsys):
    from app.core.migrate import main as migrate_main
    config = Settings(_env_file=None, database_path=tmp_path/"production.sqlite3")
    engine = create_database(config)
    with engine.begin() as connection:
        upgrade_2_to_3(connection)
        connection.exec_driver_sql("PRAGMA user_version=3")
    environment = tmp_path/"production.env"
    environment.write_text("BACKYARD_DATABASE_PATH=" + config.resolved_database_path.as_posix(), encoding="utf-8")
    monkeypatch.delenv("BACKYARD_DATABASE_PATH", raising=False)
    migrate_main(["--environment-file", str(environment)])
    assert "version 4" in capsys.readouterr().out
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA user_version").scalar_one() == 4
    engine.dispose()
