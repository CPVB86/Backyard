"""Run with the API stopped: python -m app.core.migrate."""
from app.core.config import Settings
from app.core.database import create_database
from app.core.migrations import migrate, SCHEMA_VERSION


def main():
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
