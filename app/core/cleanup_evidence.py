"""Bounded evidence cleanup; new species/legacy retention requires explicit opt-in."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
from sqlalchemy import create_engine
from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.modules.observations.service import cleanup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-batches", type=int, default=1)
    args = parser.parse_args(argv)
    if not 1 <= args.limit <= 1000 or not 1 <= args.max_batches <= 1000:
        parser.error("limit/max-batches must be in 1..1000")
    if args.env_file:
        from operations.environment import read_environment
        os.environ.update(read_environment(args.env_file))
    settings = Settings()
    if not settings.resolved_database_path.is_file():
        raise SystemExit("Database missing; cleanup will not create it")
    if args.apply:
        engine = create_database(settings)
    else:
        uri = settings.resolved_database_path.resolve().as_uri() + "?mode=ro"
        engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(uri, uri=True))
    try:
        if args.apply:
            initialize_database(engine)
        for _ in range(args.max_batches if args.apply else 1):
            report = cleanup(engine, settings, apply=args.apply, limit=args.limit)
            print(json.dumps(report, indent=2), flush=True)
            if not isinstance(report, dict) or not report.get("actions") or report.get("unlink_failures"):
                break
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
