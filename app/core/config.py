from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BACKYARD_", env_file=ROOT / ".env",
        env_file_encoding="utf-8", extra="forbid",
    )
    database_path: Path = Path("data/backyard.sqlite3")
    storage_root: Path = Path("data")
    max_audio_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    @property
    def resolved_database_path(self) -> Path:
        path = self.database_path.expanduser()
        return path if path.is_absolute() else ROOT / path

    @property
    def resolved_storage_root(self) -> Path:
        path = self.storage_root.expanduser()
        return (path if path.is_absolute() else ROOT / path).resolve()
