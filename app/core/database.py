from datetime import timezone
from sqlalchemy import DateTime, URL, create_engine, event
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator
from app.core.config import Settings


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator):
    """Store naive UTC; return aware UTC consistently across database engines."""
    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Timestamp must include a timezone")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        return value.replace(tzinfo=timezone.utc) if value is not None else None


def create_database(settings: Settings):
    path = settings.resolved_database_path
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        URL.create("sqlite+pysqlite", database=str(path)),
        connect_args={"check_same_thread": False, "timeout": 5},
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def configure_sqlite(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def initialize_database(engine):
    from app.modules.birds import models  # noqa: F401
    Base.metadata.create_all(engine)
