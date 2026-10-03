"""Usage: python -m app.modules.species.import_species --domain bird --file export.csv"""
import argparse
import json
from app.core.config import Settings
from app.core.database import create_database, initialize_database
from app.modules.species.importer import ImportFormatError, import_csv


def main():
    parser = argparse.ArgumentParser(description="Import a Waarneming.nl species CSV into Backyard")
    parser.add_argument("--domain", choices=("bird", "bat"), required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--only-bats", action="store_true",
                        help="Select bat taxa from the Waarneming.nl mammals export")
    args = parser.parse_args()
    engine = create_database(Settings())
    try:
        initialize_database(engine)
        report = import_csv(engine, args.file, domain=args.domain, only_bats=args.only_bats)
        print(json.dumps(report, ensure_ascii=False))
        if report["rejected"]:
            raise SystemExit(2)
    except ImportFormatError as error:
        parser.error(str(error))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
