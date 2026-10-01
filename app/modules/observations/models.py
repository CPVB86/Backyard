from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base, UTCDateTime


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        CheckConstraint("domain IN ('bird','bat')"),
        CheckConstraint("status IN ('auto_accepted','pending_review','review_recommended','human_confirmed','human_rejected')"),
        CheckConstraint("evidence_kind IN ('permanent','review','delete_pending','deleted')"),
        Index("uq_observation_source_event", "source", "event_id", unique=True),
        Index("ix_observations_review", "status", "start_at"),
        Index("ix_observations_domain_time", "domain", "start_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ingest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    scientific_name: Mapped[str] = mapped_column(String(255), nullable=False)
    common_name: Mapped[str] = mapped_column(String(255), nullable=False)
    start_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    end_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    best_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    decision: Mapped[dict] = mapped_column(JSON, nullable=False)
    policy: Mapped[dict] = mapped_column(JSON, nullable=False)
    clip: Mapped[dict] = mapped_column(JSON, nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    audio: Mapped[dict | None] = mapped_column(JSON)
    storage_key: Mapped[str | None] = mapped_column(String(255))
    stale_key: Mapped[str | None] = mapped_column(String(255))
    review_due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    cleanup_after: Mapped[datetime | None] = mapped_column(UTCDateTime())
    review: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(timezone.utc), nullable=False)
    supports: Mapped[list["SupportingCandidate"]] = relationship(lazy="selectin", order_by="SupportingCandidate.ordinal")


class SupportingCandidate(Base):
    __tablename__ = "observation_candidates"
    __table_args__ = (Index("uq_candidate_source_event", "source", "candidate_id", unique=True),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    observation_id: Mapped[str] = mapped_column(ForeignKey("observations.id"), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    candidate_id: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict] = mapped_column(JSON, nullable=False)
