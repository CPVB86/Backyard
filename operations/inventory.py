"""Read-only database and filesystem inventory. No migrations or cleanup."""
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import stat
import time


def database_inventory(path, since, check=False):
    path = Path(path).resolve()
    cutoff = datetime.fromtimestamp(since, timezone.utc)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
        db.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 10
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version != 2:
            raise ValueError(f"Database schema is {version}, expected 2; no migration performed")
        def counts(column, where="", args=()):
            return {str(key): count for key, count in db.execute(
                f"SELECT {column}, COUNT(*) FROM observations {where} GROUP BY {column}", args)}
        recent = "WHERE created_at >= ?"
        args = (cutoff.replace(tzinfo=None).isoformat(" "),)
        result = {
            "path": str(path), "schema": version,
            "legacy_detections": db.execute("SELECT COUNT(*) FROM bird_detections").fetchone()[0],
            "legacy_audio": db.execute("SELECT COUNT(*) FROM bird_audio").fetchone()[0],
            "observations_total": db.execute("SELECT COUNT(*) FROM observations").fetchone()[0],
            "supports_total": db.execute("SELECT COUNT(*) FROM observation_candidates").fetchone()[0],
            "by_status": counts("status"), "by_domain": counts("domain"),
            "by_evidence_kind": counts("evidence_kind"),
            "created_since_by_status": counts("status", recent, args),
            "created_since_by_domain": counts("domain", recent, args),
            "audio_metadata_available": db.execute(
                "SELECT COUNT(*) FROM observations WHERE audio IS NOT NULL AND evidence_kind != 'deleted'").fetchone()[0],
            "audio_uploaded_since": db.execute(
                "SELECT COUNT(*) FROM observations WHERE json_extract(audio, '$.created_at') >= ?",
                (cutoff.isoformat(),)).fetchone()[0],
            "review_overdue": db.execute(
                "SELECT COUNT(*) FROM observations WHERE status IN ('pending_review','review_recommended') AND review_due_at < ?",
                (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(" "),)).fetchone()[0],
        }
        if check:
            result["quick_check"] = [row[0] for row in db.execute("PRAGMA quick_check")]
        return result


def audio_inventory(root, since, max_entries=100000, timeout=10):
    result = {"groups": {}, "complete": True, "entries_scanned": 0, "errors": []}
    deadline = time.monotonic() + timeout
    locations = {"permanent_bird": "birds/audio", "permanent_bat": "bats/audio",
                 "review_bird": "review/bird/audio", "review_bat": "review/bat/audio"}
    def error(error):
        result["errors"].append(str(error)[:200])
        result["complete"] = False
    for name, relative in locations.items():
        group = result["groups"][name] = {"wav_files": 0, "bytes": 0, "new_or_modified_since": 0}
        base = Path(root) / relative
        if base.is_symlink():
            error(ValueError(f"Symlinked audio directory not traversed: {base}"))
            continue
        if not base.exists():
            continue  # A domain with no evidence yet has no directory.
        for directory, dirs, files in os.walk(base, followlinks=False, onerror=error):
            result["entries_scanned"] += len(dirs)
            if result["entries_scanned"] > max_entries or time.monotonic() > deadline:
                result["complete"] = False
                result["errors"].append("Inventory scan limit reached; totals are partial")
                return result
            for filename in files:
                result["entries_scanned"] += 1
                if result["entries_scanned"] > max_entries or time.monotonic() > deadline:
                    result["complete"] = False
                    result["errors"].append("Inventory scan limit reached; totals are partial")
                    return result
                if not filename.lower().endswith(".wav"):
                    continue
                try:
                    info = (Path(directory) / filename).lstat()
                    if not stat.S_ISREG(info.st_mode):
                        continue
                    group["wav_files"] += 1
                    group["bytes"] += info.st_size
                    group["new_or_modified_since"] += int(info.st_mtime >= since)
                except OSError as exc:
                    error(exc)
    return result
