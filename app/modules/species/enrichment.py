"""Strict offline enrichment of the existing, exact 1647-identity catalog."""
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from sqlalchemy import select, update
from app.core.migrations import SCHEMA_VERSION
from app.modules.species.models import Species

EXPECTED = {"bird": 1610, "bat": 37}
MATCH_STATUSES = ("exact", "probable", "ambiguous", "not_found")
FIELDS = ("wikipedia_nl_url", "wikipedia_title_nl", "wikipedia_match_status",
          "wikipedia_match_note", "wikipedia_en_url", "wikipedia_title_en",
          "summary_nl", "fact_nl", "encyclopedia_source")
REQUIRED = ("domain", "scientific_name", *FIELDS)


def new_report():
    return {"csv_records": 0, "birds": 0, "bats": 0, "unique_identities": 0,
            "database_records": 0, "database_matches": 0, "missing_database_records": 0,
            "duplicate_identities": 0, "duplicate_database_identities": 0,
            "unmatched_csv_records": 0, "unexpected_database_identities": 0,
            "would_update": 0, "updated": 0, "unchanged": 0,
            "wikipedia_nl": dict.fromkeys(MATCH_STATUSES, 0),
            "summary_populated": 0, "fact_populated": 0, "wikipedia_nl_links": 0,
            "wikipedia_en_fallback_populated": 0, "encyclopedia_source_populated": 0,
            "safe_to_apply": False, "applied": False, "errors": []}


def read_enrichment(path, report):
    rows = []
    try:
        with Path(path).open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            headers = reader.fieldnames or []
            missing = sorted(set(REQUIRED) - set(headers))
            duplicates = sorted(key for key, count in Counter(headers).items() if count > 1)
            if missing or duplicates:
                report["errors"].append({"reason": "invalid_columns", "missing": missing, "duplicates": duplicates})
                return []
            for row in reader:
                if None in row or any(value is None for value in row.values()):
                    report["errors"].append({"line": reader.line_num, "reason": "malformed_csv_row"})
                    continue
                identity = {"domain": row["domain"], "scientific_name": row["scientific_name"]}
                if row["domain"] not in EXPECTED:
                    report["errors"].append({**identity, "line": reader.line_num, "reason": "invalid_domain"})
                name = row["scientific_name"]
                if not name.strip() or name != name.strip() or len(name) > 255:
                    report["errors"].append({**identity, "line": reader.line_num, "reason": "invalid_scientific_name"})
                values = {field: row[field] if row[field].strip() else None for field in FIELDS}
                if values["wikipedia_match_status"] not in MATCH_STATUSES:
                    report["errors"].append({**identity, "line": reader.line_num, "reason": "invalid_match_status",
                                              "value": values["wikipedia_match_status"]})
                for field in FIELDS:
                    value = values[field]
                    length = getattr(Species.__table__.c[field].type, "length", None)
                    if value and ("\x00" in value or (length and len(value) > length)):
                        report["errors"].append({**identity, "line": reader.line_num, "field": field,
                                                  "reason": "invalid_field_length_or_nul"})
                for locale in ("nl", "en"):
                    value = values[f"wikipedia_{locale}_url"]
                    if value:
                        parsed = urlsplit(value)
                        if (parsed.scheme != "https" or parsed.netloc != f"{locale}.wikipedia.org"
                                or not parsed.path.startswith("/wiki/") or parsed.path == "/wiki/"):
                            report["errors"].append({**identity, "line": reader.line_num,
                                                      "reason": "invalid_wikipedia_url", "locale": locale})
                rows.append({**identity, "values": values})
    except (OSError, UnicodeError, csv.Error, ValueError) as error:
        report["errors"].append({"reason": "cannot_parse_csv", "detail": str(error)})
    counts = Counter(row["domain"] for row in rows)
    identities = Counter((row["domain"], row["scientific_name"]) for row in rows)
    report.update(csv_records=len(rows), birds=counts["bird"], bats=counts["bat"],
                  unique_identities=len(identities), duplicate_identities=sum(n - 1 for n in identities.values()))
    if len(rows) != sum(EXPECTED.values()):
        report["errors"].append({"reason": "wrong_total", "expected": 1647, "actual": len(rows)})
    for domain, expected in EXPECTED.items():
        if counts[domain] != expected:
            report["errors"].append({"reason": "wrong_domain_count", "domain": domain,
                                      "expected": expected, "actual": counts[domain]})
    for (domain, name), count in sorted(identities.items()):
        if count > 1:
            report["errors"].append({"reason": "duplicate_csv_identity", "domain": domain,
                                      "scientific_name": name, "count": count})
    statuses = Counter(row["values"]["wikipedia_match_status"] for row in rows)
    report["wikipedia_nl"] = {key: statuses[key] for key in MATCH_STATUSES}
    for result, field in (("summary_populated", "summary_nl"), ("fact_populated", "fact_nl"),
                          ("wikipedia_nl_links", "wikipedia_nl_url"),
                          ("wikipedia_en_fallback_populated", "wikipedia_en_url"),
                          ("encyclopedia_source_populated", "encyclopedia_source")):
        report[result] = sum(row["values"][field] is not None for row in rows)
    return rows


