from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import hashlib
import io
import wave
import json
import logging

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.birds import storage
from app.modules.observations.models import Observation, SupportingCandidate
from app.modules.observations.schemas import serialize
from observations.policy import RawCandidate, decision, validate_event

logger = logging.getLogger("backyard.observations")


def require(session, identity):
    record = session.get(Observation, str(identity))
    if record is None:
        raise HTTPException(404, "Observation not found")
    return record


def ingest(engine, policy, payload):
    canonical = payload.model_dump(mode="json")
    digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, allow_nan=False).encode()).hexdigest()
    supports = [RawCandidate(**candidate.model_dump()) for candidate in payload.candidates]
    first = supports[0]
    with Session(engine) as session:
        session.execute(text("BEGIN IMMEDIATE"))
        existing = session.scalar(select(Observation).where(
            Observation.source == first.source, Observation.event_id == payload.event_id))
        if existing:
            if existing.ingest_hash != digest:
                raise HTTPException(409, "Observation event ID already has different data")
            return serialize(existing), False
        if payload.policy_fingerprint != policy.fingerprint:
            raise HTTPException(409, "Policy mismatch: API and monitor need the same policy.json")
        try:
            start, end = validate_event(supports, policy)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        before, after = payload.clip_start_sample, payload.clip_end_sample
        if not (max(0, start - 10 * first.sample_rate) <= before <= start < end <= after
                <= end + 10 * first.sample_rate):
            raise HTTPException(422, "Clip must cover event with at most 10s context per side")
        outcome = decision(supports, policy)
        if outcome["status"] == "discarded":
            # No DB row, supporting metadata or audio for irrelevant candidates.
            return {"id": None, "status": "discarded", "decision": outcome, "audio_url": None}, False
        for candidate in supports:
            used = session.scalar(select(SupportingCandidate.id).where(
                SupportingCandidate.source == candidate.source,
                SupportingCandidate.candidate_id == candidate.candidate_id))
            if used:
                raise HTTPException(409, "Candidate already supports another observation")
        now = datetime.now(timezone.utc)
        best = max(supports, key=lambda c: c.confidence)
        record = Observation(
            source=first.source, event_id=payload.event_id, ingest_hash=digest,
            domain=first.domain, scientific_name=first.scientific_name, common_name=best.common_name,
            start_at=datetime.fromisoformat(first.timestamp(start)),
            end_at=datetime.fromisoformat(first.timestamp(end)),
            best_confidence=best.confidence, status=outcome["status"],
            decision=outcome, policy=asdict(policy), evidence_kind=outcome["evidence"],
            clip={"start_sample": before, "end_sample": after, "sample_rate": first.sample_rate,
                  "start_at": first.timestamp(before), "end_at": first.timestamp(after),
                  "stream_id": first.stream_id, "channels": 1, "sample_width": 2},
            review_due_at=now + timedelta(days=policy.review_days)
                if outcome["classification"] == "human_review" else None,
            created_at=now,
        )
        session.add(record)
        session.flush()
        record.supports = [SupportingCandidate(
            source=c.source, candidate_id=c.candidate_id, ordinal=index, raw=asdict(c))
            for index, c in enumerate(supports)]
        session.flush()
        result = serialize(record)
        session.commit()
        return result, True


def evidence_key(record, permanent=False):
    prefix = ("birds" if record.domain == "bird" else "bats") if permanent else f"review/{record.domain}"
    return f"{prefix}/audio/{record.start_at:%Y/%m/%d}/{record.id}.wav"


def checked_audio(settings, record):
    if not record.audio or not record.storage_key or record.evidence_kind == "deleted":
        raise HTTPException(404, "Evidence unavailable")
    path = storage.audio_path(settings.resolved_storage_root, record.storage_key)
    if not path.is_file():
        raise HTTPException(409, "Evidence file missing; operator review required")
    data = path.read_bytes()
    if len(data) != record.audio["size_bytes"] or hashlib.sha256(data).hexdigest() != record.audio["sha256"]:
        raise HTTPException(409, "Evidence damaged; operator review required")
    return path, data


