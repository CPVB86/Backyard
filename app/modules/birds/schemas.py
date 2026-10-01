import math
from app.core.species_names import localized_name
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints,
    field_validator,
)

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]
Version = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]


class DetectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: Name
    detected_at: AwareDatetime
    scientific_name: Name
    common_name: Name | None = None
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    source: Name
    source_version: Version | None = None
    model_version: Version | None = None
    raw_metadata: dict[str, Any] | None = None

    @field_validator("detected_at", mode="before")
    @classmethod
    def iso_timestamp_only(cls, value):
        if not isinstance(value, str) or "T" not in value:
            raise ValueError("Use an ISO 8601 timestamp with T and a timezone")
        return value

    @field_validator("detected_at")
    @classmethod
    def utc_timestamp(cls, value):
        try:
            return value.astimezone(timezone.utc)
        except (ValueError, OverflowError):
            raise ValueError("Timestamp is outside the supported UTC range") from None

    @field_validator("confidence", mode="before")
    @classmethod
    def number_only(cls, value):
        if isinstance(value, bool) or not isinstance(value, (float, int)):
            raise ValueError("Confidence must be a JSON number between 0 and 1")
        return value

    @field_validator("raw_metadata")
    @classmethod
    def finite_metadata(cls, value):
        def check(item):
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("Metadata must contain finite JSON numbers")
            if isinstance(item, dict):
                for child in item.values():
                    check(child)
            elif isinstance(item, list):
                for child in item:
                    check(child)
        check(value)
        return value


class AudioResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    content_type: str
    codec: str
    sample_rate: int
    channels: int
    sample_width: int
    duration_seconds: float
    size_bytes: int
    sha256: str
    created_at: datetime
    status: str


class DetectionResponse(BaseModel):
    id: str
    detected_at: datetime
    timestamp: datetime  # Read-only compatibility alias for foundation clients.
    scientific_name: str
    common_name: str | None
    common_name_nl: str | None
    common_name_de: str | None
    confidence: float
    source: str
    event_id: str | None
    source_version: str | None
    model_version: str | None
    raw_metadata: dict | None
    verification_status: Literal["unreviewed", "confirmed", "rejected"]
    created_at: datetime
    audio_reference: str | None  # API URL, never the legacy arbitrary file reference.
    audio_url: str | None
    audio: AudioResponse | None


def serialize(record):
    audio = record.audio
    url = (
        f"/api/birds/detections/{record.id}/audio"
        if audio is not None and audio.status == "available" else None
    )
    return DetectionResponse(
        id=record.id, detected_at=record.timestamp, timestamp=record.timestamp,
        scientific_name=record.scientific_name, common_name=record.common_name,
        common_name_de=localized_name(record.scientific_name, record.common_name, "de"),
        common_name_nl=localized_name(record.scientific_name, record.common_name, "nl"),
        confidence=record.confidence, source=record.source, event_id=record.event_id,
        source_version=record.source_version, model_version=record.model_version,
        raw_metadata=record.raw_metadata, verification_status=record.verification_status,
        created_at=record.created_at, audio_reference=url, audio_url=url,
        audio=AudioResponse.model_validate(audio) if audio is not None else None,
    )
