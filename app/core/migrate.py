"""Run with the API stopped: python -m app.core.migrate."""
import argparse
import os
from pathlib import Path
from operations.environment import read_environment
from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import migrate, SCHEMA_VERSION


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--environment-file", type=Path)
    args = parser.parse_args(argv)
    if args.environment_file:
        os.environ.update(read_environment(args.environment_file))
    settings = Settings()
    engine = create_database(settings)
    try:
        backup = migrate(engine, settings.resolved_database_path)
        print(f"Database schema is current (version {SCHEMA_VERSION}).")
        if backup:
            print(f"Pre-migration backup: {backup}")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
