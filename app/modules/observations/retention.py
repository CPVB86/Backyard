"""Opt-in audio lifecycle. Metadata is retained; deletion is durable and retryable."""
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil

from sqlalchemy import select, text
from sqlalchemy.orm import Session, noload

from app.modules.birds import storage
from app.modules.birds.models import BirdDetection
from app.modules.observations.effective import scientific_name
from app.modules.observations.models import Observation

LOCATIONS = ("birds/audio", "bats/audio", "review/bird/audio", "review/bat/audio")

def checked_path(root, key):
    path = storage.audio_path(root, key)
    cursor = root.resolve()
    for part in key.split("/"):
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError("Symlinked evidence refused")
    return path


def utcnow():
    return datetime.now(timezone.utc)


def disk_bytes(root):
    """Count physical audio, including unregistered leftovers; never traverse links."""
    total = 0
    root = root.resolve()
    for relative in LOCATIONS:
        base = root / relative
        if base.is_symlink() or not base.resolve().is_relative_to(root):
            raise ValueError("Symlinked audio tree refused")
        if not base.exists():
            continue
        for directory, dirs, files in os.walk(base, followlinks=False):
            for name in dirs + files:
                path = Path(directory) / name
                if path.is_symlink() or not path.resolve().is_relative_to(root):
                    raise ValueError("Symlinked audio entry refused")
            for name in files:
                total += (Path(directory) / name).stat().st_size
    return total


def can_publish(settings, size):
    root = settings.resolved_storage_root
    root.mkdir(parents=True, exist_ok=True)
    return (disk_bytes(root) + size <= settings.audio_retention_max_bytes
            and shutil.disk_usage(root).free - size >= settings.audio_min_free_bytes)


def _trash_deadline(record, settings):
    when = (record.review or {}).get("at")
    anchor = datetime.fromisoformat(when) if when else record.created_at
    return anchor + timedelta(hours=settings.audio_trash_hours)


def _entries(session):
    observations = session.scalars(select(Observation).options(noload(Observation.supports)).where(
        Observation.audio["status"].as_string().in_(("available", "trash", "deleting"))))
    for record in observations:
        yield {"kind": "observation", "row": record, "id": record.id,
               "key": record.storage_key, "time": record.start_at,
               "bytes": record.audio.get("size_bytes", 0), "status": record.audio.get("status"),
               "species": (record.domain, scientific_name(record)),
               "bucket": "accepted" if record.status in ("auto_accepted", "human_confirmed") else "review",
               "priority": 2 if record.status == "human_confirmed" else 1,
               "rejected": record.status == "human_rejected"}
    for record in session.scalars(select(BirdDetection)):
        audio = record.audio
        if audio is None or (audio.status != "available" and not audio.status.startswith("delete_")):
            continue
        yield {"kind": "legacy", "row": record, "id": record.id,
               "key": audio.storage_key, "time": record.timestamp,
               "bytes": audio.size_bytes, "status": audio.status,
               "species": ("bird", record.scientific_name),
               "bucket": "accepted" if record.verification_status == "confirmed" else "review",
               "priority": 2 if record.verification_status == "confirmed" else 1,
               "rejected": record.verification_status == "rejected"}


def reconcile(engine, settings, *, apply=False, now=None, limit=100):
    if not settings.audio_retention_enabled:
        return {"enabled": False, "actions": []}
    now = now or utcnow()
    actions, deletes, trashes, winners, trash = [], [], [], {}, []
    with Session(engine) as session:
        if apply:
            session.execute(text("BEGIN IMMEDIATE"))
        entries = list(_entries(session))
        for entry in entries:
            record = entry["row"]
            if entry["status"] == "deleting" or entry["status"].startswith("delete_"):
                reason = (record.audio.get("retention_reason", "expired")
                          if entry["kind"] == "observation" else entry["status"][7:])
                deletes.append((entry, reason))
                continue
            path = checked_path(settings.resolved_storage_root, entry["key"])
            if not path.is_file() or path.stat().st_size != entry["bytes"]:
                deletes.append((entry, "missing" if not path.is_file() else "corrupt"))
                continue
            if entry["rejected"]:
                # Legacy rejected evidence has no versioned trash/undo contract.
                if entry["kind"] == "legacy":
                    deletes.append((entry, "rejected"))
                else:
                    deadline = _trash_deadline(record, settings)
                    if deadline <= now:
                        deletes.append((entry, "rejected"))
                    else:
                        trash.append((entry, deadline))
                continue
            if (entry["bucket"] == "review"
                    and entry["time"] + timedelta(days=settings.audio_review_days) <= now):
                deletes.append((entry, "expired"))
                continue
            group = (*entry["species"], entry["bucket"])
            rank = (entry["priority"], entry["time"], entry["id"], entry["kind"])
            if group not in winners:
                winners[group] = (rank, entry)
            elif rank > winners[group][0]:
                deletes.append((winners[group][1], "superseded"))
                winners[group] = (rank, entry)
            else:
                deletes.append((entry, "superseded"))
        trash_size = sum(e["bytes"] for e, _ in trash)
        for entry, deadline in sorted(trash, key=lambda pair: (pair[1], pair[0]["id"])):
            if trash_size > settings.audio_trash_max_bytes:
                deletes.append((entry, "rejected"))
                trash_size -= entry["bytes"]
            elif entry["status"] != "trash":
                trashes.append((entry, deadline))
        for entry, reason in deletes[:limit]:
            actions.append({"kind": entry["kind"], "id": entry["id"], "action": "delete", "reason": reason})
            if apply:
                record = entry["row"]
                if entry["kind"] == "observation":
                    record.audio = dict(record.audio, status="deleting", retention_reason=reason,
                                        deleted_at=record.audio.get("deleted_at") or now.isoformat())
                    record.evidence_kind = "deleted"
                    record.cleanup_after = None
                else:
                    record.audio.status = "delete_" + reason
        for entry, deadline in trashes[:max(0, limit - len(actions))]:
            actions.append({"kind": "observation", "id": entry["id"], "action": "trash", "reason": "rejected"})
            if apply:
                record = entry["row"]
                record.audio = dict(record.audio, status="trash", retention_reason="rejected")
                record.evidence_kind = "delete_pending"
                record.cleanup_after = deadline
        if apply:
            session.commit()
        else:
            session.rollback()
    # Commit the tombstone FIRST. A crash or failed unlink remains retryable and
    # can never advertise a deleted file as available or allow automatic reupload.
    failures = 0
    if apply:
        for action in actions:
            if action["action"] != "delete":
                continue
            with Session(engine) as session:
                session.execute(text("BEGIN IMMEDIATE"))
                model = Observation if action["kind"] == "observation" else BirdDetection
                record = session.get(model, action["id"])
                if action["kind"] == "observation":
                    if not record.audio or record.audio.get("status") != "deleting":
                        continue
                    key = record.storage_key
                else:
                    if not record.audio.status.startswith("delete_"):
                        continue
                    key = record.audio.storage_key
                try:
                    if key:
                        checked_path(settings.resolved_storage_root, key).unlink(missing_ok=True)
                except OSError:
                    failures += 1
                    continue
                if action["kind"] == "observation":
                    record.audio = dict(record.audio, status="deleted")
                    record.storage_key = None
                else:
                    record.audio.status = action["reason"]
                session.commit()
    return {"enabled": True, "apply": apply, "actions": actions, "unlink_failures": failures}


def refreshed(engine, settings, identity):
    from app.modules.observations.schemas import serialize
    reconcile(engine, settings, apply=True)
    with Session(engine) as session:
        return serialize(session.get(Observation, str(identity)))
