from dataclasses import asdict
from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy import select, func, or_
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from app.modules.birds.router import bounded_body
from app.modules.birds.storage import audio_path
from app.modules.observations.models import Observation
from app.modules.observations.schemas import ObservationInput, ReviewInput, serialize, observation_request_schema
from app.modules.observations import service

router = APIRouter(prefix="/api/observations", tags=["observations"])
Status = Literal["auto_accepted", "pending_review", "review_recommended",
                 "human_confirmed", "human_rejected"]


@router.get("/policy")
def get_policy(request: Request):
    policy = request.app.state.observation_policy
    return {"fingerprint": policy.fingerprint, "values": asdict(policy)}


@router.post("", status_code=201, openapi_extra={"requestBody": {"required": True, "content": {
    "application/json": {"schema": observation_request_schema()}}}})
async def create(request: Request, response: Response):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Use application/json")
    body = await bounded_body(request, 256 * 1024)
    try:
        payload = ObservationInput.model_validate_json(body)
    except (ValidationError, ValueError, RecursionError):
        raise HTTPException(422, "Invalid observation; use documented schema") from None
    result, created = await run_in_threadpool(
        service.ingest, request.app.state.engine, request.app.state.observation_policy, payload)
    response.status_code = 201 if created else 200
    await run_in_threadpool(request.app.state.generator_scheduler.accepted, result)
    return result


def filtered(statement, domain, status):
    if domain:
        statement = statement.where(Observation.domain == domain)
    if status:
        statement = statement.where(Observation.status.in_(status))
    return statement


def query(request, domain, status, limit):
    statement = filtered(select(Observation), domain, status)
    with Session(request.app.state.engine) as session:
        return [serialize(row) for row in session.scalars(
            statement.order_by(Observation.start_at.desc(), Observation.id.desc()).limit(limit))]


@router.get("")
def observations(request: Request, domain: Literal["bird", "bat"] | None = None,
                 status: Status | None = None,
                 limit: Annotated[int, Query(ge=1, le=100)] = 50):
    return query(request, domain, [status] if status else None, limit)


def review_records(request, domain):
    from app.modules.observations.classification import category
    from observations.policy import VERSION
    statement = filtered(select(Observation), domain, ["pending_review", "review_recommended"])
    # Migrated/new rows are filtered in SQL; legacy rows use the same classifier.
    statement = statement.where(or_(
        Observation.decision["classification"].as_string() == "human_review",
        Observation.decision["policy_version"].as_string() != VERSION,
        Observation.decision["policy_version"].as_string().is_(None)))
    with Session(request.app.state.engine) as session:
        for row in session.scalars(statement.order_by(Observation.start_at.desc(), Observation.id.desc())):
            if category(row) == "human_review":
                yield row


def selected_review_records(request, domain, identity_override=None):
    if identity_override is None:
        yield from review_records(request, domain)
        return
    from app.modules.observations.schemas import identity_overrides
    statement = filtered(select(Observation), domain, ["pending_review", "review_recommended"])
    with Session(request.app.state.engine) as session:
        for row in session.scalars(statement.order_by(Observation.start_at.desc(), Observation.id.desc())):
            if identity_override in identity_overrides(row):
                yield row


@router.get("/review")
def review_list(request: Request, domain: Literal["bird", "bat"] | None = None,
                limit: Annotated[int, Query(ge=1, le=100)] = 50,
                identity_override: Literal["otje"] | None = None):
    from itertools import islice
    return [serialize(row) for row in islice(selected_review_records(request, domain, identity_override), limit)]


@router.get("/count")
def observation_count(request: Request, domain: Literal["bird", "bat"] | None = None,
                      status: Status | None = None, review_only: bool = False,
                      identity_override: Literal["otje"] | None = None):
    if identity_override is not None and not review_only:
        raise HTTPException(422, "identity_override requires review_only")
    if review_only:
        if status is not None:
            raise HTTPException(422, "review_only cannot be combined with status")
        result = {"count": sum(1 for _ in selected_review_records(request, domain, identity_override))}
        if identity_override is not None:
            result["identity_override"] = identity_override
        return result
    statement = filtered(select(func.count()).select_from(Observation), domain, [status] if status else None)
    with Session(request.app.state.engine) as session:
        return {"count": session.scalar(statement)}


@router.get("/{identity}")
def detail(request: Request, identity: UUID):
    with Session(request.app.state.engine) as session:
        return serialize(service.require(session, identity))


@router.put("/{identity}/audio", status_code=201, openapi_extra={"requestBody": {
    "required": True, "content": {"audio/wav": {"schema": {"type": "string", "format": "binary"}}}}})
async def put_audio(request: Request, response: Response, identity: UUID):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() not in {
            "audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(415, "Use raw PCM audio/wav")
    body = await bounded_body(request, request.app.state.settings.max_audio_bytes)
    result, created = await run_in_threadpool(service.attach_audio, request.app.state.engine,
                                             request.app.state.settings, identity, body)
    response.status_code = 201 if created else 200
    return result


@router.get("/{identity}/audio")
def get_audio(request: Request, identity: UUID):
    with Session(request.app.state.engine) as session:
        record = service.require(session, identity)
        if not record.audio or record.evidence_kind == "deleted":
            raise HTTPException(404, "Evidence unavailable")
        path = audio_path(request.app.state.settings.resolved_storage_root, record.storage_key)
        if not path.is_file():
            raise HTTPException(404, "Evidence unavailable")
        return FileResponse(path, media_type="audio/wav", filename=f"{identity}.wav",
                            content_disposition_type="inline", headers={"X-Content-Type-Options": "nosniff"})


@router.post("/{identity}/confirm")
def confirm(request: Request, identity: UUID, payload: ReviewInput):
    result = service.review(request.app.state.engine, request.app.state.settings, identity, "confirm", payload)
    request.app.state.generator_scheduler.accepted(result)
    return result


@router.post("/{identity}/reject")
def reject(request: Request, identity: UUID, payload: ReviewInput):
    return service.review(request.app.state.engine, request.app.state.settings, identity, "reject", payload)
