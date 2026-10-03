"""Explicit offline encyclopedia import. Default is a write-free preflight."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from app.core.config import Settings
from app.modules.species.enrichment import import_enrichment, new_report
from operations.environment import read_environment


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply after all preflight checks pass")
    mode.add_argument("--dry-run", action="store_true", help="Default: validate, match and report only")
    parser.add_argument("--environment-file", type=Path)
    args = parser.parse_args(argv)
    engine = None
    try:
        if args.environment_file:
            os.environ.update(read_environment(args.environment_file))
        path = Settings().resolved_database_path.resolve()
        # mode=ro prevents accidental dry-run writes; mode=rw refuses to create a DB.
        uri = path.as_uri() + ("?mode=rw" if args.apply else "?mode=ro")
        engine = create_engine("sqlite+pysqlite://", creator=lambda: sqlite3.connect(uri, uri=True),
                               hide_parameters=True)
        report = import_enrichment(engine, args.file, apply=args.apply)
    except (OSError, ValueError, RuntimeError, SQLAlchemyError, sqlite3.Error):
        report = new_report()
        report["errors"].append({"reason": "import_failed_rolled_back",
                                  "detail": "Check file, database, schema and permissions; no partial apply committed"})
    finally:
        if engine is not None:
            engine.dispose()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("SAFE TO APPLY: " + ("yes" if report["safe_to_apply"] else "no"))
    if not report["safe_to_apply"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
