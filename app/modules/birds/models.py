from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import CheckConstraint, Float, Index, JSON, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base, UTCDateTime


class BirdDetection(Base):
    __tablename__ = "bird_detections"
    __table_args__ = (
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        Index("ix_birds_timestamp", "timestamp"),
        Index("ix_birds_species_timestamp", "scientific_name", "timestamp"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    timestamp: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    scientific_name: Mapped[str] = mapped_column(String(255), nullable=False)
    common_name: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    audio_reference: Mapped[str | None] = mapped_column(String(2048))
    model_version: Mapped[str | None] = mapped_column(String(100))
    raw_metadata: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
