"""Shared, read-only presentation snapshot for birds and bats."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from random import SystemRandom
import re
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, literal
from sqlalchemy.orm import Session
from app.modules.observations.models import Observation
from app.modules.observations.effective import species_expression, name_expression
from app.modules.avian_visitors.service import _identity_expression, ACCEPTED_STATUSES
from app.modules.avian_visitors.identities import resolve
from app.core.species_names import localized_name
from app.modules.species.models import Species


def parse_timezone(name):
    if re.fullmatch(r"[+-](?:0[0-9]|1[0-4]):[0-5][0-9]", name):
        sign = 1 if name[0] == "+" else -1
        return timezone(sign * timedelta(hours=int(name[1:3]), minutes=int(name[4:])))
    return ZoneInfo(name)


def snapshot(engine, module, period, timezone_name="Europe/Amsterdam", *, now=None, identity_filter=None):
    domain = {"birds": "bird", "bats": "bat"}[module]
    end = now or datetime.now(timezone.utc)
    zone = parse_timezone(timezone_name)
    today = end.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    start = (today if period == "today" else None if period == "all"
             else end - timedelta(hours={"24h": 24, "7d": 168, "30d": 720}[period]))
    scientific = species_expression()
    identity = _identity_expression() if domain == "bird" else literal(None)
    accepted = [Observation.domain == domain, Observation.status.in_(ACCEPTED_STATUSES), Observation.start_at <= end]

    if identity_filter is not None:
        accepted.append(identity == identity_filter)

    def aggregate(session, since):
        query = select(scientific.label("scientific_name"), identity.label("identity"),
                       func.max(name_expression()).label("name"), func.count().label("count"),
                       func.min(Observation.start_at).label("first_seen"), func.max(Observation.start_at).label("last_seen"))
        query = query.where(*accepted).group_by(scientific, identity)
        if since is not None:
            query = query.where(Observation.start_at >= since)
        return list(session.execute(query))

    with Session(engine) as session:
        # SQLite's legacy driver does not begin a transaction for SELECT itself.
        session.connection().exec_driver_sql("BEGIN")
        lifetime = aggregate(session, None)
        rows = lifetime if start is None else aggregate(session, start)
        today_rows = rows if start == today else aggregate(session, today)
        names = {row.scientific_name for row in rows}
        catalog = {row.scientific_name: row for row in session.scalars(
            select(Species).where(Species.domain == domain, Species.scientific_name.in_(names)))} if names else {}
        dates_query = select(Observation.start_at).where(*accepted)
        if start is not None:
            dates_query = dates_query.where(Observation.start_at >= start)
        active_days = len({stamp.astimezone(zone).date() for stamp in session.scalars(dates_query.execution_options(yield_per=1000))})
        species = []
        for row in rows:
            entry = catalog.get(row.scientific_name)
            profile = resolve(row.identity, row.scientific_name) if domain == "bird" else None
            name = (profile["display_name"] if profile else
                    (entry.common_name_nl if entry else None) or localized_name(row.scientific_name, row.name, "nl") or row.scientific_name)
            species.append({"species_id": sha256(f"{domain}|{row.scientific_name}|{row.identity or ''}".encode()).hexdigest(),
                "name": name, "scientific_name": row.scientific_name, "identity": row.identity,
                "count": row.count, "first_seen": row.first_seen, "last_seen": row.last_seen,
                "wikipedia_url": (entry.wikipedia_nl_url or entry.wikipedia_en_url) if entry else None,
                "observations_url": entry.source_url if entry else None,
                "assets": profile["asset_ids"] if profile else {"perched": "perched", "flight": "flight"}})
        # One canonical tie-breaker, shared by every field and client.
        species.sort(key=lambda row: (row["scientific_name"], row["identity"] or ""))
        if identity_filter is not None and species:
            # One local individual may have evidence under several recognized species.
            # Keep the latest effective species for links/assets, aggregate all marked evidence.
            representative = max(species, key=lambda row: row["last_seen"])
            species = [dict(representative,
                species_id=sha256(f"{domain}|identity|{identity_filter}".encode()).hexdigest(),
                count=sum(row["count"] for row in species),
                first_seen=min(row["first_seen"] for row in species),
                last_seen=max(row["last_seen"] for row in species))]
        rankings = {
            "last": sorted(species, key=lambda row: row["last_seen"], reverse=True),
            "first": sorted(species, key=lambda row: row["first_seen"]),
            "most": sorted(species, key=lambda row: row["count"], reverse=True),
            "rarest": sorted(species, key=lambda row: row["count"]),
            "random": SystemRandom().sample(species, len(species)),
        }
        firsts = {}
        for row in lifetime:
            firsts[row.scientific_name] = min(firsts.get(row.scientific_name, row.first_seen), row.first_seen)
        return {"module": module, "period": period, "timezone": timezone_name, "identity": identity_filter,
            "window_start": start, "window_end": end, "species": species,
            "rankings": {key: [row["species_id"] for row in values[:10]] for key, values in rankings.items()},
            "stats": {"total_observations": sum(row.count for row in rows), "unique_species": len(names),
                "today_observations": sum(row.count for row in today_rows),
                "today_species": len({row.scientific_name for row in today_rows}),
                "last_activity": max((row.last_seen for row in rows), default=None),
                "new_species": sum(1 for stamp in firsts.values() if start is None or stamp >= start),
                "active_days": active_days}}