def import_enrichment(engine, path, *, apply=False, now=None):
    report = new_report()
    rows = read_enrichment(path, report)
    if report["errors"]:
        return report
    stamp = now or datetime.now(timezone.utc)
    table = Species.__table__
    with engine.connect() as connection:
        # Lock writers BEFORE matching/diffing when applying: no preflight/write race.
        # Dry-run never calls initialize_database, migrate, INSERT or UPDATE.
        connection.exec_driver_sql("BEGIN IMMEDIATE" if apply else "BEGIN")
        try:
            if connection.exec_driver_sql("PRAGMA user_version").scalar_one() != SCHEMA_VERSION:
                report["errors"].append({"reason": "migration_required", "expected_schema": SCHEMA_VERSION})
                return report
            existing = connection.execute(select(table)).mappings().all()
            indexed = defaultdict(list)
            for record in existing:
                indexed[(record["domain"], record["scientific_name"])].append(record)
            report["database_records"] = len(existing)
            if len(existing) != 1647:
                report["errors"].append({"reason": "wrong_database_total", "expected": 1647, "actual": len(existing)})
            wanted = {(row["domain"], row["scientific_name"]) for row in rows}
            for (domain, name), matches in indexed.items():
                if len(matches) > 1:
                    report["duplicate_database_identities"] += 1
                    report["errors"].append({"reason": "duplicate_database_identity", "domain": domain,
                                              "scientific_name": name, "ids": [r["id"] for r in matches]})
                if (domain, name) not in wanted:
                    report["unexpected_database_identities"] += 1
                    report["errors"].append({"reason": "unexpected_database_identity", "domain": domain,
                                              "scientific_name": name})
            changes = []
            for row in rows:
                matches = indexed[(row["domain"], row["scientific_name"])]
                if len(matches) != 1:
                    report["unmatched_csv_records"] += 1
                    if not matches:
                        report["missing_database_records"] += 1
                        report["errors"].append({"reason": "missing_database_identity", "domain": row["domain"],
                                                  "scientific_name": row["scientific_name"]})
                    continue
                report["database_matches"] += 1
                record = matches[0]
                changed = {field: value for field, value in row["values"].items() if record[field] != value}
                if changed:
                    changes.append((record["id"], changed))
                else:
                    report["unchanged"] += 1
            report["would_update"] = len(changes)
            report["safe_to_apply"] = not report["errors"] and report["database_matches"] == 1647
            if not report["safe_to_apply"] or not apply:
                return report
            for identity, changed in changes:
                result = connection.execute(update(table).where(table.c.id == identity).values(
                    **changed, updated_at=stamp))
                if result.rowcount != 1:
                    raise RuntimeError("Catalog changed during enrichment; full rollback required")
            connection.commit()
            report["updated"] = len(changes)
            report["applied"] = True
            return report
        finally:
            # Rolls back all updates on any error, and ends read-only dry-run snapshots.
            if connection.in_transaction():
                connection.rollback()
