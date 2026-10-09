"""Persistent Pi-owned export period; independent of WordPress at export time."""
from typing import Literal
from pydantic import BaseModel, ConfigDict
from app.modules.samsung_frame.storage import save_state

PERIOD_HOURS = {"1h": 1, "12h": 12, "24h": 24, "7d": 168, "all": None}


class ExportSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: Literal["1h", "12h", "24h", "7d", "all"] = "24h"


def settings_path(settings):
    return settings.resolved_storage_root / "avian_export" / "settings.json"


def read_settings(settings):
    path = settings_path(settings)
    if path.exists():
        return ExportSettings.model_validate_json(path.read_text(encoding="utf-8"))
    # Retain the existing environment setting until explicitly saved through the API.
    period = next((key for key, hours in PERIOD_HOURS.items()
                   if hours == settings.avian_export_hours), "24h")
    return ExportSettings(period=period)


def export_hours(settings):
    if not settings_path(settings).exists():
        return settings.avian_export_hours
    return PERIOD_HOURS[read_settings(settings).period]


def write_settings(settings, value):
    save_state(settings_path(settings), value.model_dump())
    return value
