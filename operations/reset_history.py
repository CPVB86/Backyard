"""Archive registrations before a local date. Preview by default; Pi services must be stopped."""
import argparse
from contextlib import closing
from datetime import date, datetime, time, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from zoneinfo import ZoneInfo

from app.core.config import Settings, ROOT
from app.modules.birds.storage import KEY_PATTERN
from operations.environment import read_environment

TABLES = ("observations", "observation_candidates", "bird_detections", "bird_audio")


def cutoff_for(day):
    return datetime.combine(date.fromisoformat(day), time(), ZoneInfo("Europe/Amsterdam")).astimezone(timezone.utc)


def checked_path(root, key):
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("Unexpected audio key; refused")
    target = root / key
    if any(p.is_symlink() for p in [target, *target.parents] if p != root.parent):
        raise ValueError("Symlinked audio path; refused")
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError("Audio outside storage; refused")
    return target


def plan(database, storage, states, cutoff):
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Database integrity check failed")
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if not set(TABLES).issubset(tables):
            raise ValueError("Expected tables missing")
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            for fk in db.execute(f"PRAGMA foreign_key_list({quoted})"):
                if fk[2] in TABLES and name not in TABLES:
                    raise ValueError("Unexpected incoming database relationship")
        boundary = cutoff.isoformat()
        old = db.execute("SELECT id,storage_key,stale_key,domain,status FROM observations WHERE julianday(start_at)<julianday(?)", (boundary,)).fetchall()
        legacy = db.execute("SELECT id FROM bird_detections WHERE julianday(timestamp)<julianday(?)", (boundary,)).fetchall()
        legacy_audio = db.execute("SELECT storage_key FROM bird_audio WHERE detection_id IN (SELECT id FROM bird_detections WHERE julianday(timestamp)<julianday(?))", (boundary,)).fetchall()
        keys = {r[k] for r in old for k in ("storage_key", "stale_key") if r[k]}
        keys.update(r[0] for r in legacy_audio)
        retained = db.execute("SELECT storage_key,stale_key FROM observations WHERE julianday(start_at)>=julianday(?)", (boundary,)).fetchall()
        retained_keys = {v for row in retained for v in row if v}
        retained_keys.update(r[0] for r in db.execute("SELECT storage_key FROM bird_audio WHERE detection_id NOT IN (SELECT id FROM bird_detections WHERE julianday(timestamp)<julianday(?))", (boundary,)))
        if keys & retained_keys:
            raise ValueError("Audio shared with retained registrations; refused")
        if db.execute("SELECT COUNT(*) FROM observations WHERE julianday(start_at) IS NULL").fetchone()[0]:
            raise ValueError("Invalid observation timestamp")
        files = [(key, checked_path(storage, key)) for key in sorted(keys)]
        summary = {}
        for row in old:
            group = row["domain"] + "/" + row["status"]
            summary[group] = summary.get(group, 0) + 1
    checkpoints = []
    for path in states:
        if path.is_symlink():
            raise ValueError("Symlinked checkpoint")
        state = json.loads(path.read_text())
        if state.get("pending") is not None:
            raise ValueError("Pending bridge import: drain it before reset")
        if state.get("domain") not in ("bird", "bat") or not state.get("instance"):
            raise ValueError("Unexpected checkpoint")
        checkpoints.append((path, state))
    return dict(observations=len(old), legacy_detections=len(legacy), by_status=summary,
                audio_files=sum(path.is_file() for _, path in files),
                audio_bytes=sum(path.stat().st_size for _, path in files if path.is_file())), files, checkpoints


