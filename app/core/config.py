from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BACKYARD_", env_file=ROOT / ".env",
        env_file_encoding="utf-8", extra="forbid",
    )
    api_token: SecretStr = SecretStr("")
    policy_path: Path | None = None
    database_path: Path = Path("data/backyard.sqlite3")
    storage_root: Path = Path("data")
    max_audio_bytes: int = Field(default=8 * 1024 * 1024, ge=1024, le=64 * 1024 * 1024)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"

    # Optional manual Samsung CLI; no API/ML runtime imports.
    samsung_frame_host: str = ""
    samsung_frame_token: SecretStr | None = None
    samsung_frame_token_path: Path = Path("data/samsung_frame/token")
    samsung_frame_state_path: Path = Path("data/samsung_frame/artwork.json")
    samsung_frame_image_path: Path = Path("data/samsung_frame/samsung-frame.png")
    samsung_frame_timeout: float = Field(default=60, ge=1, le=300)

    @property
    def resolved_database_path(self) -> Path:
        path = self.database_path.expanduser()
        return path if path.is_absolute() else ROOT / path

    @property
    def resolved_storage_root(self) -> Path:
        path = self.storage_root.expanduser()
        return (path if path.is_absolute() else ROOT / path).resolve()
