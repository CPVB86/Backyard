from pathlib import Path
from typing import Literal
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BACKYARD_", env_file=ROOT / ".env",
        env_file_encoding="utf-8", extra="forbid",
    )
    database_path: Path = Path("data/backyard.sqlite3")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @property
    def resolved_database_path(self) -> Path:
        path = self.database_path.expanduser()
        return path if path.is_absolute() else ROOT / path
