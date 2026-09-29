import hashlib
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.birds.models import BirdAudio, BirdDetection
from app.modules.birds.schemas import serialize
from app.modules.birds import storage

logger = logging.getLogger("backyard.birds")


def require_detection(session, detection_id):
    record = session.get(BirdDetection, str(detection_id))
    if record is None:
        raise HTTPException(404, "Detection not found")
    return record


def ingest(engine, payload):
    canonical = payload.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(
        canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")).hexdigest()
    with Session(engine) as session:
        # Serialize SQLite writers so retries cannot race the uniqueness check.
        session.execute(text("BEGIN IMMEDIATE"))
        existing = session.scalar(select(BirdDetection).where(
            BirdDetection.source == payload.source, BirdDetection.event_id == payload.event_id
        ))
        if existing is not None:
            if existing.ingest_hash != digest:
                raise HTTPException(409, "Event ID already used with different detection data")
            return serialize(existing), False
        values = payload.model_dump()
        values["timestamp"] = values.pop("detected_at")
        record = BirdDetection(**values, ingest_hash=digest)
        session.add(record)
        session.flush()
        result = serialize(record)
        session.commit()
        return result, True


def attach_audio(engine, settings, detection_id, data):
    info = storage.inspect_wav(data)
    owned_path = None
    try:
        with Session(engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = require_detection(session, detection_id)
            if record.audio is not None:
                if record.audio.sha256 != info["sha256"]:
                    raise HTTPException(409, "Detection already has different audio")
                if record.audio.status != "available":
                    raise HTTPException(409, "Audio no longer available; automatic restoration disabled")
                path = storage.audio_path(settings.resolved_storage_root, record.audio.storage_key)
                if not path.is_file():
                    raise HTTPException(409, "Registered audio missing; operator review required")
                if (path.stat().st_size != info["size_bytes"]
                        or hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]):
                    raise HTTPException(409, "Registered audio damaged; operator review required")
                return serialize(record), False

            date = record.timestamp
            key = f"birds/audio/{date.year:04d}/{date.month:02d}/{date.day:02d}/{record.id}.wav"
            path, created = storage.publish(
                settings.resolved_storage_root, key, data, info["sha256"]
            )
            if created:
                owned_path = path
            audio = BirdAudio(detection_id=record.id, storage_key=key, **info)
            session.add(audio)
            record.audio = audio
            session.flush()
            result = serialize(record)
            session.commit()
            owned_path = None
            return result, True
    finally:
        if owned_path is not None:
            try:
                owned_path.unlink(missing_ok=True)
            except OSError:
                logger.error("Audio rollback cleanup failed; operator review required")
