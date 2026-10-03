"""Offline, idempotent Waarneming.nl species CSV import."""
import csv
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.modules.species.models import Species

HEADERS = ("id", "name", "scientific name", "type", "parent species id", "family",
           "group", "authority", "rarity", "status", "obscurity", "link")
BAT_FAMILY_MARKERS = ("Vespertilionidae", "Rhinolophidae", "Miniopteridae")


class ImportFormatError(ValueError):
    pass


def clean(value):
    value = (value or "").strip()
    return value or None


def integer(value, field, line):
    value = clean(value)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise ImportFormatError(f"Line {line}: {field} must be an integer") from None


def is_bat(row):
    family = row.get("family", "")
    return any(marker in family for marker in BAT_FAMILY_MARKERS) or family.strip() == "Vleermuizen"


def read_rows(path, *, only_bats=False):
    try:
        handle = Path(path).open(encoding="utf-8-sig", newline="")
    except (OSError, UnicodeError) as error:
        raise ImportFormatError(f"Cannot read CSV: {error}") from None
    with handle:
        reader = csv.DictReader(handle)
        if tuple(reader.fieldnames or ()) != HEADERS:
            raise ImportFormatError("Unexpected CSV columns; expected: " + ",".join(HEADERS))
        rows = []
        for line, row in enumerate(reader, 2):
            if only_bats and not is_bat(row):
                continue
            name = clean(row["scientific name"])
            source_id = integer(row["id"], "id", line)
            if name is None or source_id is None:
                raise ImportFormatError(f"Line {line}: id and scientific name are required")
            rows.append((line, row, name, source_id))
    return rows


def import_csv(engine, path, *, domain, source="waarneming.nl", only_bats=False, now=None):
    if domain not in ("bird", "bat") or (only_bats and domain != "bat"):
        raise ValueError("Use domain bird/bat; only_bats requires bat")
    rows = read_rows(path, only_bats=only_bats)
    identities, source_ids, rejected = set(), set(), []
    for line, _, name, source_id in rows:
        if (domain, name) in identities or (source, source_id) in source_ids:
            rejected.append({"line": line, "scientific_name": name, "reason": "duplicate identity or source id"})
        identities.add((domain, name)); source_ids.add((source, source_id))
    if rejected:
        return {"inserted": 0, "updated": 0, "unchanged": 0, "rejected": len(rejected), "errors": rejected}
    stamp = now or datetime.now(timezone.utc)
    counts = {"inserted": 0, "updated": 0, "unchanged": 0, "rejected": 0, "errors": []}
    with Session(engine) as session:
        session.execute(text("BEGIN IMMEDIATE"))
        for line, row, name, source_id in rows:
            existing = session.scalar(select(Species).where(Species.domain == domain, Species.scientific_name == name))
            owner = session.scalar(select(Species).where(Species.source == source, Species.source_species_id == source_id))
            if owner is not None and owner is not existing:
                counts["rejected"] += 1
                counts["errors"].append({"line": line, "scientific_name": name, "reason": "source id belongs to another species"})
                continue
            values = {"common_name_nl": clean(row["name"]), "authority": clean(row["authority"]),
                      "family": clean(row["family"]), "taxon_type": clean(row["type"]),
                      "taxon_group": clean(row["group"]),
                      "parent_source_species_id": integer(row["parent species id"], "parent species id", line),
                      "source": source, "source_species_id": source_id, "source_url": clean(row["link"]),
                      "rarity": clean(row["rarity"]), "status": clean(row["status"]),
                      "obscurity": clean(row["obscurity"]), "source_metadata": dict(row)}
            if existing is None:
                session.add(Species(domain=domain, scientific_name=name, imported_at=stamp, updated_at=stamp, **values)); counts["inserted"] += 1
            elif all(getattr(existing, key) == value for key, value in values.items()):
                counts["unchanged"] += 1
            else:
                for key, value in values.items(): setattr(existing, key, value)
                existing.updated_at = stamp; counts["updated"] += 1
        session.commit()
    return counts
