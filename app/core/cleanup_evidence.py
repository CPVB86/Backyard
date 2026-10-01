"""Explicit, bounded cleanup; dry run unless --apply. Never touches legacy audio."""
import argparse
import json
from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.modules.observations.service import cleanup


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    settings = Settings()
    engine = create_database(settings)
    try:
        initialize_database(engine)
        print(json.dumps(cleanup(engine, settings, apply=args.apply), indent=2))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
