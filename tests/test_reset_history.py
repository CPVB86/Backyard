import json
import sqlite3
from pathlib import Path
import pytest
from operations.reset_history import cutoff_for, plan, apply
from app.core.monitoring import monitoring_started_at
from app.core.config import Settings


@pytest.fixture
def setup(tmp_path):
    storage = tmp_path / "data"
    storage.mkdir()
    database = storage / "backyard.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript("""
        CREATE TABLE observations(id TEXT PRIMARY KEY,domain TEXT,status TEXT,start_at TEXT,storage_key TEXT,stale_key TEXT);
        CREATE TABLE observation_candidates(id TEXT,observation_id TEXT REFERENCES observations(id));
        CREATE TABLE bird_detections(id TEXT PRIMARY KEY,timestamp TEXT);
        CREATE TABLE bird_audio(detection_id TEXT REFERENCES bird_detections(id),storage_key TEXT);
        CREATE TABLE species_catalog(name TEXT);
        INSERT INTO species_catalog VALUES ('preserve');
        """)
        for index, stamp in enumerate(("2026-11-08 22:59:59", "2026-11-08 23:00:00")):
            key = f"review/bat/audio/2026/11/08/00000000-0000-0000-0000-00000000000{index}.wav"
            path = storage/key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"evidence")
            db.execute("INSERT INTO observations VALUES (?,?,?,?,?,NULL)",
                       (str(index), "bat", "human_confirmed", stamp, key))
            db.execute("INSERT INTO observation_candidates VALUES (?,?)", (str(index),str(index)))
        db.execute("INSERT INTO bird_detections VALUES ('old','2026-11-08 22:00:00')")
    asset = storage/"generator/bat/illustration.png"
    asset.parent.mkdir(parents=True)
    asset.write_bytes(b"preserve image")
    states = []
    for domain in ("bird","bat"):
        path = storage/f"{domain}.json"
        path.write_text(json.dumps(dict(domain=domain, instance=domain, date="2026-11-09",
                                       done=["42"], pending=None)))
        states.append(path)
    return database, storage, states


def test_preview_cutoff_apply_and_repeat(setup):
    database, storage, states = setup
    cutoff = cutoff_for("2026-11-09")
    assert cutoff.isoformat() == "2026-11-08T23:00:00+00:00"
    before = database.read_bytes()
    summary, _, _ = plan(database, storage, states, cutoff)
    assert summary["observations"] == summary["legacy_detections"] == 1
    assert database.read_bytes() == before
    backup = apply(database, storage, states, cutoff, storage.parent/"backups")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT id FROM observations").fetchall() == [("1",)]
        assert db.execute("SELECT id FROM observation_candidates").fetchall() == [("1",)]
        assert db.execute("SELECT COUNT(*) FROM bird_detections").fetchone()[0] == 0
        assert db.execute("SELECT name FROM species_catalog").fetchone()[0] == "preserve"
    assert len(list((backup/"audio").rglob("*.wav"))) == 1
    assert len(list((storage/"review").rglob("*.wav"))) == 1
    assert (storage/"generator/bat/illustration.png").read_bytes() == b"preserve image"
    for path in states:
        state = json.loads(path.read_text())
        assert state["done"] == ["42"]
        assert state["not_before"] == cutoff.isoformat()
    assert monitoring_started_at(Settings(_env_file=None, storage_root=storage)) == cutoff.isoformat()
    summary, _, _ = plan(database, storage, states, cutoff)
    assert summary["observations"] == summary["audio_files"] == 0
    apply(database, storage, states, cutoff, storage.parent/"backups")


def test_pending_import_refused(setup):
    database, storage, states = setup
    value = json.loads(states[0].read_text())
    value["pending"] = {"record": {"id":42}}
    states[0].write_text(json.dumps(value))
    with pytest.raises(ValueError, match="Pending"):
        plan(database, storage, states, cutoff_for("2026-11-09"))


def test_archive_failure_rolls_back_database_and_audio(setup, monkeypatch):
    from operations import reset_history
    database, storage, states = setup
    def fail(*args):
        raise OSError("test")
    monkeypatch.setattr(reset_history.shutil, "move", fail)
    with pytest.raises(OSError):
        apply(database, storage, states, cutoff_for("2026-11-09"), storage.parent/"backups")
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2
    assert len(list((storage/"review").rglob("*.wav"))) == 2


def test_unexpected_foreign_key_refused(setup):
    database, storage, states = setup
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE future(obs TEXT REFERENCES observations(id))")
    with pytest.raises(ValueError, match="Unexpected incoming"):
        plan(database, storage, states, cutoff_for("2026-11-09"))
