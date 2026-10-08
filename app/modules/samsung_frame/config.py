"""Project .env is the default for manual commands and services; explicit overrides remain supported."""
import os
from pathlib import Path
from app.core.config import ROOT, Settings
from operations.environment import read_environment


def resolve_path(path: Path) -> Path:
    path = path.expanduser()
    return path if path.is_absolute() else ROOT / path


def load_settings(environment_file: Path | None = None) -> Settings:
    # Explicit shell environment > production file > existing project .env > defaults.
    values = read_environment(environment_file) if environment_file is not None else {}
    overrides = {}
    for field in Settings.model_fields:
        key = "BACKYARD_" + field.upper()
        if key in values and key not in os.environ:
            overrides[field] = values[key]
    return Settings(**overrides)