def attach_audio(engine, settings, identity, data):
    info = storage.inspect_wav(data)
    owned = None
    try:
        with Session(engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = require(session, identity)
            if record.status == "human_rejected" or record.evidence_kind in ("delete_pending", "deleted"):
                raise HTTPException(409, "Rejected evidence cannot be uploaded")
            clip = record.clip
            with wave.open(io.BytesIO(data)) as wav:
                frames = wav.getnframes()
            if (info["channels"] != 1 or info["sample_width"] != 2
                    or info["sample_rate"] != clip["sample_rate"]
                    or frames != clip["end_sample"] - clip["start_sample"]):
                raise HTTPException(422, "Audio format/sample count does not match observation clip")
            if record.audio:
                if record.audio["sha256"] != info["sha256"]:
                    raise HTTPException(409, "Observation already has different evidence")
                checked_audio(settings, record)
                return serialize(record), False
            key = evidence_key(record, record.evidence_kind == "permanent")
            path, created = storage.publish(settings.resolved_storage_root, key, data, info["sha256"])
            if created:
                owned = path
            record.storage_key = key
            record.audio = dict(info, created_at=datetime.now(timezone.utc).isoformat(), status="available")
            session.flush()
            result = serialize(record)
            session.commit()
            owned = None
            return result, True
    finally:
        if owned:
            try:
                owned.unlink(missing_ok=True)
            except OSError:
                logger.error("Evidence rollback cleanup failed")


def review(engine, settings, identity, action, payload):
    target = "human_confirmed" if action == "confirm" else "human_rejected"
    owned = None
    try:
        with Session(engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = require(session, identity)
            if record.status == target and record.review and record.review["note"] == payload.note:
                return serialize(record)
            if record.status != payload.expected_status:
                raise HTTPException(409, "Observation changed; refresh before reviewing")
            if record.status in ("human_confirmed", "human_rejected"):
                raise HTTPException(409, "Human decision is final in this phase")
            now = datetime.now(timezone.utc)
            if action == "confirm":
                _, data = checked_audio(settings, record)
                if record.evidence_kind == "review":
                    key = evidence_key(record, True)
                    path, created = storage.publish(settings.resolved_storage_root, key, data, record.audio["sha256"])
                    if created:
                        owned = path
                    record.stale_key = record.storage_key
                    record.storage_key = key
                record.evidence_kind = "permanent"
                record.cleanup_after = None
            else:
                record.evidence_kind = "delete_pending"
                record.cleanup_after = now + timedelta(days=record.policy["review_days"])
            record.status = target
            record.review = {"action": action, "note": payload.note, "at": now.isoformat(),
                             "reason": "human_confirmation" if action == "confirm" else "human_rejection"}
            record.review_due_at = None
            session.flush()
            result = serialize(record)
            session.commit()
            owned = None
        # Stale review copy is tracked in DB across crashes; best effort removal.
        # Failure does not undo the completed review; cleanup can retry it later.
        try:
            cleanup_stale(engine, settings, identity)
        except OSError:
            logger.error("Promoted review copy cleanup pending")
        return result
    finally:
        if owned:
            try:
                owned.unlink(missing_ok=True)
            except OSError:
                logger.error("Promotion rollback cleanup failed")


def cleanup_stale(engine, settings, identity):
    with Session(engine) as session:
        session.execute(text("BEGIN IMMEDIATE"))
        record = require(session, identity)
        if record.stale_key:
            storage.audio_path(settings.resolved_storage_root, record.stale_key).unlink(missing_ok=True)
            record.stale_key = None
            session.commit()


def cleanup(engine, settings, *, apply=False, now=None, limit=100):
    """Only explicitly rejected evidence. Unreviewed evidence never expires."""
    now = now or datetime.now(timezone.utc)
    report = []
    with Session(engine) as session:
        identities = list(session.scalars(select(Observation.id).where(
            ((Observation.evidence_kind == "delete_pending") & (Observation.cleanup_after <= now))
            | Observation.stale_key.is_not(None)).limit(limit)))
    for identity in identities:
        with Session(engine) as session:
            session.execute(text("BEGIN IMMEDIATE"))
            record = require(session, identity)
            rejected = record.evidence_kind == "delete_pending" and record.cleanup_after <= now
            report.append({"id": identity, "delete_rejected": rejected, "stale_copy": bool(record.stale_key)})
            if not apply:
                continue
            if record.stale_key:
                storage.audio_path(settings.resolved_storage_root, record.stale_key).unlink(missing_ok=True)
                record.stale_key = None
            if rejected:
                if record.storage_key:
                    storage.audio_path(settings.resolved_storage_root, record.storage_key).unlink(missing_ok=True)
                record.evidence_kind = "deleted"
                if record.audio:
                    record.audio = dict(record.audio, status="deleted")
            session.commit()
    return report
