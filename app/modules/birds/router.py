from datetime import timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.modules.birds.models import BirdDetection
from app.modules.birds.schemas import DetectionInput, DetectionResponse, serialize
from app.modules.birds.service import attach_audio, ingest, require_detection
from app.modules.birds.storage import audio_path

router = APIRouter(prefix="/api/birds", tags=["birds"])
JSON_LIMIT = 64 * 1024


async def bounded_body(request, maximum):
    # Count real streamed bytes; Content-Length is neither required nor trusted.
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > maximum:
            raise HTTPException(413, "Request body too large")
        body.extend(chunk)
    return bytes(body)


@router.post("/detections", response_model=DetectionResponse, status_code=201,
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/json": {"schema": DetectionInput.model_json_schema()}
             }}})
async def create_detection(request: Request, response: Response):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise HTTPException(415, "Use application/json")
    data = await bounded_body(request, JSON_LIMIT)
    try:
        payload = DetectionInput.model_validate_json(data)
    except (ValidationError, ValueError, RecursionError):
        raise HTTPException(422, "Invalid detection: use the documented JSON schema") from None
    result, created = await run_in_threadpool(ingest, request.app.state.engine, payload)
    response.status_code = 201 if created else 200
    response.headers["Location"] = f"/api/birds/detections/{result.id}"
    return result


@router.get("/detections", response_model=list[DetectionResponse])
def detections(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    scientific_name: Annotated[str | None, Query(min_length=1, max_length=255)] = None,
    since: Annotated[AwareDatetime | None, Query()] = None,
    until: Annotated[AwareDatetime | None, Query()] = None,
):
    if since and until and since >= until:
        raise HTTPException(422, "since must be earlier than until")
    statement = select(BirdDetection)
    if scientific_name is not None:
        statement = statement.where(BirdDetection.scientific_name == scientific_name)
    if since is not None:
        statement = statement.where(BirdDetection.timestamp >= since.astimezone(timezone.utc))
    if until is not None:
        statement = statement.where(BirdDetection.timestamp < until.astimezone(timezone.utc))
    with Session(request.app.state.engine) as session:
        records = session.scalars(statement.order_by(
            BirdDetection.timestamp.desc(), BirdDetection.id.desc()
        ).limit(limit))
        return [serialize(record) for record in records]


@router.get("/latest", response_model=DetectionResponse)
def latest(request: Request):
    with Session(request.app.state.engine) as session:
        record = session.scalar(select(BirdDetection).order_by(
            BirdDetection.timestamp.desc(), BirdDetection.id.desc()
        ).limit(1))
        if record is None:
            raise HTTPException(404, "No detections")
        return serialize(record)


@router.get("/detections/{detection_id}", response_model=DetectionResponse)
def detection(request: Request, detection_id: UUID):
    with Session(request.app.state.engine) as session:
        return serialize(require_detection(session, detection_id))


@router.put("/detections/{detection_id}/audio", response_model=DetectionResponse,
            status_code=201, openapi_extra={"requestBody": {
                "required": True, "content": {"audio/wav": {
                    "schema": {"type": "string", "format": "binary"}
                }}
            }})
async def upload_audio(request: Request, response: Response, detection_id: UUID):
    mime = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if mime not in {"audio/wav", "audio/x-wav", "audio/wave"}:
        raise HTTPException(415, "Use audio/wav with raw PCM WAV bytes")
    data = await bounded_body(request, request.app.state.settings.max_audio_bytes)
    result, created = await run_in_threadpool(
        attach_audio, request.app.state.engine, request.app.state.settings,
        detection_id, data,
    )
    response.status_code = 201 if created else 200
    response.headers["Location"] = result.audio_url
    return result


@router.get("/detections/{detection_id}/audio")
def get_audio(request: Request, detection_id: UUID):
    with Session(request.app.state.engine) as session:
        audio = require_detection(session, detection_id).audio
        if audio is None or audio.status != "available":
            raise HTTPException(404, "Audio unavailable")
        path = audio_path(request.app.state.settings.resolved_storage_root, audio.storage_key)
        if not path.is_file():
            raise HTTPException(404, "Audio unavailable")
        return FileResponse(
            path, media_type="audio/wav", filename=f"{detection_id}.wav",
            content_disposition_type="inline",
            headers={"X-Content-Type-Options": "nosniff"},
        )
