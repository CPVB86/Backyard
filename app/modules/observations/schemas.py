import json
from app.core.species_names import localized_name
from typing import Annotated, Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from observations.policy import RawCandidate

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255)]


class CandidateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: Name
    window_id: Name
    source: Name
    stream_id: Name
    domain: Literal["bird", "bat", "unsupported", "unknown"]
    scientific_name: Name
    common_name: Name
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False, strict=True)
    sample_rate: int = Field(ge=8000, le=192000, strict=True)
    start_sample: int = Field(ge=0, strict=True)
    end_sample: int = Field(gt=0, strict=True)
    utc_anchor: Name
    model_version: Name
    plausibility: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_candidate(self):
        json.dumps(self.model_dump(), allow_nan=False)
        RawCandidate(**self.model_dump())
        return self


class ObservationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: Name
    policy_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidates: list[CandidateInput] = Field(min_length=1, max_length=32)
    clip_start_sample: int = Field(ge=0, strict=True)
    clip_end_sample: int = Field(gt=0, strict=True)


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_status: Literal["auto_accepted", "pending_review", "review_recommended",
                             "human_confirmed", "human_rejected"]
    note: str = Field(default="", max_length=1000)


def serialize(record):
    from app.modules.observations.classification import category
    audio_available = bool(record.audio and record.audio.get("status") == "available"
                           and record.evidence_kind != "deleted")
    return {
        "timestamp": record.start_at, "common_name_en": record.common_name,
        "confidence": record.best_confidence, "supports": len(record.supports),
        "evidence": record.evidence_kind, "audio_available": audio_available,
        "id": record.id, "event_id": record.event_id, "source": record.source,
        "domain": record.domain, "scientific_name": record.scientific_name,
        "common_name_de": localized_name(record.scientific_name, record.common_name, "de"),
        "common_name_nl": localized_name(record.scientific_name, record.common_name, "nl"),
        "common_name": record.common_name, "start_at": record.start_at, "end_at": record.end_at,
        "best_confidence": record.best_confidence, "supporting_candidate_count": len(record.supports),
        "candidates": [candidate.raw for candidate in record.supports],
        "status": record.status, "classification": category(record), "decision": record.decision, "policy": record.policy,
        "clip": record.clip, "evidence_kind": record.evidence_kind,
        "audio": record.audio,
        "audio_url": f"/api/observations/{record.id}/audio" if record.audio and record.evidence_kind != "deleted" else None,
        "review_due_at": record.review_due_at, "cleanup_after": record.cleanup_after,
        "review": record.review, "created_at": record.created_at,
    }


def observation_request_schema():
    """Inline local definitions so Swagger references resolve at the API root."""
    schema = ObservationInput.model_json_schema()
    definitions = schema.pop("$defs", {})
    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if isinstance(value, dict):
            if "$ref" in value:
                return expand(definitions[value["$ref"].rsplit("/", 1)[-1]])
            return {key: expand(item) for key, item in value.items()}
        return value
    return expand(schema)
