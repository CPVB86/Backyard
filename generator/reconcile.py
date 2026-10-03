"""Explicit reconciliation of existing accepted species; schedules, never generates."""
import argparse
import json
import os
from pathlib import Path
import sqlite3

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
from app.modules.observations.models import Observation
from generator.scheduler import Scheduler
from generator.store import AssetStore
from operations.environment import read_environment


def reconcile(engine, scheduler, *, domain=None, scientific_name=None):
    """Read unique accepted species and pass missing assets to the existing queue.

    Never start/recover a worker here: a live API worker may already be generating.
    No retry_failed, observation writes, provider calls or queue resets.
    """
    if scientific_name is not None and domain is None:
        raise ValueError("A scientific name requires a domain")
    if domain is not None:
        scheduler.store.adapter(domain)
    if scientific_name is not None:
        scientific_name = scientific_name.strip()
        if not scientific_name:
            raise ValueError("Scientific name cannot be empty")
    statement = select(
        Observation.domain, Observation.scientific_name,
        func.min(Observation.common_name).label("common_name"),
        func.min(Observation.status).label("status"),
    ).where(Observation.status.in_(("auto_accepted", "human_confirmed")))
    if domain is not None:
        statement = statement.where(Observation.domain == domain)
    if scientific_name is not None:
        statement = statement.where(Observation.scientific_name == scientific_name)
    statement = statement.group_by(Observation.domain, Observation.scientific_name).order_by(
        Observation.domain, Observation.scientific_name)
    with engine.connect() as connection:
        species = connection.execute(statement).mappings().all()
    if scientific_name is not None and not species:
        raise ValueError("No existing accepted observation for this domain/species")
    scheduler.jobs.initialize()
    report = []
    for row in species:
        current = scheduler.store.lookup(row["domain"], row["scientific_name"])
        if current["status"] == "ready":
            action = "already_ready"
        else:
            if not scheduler.accepted(row):
                raise RuntimeError("Generator scheduling failed; observations unchanged")
            action = "reconciled"
            current = scheduler.store.lookup(row["domain"], row["scientific_name"])
        report.append({"domain": row["domain"], "scientific_name": row["scientific_name"],
                       "action": action, "generation": current["generation"]})
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--scientific-name", help="Only this existing accepted species; requires --domain")
    scope.add_argument("--all", action="store_true", help="Explicitly reconcile all accepted species")
    parser.add_argument("--domain", help="Domain identity; optional filter for --all")
    parser.add_argument("--environment-file", type=Path)
    args = parser.parse_args(argv)
    if args.scientific_name and not args.domain:
        parser.error("--scientific-name requires --domain")
    engine = None
    try:
        if args.environment_file:
            os.environ.update(read_environment(args.environment_file))
        settings = Settings()
        path = settings.resolved_database_path.resolve()
        if not path.is_file():
            raise ValueError("Existing observation database not found")
        # SQLite enforces read-only access, including protection against accidental
        # creation or migration of the observation database by a management command.
        uri = path.as_uri() + "?mode=ro"
        engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(uri, uri=True),
                               hide_parameters=True)
        scheduler = Scheduler(AssetStore(settings.resolved_storage_root / "generator"))
        result = reconcile(engine, scheduler, domain=args.domain, scientific_name=args.scientific_name)
        print(json.dumps({"species": result}, ensure_ascii=True, indent=2))
    except (OSError, ValueError, RuntimeError, SQLAlchemyError, sqlite3.Error):
        # Do not echo credentials, SQL parameters or configuration values.
        parser.exit(1, "Reconciliation failed: check database/configuration and accepted species; "
                       "observations unchanged.\n")
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    main()
