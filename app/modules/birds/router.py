from datetime import datetime
from typing import Annotated
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.modules.birds.models import BirdDetection

router = APIRouter(prefix="/api/birds", tags=["birds"])


class DetectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    timestamp: datetime
    scientific_name: str
    common_name: str | None
    confidence: float
    source: str
    audio_reference: str | None
    model_version: str | None
    created_at: datetime


@router.get("/detections", response_model=list[DetectionResponse])
def detections(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50):
    with Session(request.app.state.engine) as session:
        records = session.scalars(select(BirdDetection).order_by(
            BirdDetection.timestamp.desc(), BirdDetection.id.desc()
        ).limit(limit))
        return [DetectionResponse.model_validate(record) for record in records]
