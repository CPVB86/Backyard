"""Read-only AvianVisitors view models built from Backyard-owned data."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.species_names import localized_name
from app.modules.observations.models import Observation
from generator.store import public_status

ACCEPTED_STATUSES = ("auto_accepted", "human_confirmed")
LOCALES = ("nl", "en", "de")


def display_name(scientific_name: str, english_name: str | None, locale: str) -> str:
    """Select an existing Backyard name, with English/scientific fallbacks."""
    english = (english_name or "").strip()
    translated = english if locale == "en" else localized_name(scientific_name, english, locale)
    return (translated or "").strip() or english or scientific_name


def recent(engine, generator, hours: int, locale: str, *, now: datetime | None = None) -> dict:
    """Aggregate accepted bird observations and attach public Generator metadata."""
    if locale not in LOCALES:
        raise ValueError("Unsupported locale")
    end = now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    start = end - timedelta(hours=hours)
    statement = (
        select(
            Observation.scientific_name,
            func.max(Observation.common_name).label("common_name"),
            func.count(Observation.id).label("count"),
            func.min(Observation.start_at).label("first_observed_at"),
            func.max(Observation.start_at).label("last_observed_at"),
        )
        .where(
            Observation.domain == "bird",
            Observation.status.in_(ACCEPTED_STATUSES),
            Observation.start_at >= start,
            Observation.start_at <= end,
        )
        .group_by(Observation.scientific_name)
        .order_by(func.count(Observation.id).desc(), Observation.scientific_name.asc())
    )
    with Session(engine) as session:
        rows = list(session.execute(statement))

    species = []
    for row in rows:
        generated = public_status(generator.lookup("bird", row.scientific_name))
        pose_assets = {pose: generated["assets"][pose]
                       for pose in ("perched", "flight") if pose in generated["assets"]}
        species.append({
            "scientific_name": row.scientific_name,
            "common_name": display_name(row.scientific_name, row.common_name, locale),
            "common_name_en": (row.common_name or "").strip() or row.scientific_name,
            "count": row.count,
            "first_observed_at": row.first_observed_at,
            "last_observed_at": row.last_observed_at,
            "generator_status": generated["status"],
            "generation": generated.get("generation"),
            "missing_assets": generated["missing_assets"],
            "assets": pose_assets,
        })
    return {
        "locale": locale,
        "hours": hours,
        "window_start": start,
        "window_end": end,
        "observation_count": sum(item["count"] for item in species),
        "species": species,
    }
