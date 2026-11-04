"""Preview or activate tested audio retention on the central Pi; never resets data."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile

from sqlalchemy import create_engine
from app.core.config import ROOT, Settings
from app.core.database import create_database
from app.core.migrations import SCHEMA_VERSION
from app.modules.observations.retention import reconcile, disk_bytes
from operations.environment import read_environment

UNITS = ("backyard-api.service", "backyard-birds.service", "backyard-bats.service")


def env_text(original, values):
    for name, value in values.items():
        pattern = rf"(?m)^{re.escape(name)}=.*$"
        if len(re.findall(pattern, original)) > 1:
            raise ValueError(f"Duplicate environment setting: {name}")
        line = f"{name}={value}"
        original = re.sub(pattern, lambda _: line, original) if re.search(pattern, original) else original.rstrip() + "\n" + line + "\n"
    return original


def atomic_env(path, content):
    metadata = path.stat()
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, metadata.st_mode & 0o777)
        os.chown(temporary, metadata.st_uid, metadata.st_gid)
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def systemctl(*args, check=True):
    return subprocess.run(["systemctl", *args], text=True, check=check, capture_output=True)


def preview(settings):
    database = settings.resolved_database_path.resolve(strict=True)
    uri = database.as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("Database schema differs; run the documented migration first")
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Database integrity check failed")
    engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
    try:
        report = reconcile(engine, settings, apply=False, limit=100)
    finally:
        engine.dispose()
    return {"database": str(database), "physical_audio_bytes": disk_bytes(settings.resolved_storage_root),
            "max_audio_bytes": settings.audio_retention_max_bytes,
            "review_days": settings.audio_review_days, "trash_hours": settings.audio_trash_hours,
            "preview_first_batch": report}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path("/etc/backyard/backyard.env"))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    environment = read_environment(args.env_file)
    os.environ.update(environment)
    settings = Settings().model_copy(update={"audio_retention_enabled": True})
    values = {
        "BACKYARD_AUDIO_RETENTION_ENABLED": "true",
        "BACKYARD_AUDIO_REVIEW_DAYS": settings.audio_review_days,
        "BACKYARD_AUDIO_TRASH_HOURS": settings.audio_trash_hours,
        "BACKYARD_AUDIO_RETENTION_MAX_BYTES": settings.audio_retention_max_bytes,
        "BACKYARD_AUDIO_TRASH_MAX_BYTES": settings.audio_trash_max_bytes,
        "BACKYARD_AUDIO_MIN_FREE_BYTES": settings.audio_min_free_bytes,
    }
    updated = env_text(args.env_file.read_text(), values)
    print(json.dumps(preview(settings), indent=2), flush=True)
    if not args.apply:
        print("Preview only: no database, audio, service or environment changes.")
        return
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Activation requires sudo on the central Linux Pi")
    prior_active = [u for u in UNITS if systemctl("is-active", "--quiet", u, check=False).returncode == 0]
    # Close writers before the consistent backup and first cleanup. Go capture
    # and BYRemote keep running; there is no source clip deletion here.
    for unit in ("backyard-audio-cleanup.timer", "backyard-audio-cleanup.service"):
        systemctl("stop", unit, check=False)
    systemctl("stop", *UNITS)
    try:
        backup = ROOT / "retention-backups" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup.mkdir(parents=True, mode=0o700)
        with closing(sqlite3.connect(settings.resolved_database_path)) as source:
            with closing(sqlite3.connect(backup / "backyard.sqlite3")) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("Backup integrity check failed")
        shutil.copy2(args.env_file, backup / "backyard.env")
        atomic_env(args.env_file, updated)
        for name in ("backyard-audio-cleanup.service", "backyard-audio-cleanup.timer"):
            target = Path("/etc/systemd/system") / name
            if target.exists():
                shutil.copy2(target, backup / name)
            target.write_text((ROOT / "deploy/systemd" / name).read_text())
            os.chmod(target, 0o644)
        systemctl("daemon-reload")
        engine = create_database(settings)
        try:
            for _ in range(20):
                report = reconcile(engine, settings, apply=True, limit=100)
                print(json.dumps(report), flush=True)
                if not report["actions"] or report["unlink_failures"]:
                    break
        finally:
            engine.dispose()
        systemctl("enable", "--now", "backyard-audio-cleanup.timer")
        print("Audio retention activated. Database/environment backup:", backup, flush=True)
        print("No reset. Metadata retained. Deleted audio is NOT in the database backup.", flush=True)
    finally:
        if prior_active:
            systemctl("start", *prior_active)
    for unit in prior_active:
        print(unit, systemctl("is-active", unit).stdout.strip())


if __name__ == "__main__":
    main()
