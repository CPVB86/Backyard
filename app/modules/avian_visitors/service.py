"""Read-only AvianVisitors view models built from Backyard-owned data."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, case, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.species_names import localized_name
from app.modules.observations.models import Observation
from app.modules.avian_visitors.identities import PROFILES, resolve
from app.modules.species.models import Species
from app.modules.species import service as species_service
from generator.store import public_status

ACCEPTED_STATUSES = ("auto_accepted", "human_confirmed")
LOCALES = ("nl", "en", "de")


def display_name(scientific_name: str, english_name: str | None, locale: str) -> str:
    """Select an existing Backyard name, with English/scientific fallbacks."""
    english = (english_name or "").strip()
    translated = english if locale == "en" else localized_name(scientific_name, english, locale)
    return (translated or "").strip() or english or scientific_name


def _end(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _filters(start: datetime | None, end: datetime):
    values = [
        Observation.domain == "bird",
        Observation.status.in_(ACCEPTED_STATUSES),
        Observation.start_at <= end,
    ]
    if start is not None:
        values.append(Observation.start_at >= start)
    return values


def _identity_expression():
    return case(
        (and_(Observation.review["identity_override"].as_string() == "otje",
              Observation.scientific_name.in_(PROFILES["otje"]["source_species"])), "otje"),
        else_=None,
    )


def search(engine, query: str, limit: int = 8) -> list[dict]:
    """Search the bird catalog and known observation names without generating assets."""
    term = query.strip()
    if not term:
        return []
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    contains = f"%{escaped}%"
    starts = f"{escaped}%"
    accepted = and_(Observation.domain == "bird", Observation.status.in_(ACCEPTED_STATUSES),
                    Observation.scientific_name == Species.scientific_name)
    observed_name = exists(select(Observation.id).where(
        accepted, Observation.common_name.ilike(contains, escape="\\")))
    counts = (select(Observation.scientific_name.label("scientific_name"),
                     func.count(Observation.id).label("count"),
                     func.max(Observation.start_at).label("last_observed_at"))
              .where(Observation.domain == "bird", Observation.status.in_(ACCEPTED_STATUSES))
              .group_by(Observation.scientific_name).subquery())
    match = or_(Species.common_name_nl.ilike(contains, escape="\\"),
                Species.scientific_name.ilike(contains, escape="\\"),
                Species.wikipedia_title_nl.ilike(contains, escape="\\"),
                Species.wikipedia_title_en.ilike(contains, escape="\\"), observed_name)
    priority = case(
        (Species.common_name_nl.ilike(starts, escape="\\"), 0),
        (Species.scientific_name.ilike(starts, escape="\\"), 1), else_=2)
    statement = (select(Species.common_name_nl, Species.scientific_name,
                        counts.c.count, counts.c.last_observed_at)
                 .outerjoin(counts, counts.c.scientific_name == Species.scientific_name)
                 .where(Species.domain == "bird", match)
                 .order_by(priority, Species.common_name_nl.asc(), Species.scientific_name.asc())
                 .limit(limit))
    with Session(engine) as session:
        rows = session.execute(statement)
        return [{"common_name_nl": row.common_name_nl or row.scientific_name,
                 "scientific_name": row.scientific_name, "observation_count": row.count or 0,
                 "last_observed_at": row.last_observed_at} for row in rows]


def _aggregate(engine, start: datetime | None, end: datetime):
    identity_id = _identity_expression().label("identity_id")
    statement = (
        select(
            Observation.scientific_name,
            identity_id,
            func.max(Observation.common_name).label("common_name"),
            func.count(Observation.id).label("count"),
            func.min(Observation.start_at).label("first_observed_at"),
            func.max(Observation.start_at).label("last_observed_at"),
        )
        .where(*_filters(start, end))
        .group_by(Observation.scientific_name, identity_id)
        .order_by(func.count(Observation.id).desc(), Observation.scientific_name.asc(), identity_id.asc())
    )
    with Session(engine) as session:
        return list(session.execute(statement))


def _species(rows, generator, locale: str, *, assets: bool) -> list[dict]:
    result = []
    for row in rows:
        profile = resolve(row.identity_id, row.scientific_name)
        source_name = display_name(row.scientific_name, row.common_name, locale)
        item = {
            "scientific_name": row.scientific_name,
            "common_name": profile["display_name"] if profile else source_name,
            "common_name_en": (row.common_name or "").strip() or row.scientific_name,
            "identity_id": profile["id"] if profile else None,
            "subtitle": profile["subtitle"] if profile else None,
            "source_common_name": source_name,
            "count": row.count,
            "first_observed_at": row.first_observed_at,
            "last_observed_at": row.last_observed_at,
        }
        if assets:
            generated = public_status(generator.lookup("bird", row.scientific_name))
            pose_assets = _pose_assets(generated["assets"], profile)
            item.update({
                "generator_status": generated["status"],
                "generation": generated.get("generation"),
                "missing_assets": generated["missing_assets"],
                "assets": pose_assets,
            })
        result.append(item)
    return result


def _pose_assets(assets, profile=None):
    result = {pose: assets[pose] for pose in ("perched", "flight") if pose in assets}
    if profile:
        for pose, asset_id in profile["asset_ids"].items():
            if asset_id in assets:
                result[pose] = assets[asset_id]
    return result


def detail(engine, settings, generator, scientific_name: str, locale: str, identity_id: str | None = None):
    """Compose local presentation over unchanged species enrichment and evidence."""
    _validate(locale)
    profile = resolve(identity_id, scientific_name)
    result = species_service.detail(
        engine, settings, generator, "bird", scientific_name, locale,
        identity_override=profile["id"] if profile else None,
    )
    if result is None:
        return None
    result["generator"]["assets"] = _pose_assets(result["generator"]["assets"], profile)
    if profile:
        result["presentation"] = {
            "id": profile["id"], "display_name": profile["display_name"],
            "subtitle": profile["subtitle"], "images": profile["images"],
        }
        result["local_content"] = {
            "summary_nl": profile["summary_nl"], "fact_nl": profile["fact_nl"],
        }
    return result


def _validate(locale: str):
    if locale not in LOCALES:
        raise ValueError("Unsupported locale")


def recent(engine, generator, hours: int, locale: str, *, now: datetime | None = None) -> dict:
    """Aggregate accepted bird observations and attach public Generator metadata."""
    _validate(locale)
    end = _end(now)
    start = end - timedelta(hours=hours)
    species = _species(_aggregate(engine, start, end), generator, locale, assets=True)
    return {
        "locale": locale,
        "hours": hours,
        "window_start": start,
        "window_end": end,
        "observation_count": sum(item["count"] for item in species),
        "species": species,
    }


def lifelist(engine, generator, locale: str, *, now: datetime | None = None) -> dict:
    """All accepted bird species, ordered by first arrival then identity."""
    _validate(locale)
    end = _end(now)
    species = _species(_aggregate(engine, None, end), generator, locale, assets=True)
    species.sort(key=lambda item: (item["first_observed_at"], item["scientific_name"]))
    return {
        "locale": locale,
        "observation_count": sum(item["count"] for item in species),
        "species_count": len(species),
        "species": species,
    }


def stats(engine, hours: int, locale: str, *, now: datetime | None = None) -> dict:
    """Efficient period aggregates for the AvianVisitors statistics sheet."""
    _validate(locale)
    end = _end(now)
    start = end - timedelta(hours=hours)
    period_rows = _aggregate(engine, start, end)
    period_species = _species(period_rows, None, locale, assets=False)
    all_rows = _aggregate(engine, None, end)
    all_species = _species(all_rows, None, locale, assets=False)
    names = {(item["scientific_name"], item["identity_id"]): item["common_name"]
             for item in period_species}
    bucket_format = "%Y-%m-%dT%H:00:00Z" if hours <= 48 else "%Y-%m-%dT00:00:00Z"
    bucket = func.strftime(bucket_format, Observation.start_at)
    hour = func.strftime("%H", Observation.start_at)
    # The all-time sheet keeps all-time totals but, like the original app,
    # limits the plotted history so a long-running station does not return
    # tens of thousands of empty daily buckets to a browser.
    timeline_start = max(start, end - timedelta(days=30)) if hours > 24 * 365 else start
    with Session(engine) as session:
        timeline_counts = dict(session.execute(
            select(bucket, func.count(Observation.id)).where(*_filters(timeline_start, end))
            .group_by(bucket).order_by(bucket)
        ).tuples().all())
        rhythm = [0] * 24
        for value, count in session.execute(
                select(hour, func.count(Observation.id)).where(*_filters(start, end))
                .group_by(hour).order_by(hour)):
            rhythm[int(value)] = count
        heatmap = {}
        identity_id = _identity_expression().label("identity_id")
        for scientific_name, identity, value, count in session.execute(
                select(Observation.scientific_name, identity_id, hour, func.count(Observation.id))
                .where(*_filters(start, end)).group_by(Observation.scientific_name, identity_id, hour)
                .order_by(Observation.scientific_name, identity_id, hour)):
            heatmap.setdefault((scientific_name, identity), [0] * 24)[int(value)] = count
    if hours <= 48:
        cursor = timeline_start.replace(minute=0, second=0, microsecond=0)
        step = timedelta(hours=1)
        key_format = "%Y-%m-%dT%H:00:00Z"
    else:
        cursor = timeline_start.replace(hour=0, minute=0, second=0, microsecond=0)
        step = timedelta(days=1)
        key_format = "%Y-%m-%dT00:00:00Z"
    timeline = []
    while cursor <= end:
        value = cursor.strftime(key_format)
        timeline.append({"bucket_start": value, "count": timeline_counts.get(value, 0)})
        cursor += step
    newest = sorted(all_species, key=lambda item: (
        item["first_observed_at"], item["scientific_name"]), reverse=True)[:8]
    return {
        "locale": locale,
        "hours": hours,
        "window_start": start,
        "window_end": end,
        "observation_count": sum(item["count"] for item in period_species),
        "species_count": len(period_species),
        "all_time_observation_count": sum(item["count"] for item in all_species),
        "all_time_species_count": len(all_species),
        "first_observed_at": min((item["first_observed_at"] for item in all_species), default=None),
        "last_observed_at": max((item["last_observed_at"] for item in all_species), default=None),
        "species": period_species,
        "newest_species": newest,
        "timeline_granularity": "hour" if hours <= 48 else "day",
        "timeline": timeline,
        "rhythm": rhythm,
        "hourly_species": [
            {"scientific_name": scientific_name, "identity_id": identity,
             "common_name": names[(scientific_name, identity)], "counts": counts}
            for (scientific_name, identity), counts in heatmap.items()
        ],
    }