def atomic_json(path, value, owner=None):
    existing = path.stat() if path.exists() else owner
    temporary = path.with_name(path.name + ".reset-tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    if existing:
        os.chmod(temporary, existing.st_mode & 0o777)
        if hasattr(os, "chown"):
            os.chown(temporary, existing.st_uid, existing.st_gid)
    temporary.replace(path)


def apply(database, storage, states, cutoff, backup_root):
    summary, files, checkpoints = plan(database, storage, states, cutoff)
    backup = backup_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup.mkdir(parents=True, mode=0o700)
    with closing(sqlite3.connect(database)) as source:
        with closing(sqlite3.connect(backup / "database.sqlite3")) as target:
            source.backup(target)
    marker = storage / "monitoring-start.json"
    if marker.exists():
        shutil.copy2(marker, backup / "monitoring-start.json")
    for index, (path, _) in enumerate(checkpoints):
        shutil.copy2(path, backup / f"checkpoint-{index}.json")
    manifest = dict(cutoff=cutoff.isoformat(), database=str(database),
                    checkpoints=[str(p) for p, _ in checkpoints], summary=summary,
                    audio_keys=[key for key, _ in files], completed=False)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
    # Update cutoff first, never clear done IDs or rewind the current cursor.
    # With writers stopped, any later failure leaves at worst an earlier import floor.
    for path, state in checkpoints:
        previous = state.get("not_before")
        if not previous or datetime.fromisoformat(previous.replace("Z", "+00:00")) < cutoff:
            state["not_before"] = cutoff.isoformat()
        atomic_json(path, state)
    moved = []
    try:
        with closing(sqlite3.connect(database)) as db:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            try:
                boundary = cutoff.isoformat()
                db.execute("DELETE FROM observation_candidates WHERE observation_id IN (SELECT id FROM observations WHERE julianday(start_at)<julianday(?))", (boundary,))
                db.execute("DELETE FROM observations WHERE julianday(start_at)<julianday(?)", (boundary,))
                db.execute("DELETE FROM bird_audio WHERE detection_id IN (SELECT id FROM bird_detections WHERE julianday(timestamp)<julianday(?))", (boundary,))
                db.execute("DELETE FROM bird_detections WHERE julianday(timestamp)<julianday(?)", (boundary,))
                for key, source in files:
                    if source.is_file():
                        destination = backup / "audio" / key
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(source), str(destination))
                        moved.append((source, destination))
                if db.execute("PRAGMA foreign_key_check").fetchall():
                    raise ValueError("Foreign key check failed")
                db.commit()
            except BaseException:
                db.rollback()
                raise
    except BaseException:
        for source, destination in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(destination), str(source))
        raise
    # A persistent start date is distinct from the first accepted observation.
    atomic_json(marker, {"started_at": cutoff.isoformat(), "timezone": "Europe/Amsterdam"}, database.stat())
    manifest["completed"] = True
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, help="Exclusive YYYY-MM-DD boundary in Europe/Amsterdam")
    parser.add_argument("--environment-file", type=Path, default=Path("/etc/backyard/backyard.env"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    cutoff = cutoff_for(args.before)
    if cutoff > datetime.now(timezone.utc):
        parser.error("Future reset boundary refused")
    env = read_environment(args.environment_file)
    os.environ.update(env)
    config = Settings()
    database, storage = config.resolved_database_path.resolve(), config.resolved_storage_root
    if not database.is_file():
        parser.error("Existing database not found")
    states = []
    for key in ("BACKYARD_GO_BIRD_STATE", "BACKYARD_BAT_STATE"):
        path = Path(env[key])
        states.append(path if path.is_absolute() else ROOT/path)
    summary, _, _ = plan(database, storage, states, cutoff)
    print(json.dumps(dict(cutoff_utc=cutoff.isoformat(), **summary), indent=2))
    if not args.apply:
        print("PREVIEW ONLY. Illustrations/catalog retained; audio will be archived, not destroyed.")
        return
    if os.name != "posix" or os.geteuid() != 0:
        parser.error("--apply requires sudo on the central Pi")
    active = subprocess.check_output(["systemctl", "list-units", "--state=active",
        "--no-legend", "--plain", "backyard-*.service", "backyard-*.timer"], text=True).strip()
    if active:
        parser.error("Stop Backyard services and timers before --apply")
    print("Backup:", apply(database, storage, states, cutoff, database.parent.parent / "reset-backups"))
    print("Completed. File ownership preserved. See docs/RESET_HISTORY.md before restarting services.")


if __name__ == "__main__":
    main()
