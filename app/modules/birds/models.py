from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, UTCDateTime


class BirdDetection(Base):
    __tablename__ = "bird_detections"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("verification_status IN ('unreviewed', 'confirmed', 'rejected')", name="verification_status_values"),
        Index("ix_birds_timestamp", "timestamp"),
        Index("ix_birds_species_timestamp", "scientific_name", "timestamp"),
        Index("uq_birds_source_event", "source", "event_id", unique=True),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    scientific_name: Mapped[str] = mapped_column(String(255), nullable=False)
    common_name: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    # Retained for foundation data, never used to open arbitrary legacy files.
    audio_reference: Mapped[str | None] = mapped_column(String(2048))
    model_version: Mapped[str | None] = mapped_column(String(100))
    raw_metadata: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    event_id: Mapped[str | None] = mapped_column(String(255))
    source_version: Mapped[str | None] = mapped_column(String(100))
    ingest_hash: Mapped[str | None] = mapped_column(String(64))
    verification_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unreviewed", server_default="unreviewed"
    )
    audio: Mapped["BirdAudio | None"] = relationship(lazy="selectin", uselist=False)


class BirdAudio(Base):
    __tablename__ = "bird_audio"
    detection_id: Mapped[str] = mapped_column(
        ForeignKey("bird_detections.id"), primary_key=True
    )
    storage_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    codec: Mapped[str] = mapped_column(String(30), nullable=False)
    sample_rate: Mapped[int] = mapped_column(Integer, nullable=False)
    channels: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_width: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    # A future retention job may mark this missing/deleted while retaining metadata.
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="available", server_default="available"
    )
