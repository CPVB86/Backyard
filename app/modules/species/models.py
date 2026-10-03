from datetime import datetime, timezone
from sqlalchemy import CheckConstraint, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base, UTCDateTime


class Species(Base):
    __tablename__ = "species_catalog"
    __table_args__ = (
        CheckConstraint("domain IN ('bird','bat')"),
        Index("uq_species_identity", "domain", "scientific_name", unique=True),
        Index("uq_species_source_id", "source", "source_species_id", unique=True),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    scientific_name: Mapped[str] = mapped_column(String(255), nullable=False)
    common_name_nl: Mapped[str | None] = mapped_column(String(255))
    authority: Mapped[str | None] = mapped_column(String(255))
    family: Mapped[str | None] = mapped_column(String(255))
    taxon_type: Mapped[str | None] = mapped_column(String(100))
    taxon_group: Mapped[str | None] = mapped_column(String(100))
    parent_source_species_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_species_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str | None] = mapped_column(String(2048))
    rarity: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str | None] = mapped_column(String(100))
    obscurity: Mapped[str | None] = mapped_column(String(255))
    source_metadata: Mapped[dict] = mapped_column(JSON, nullable=False)
    wikipedia_nl_url: Mapped[str | None] = mapped_column(String(2048))
    wikipedia_title_nl: Mapped[str | None] = mapped_column(String(255))
    wikipedia_match_status: Mapped[str | None] = mapped_column(String(20))
    wikipedia_match_note: Mapped[str | None] = mapped_column(Text)
    wikipedia_en_url: Mapped[str | None] = mapped_column(String(2048))
    wikipedia_title_en: Mapped[str | None] = mapped_column(String(255))
    summary_nl: Mapped[str | None] = mapped_column(Text)
    fact_nl: Mapped[str | None] = mapped_column(Text)
    encyclopedia_source: Mapped[str | None] = mapped_column(String(255))
    imported_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=lambda: datetime.now(timezone.utc), nullable=False)
