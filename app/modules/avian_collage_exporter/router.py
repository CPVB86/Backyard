"""Authenticated export settings; never trigger rendering or Samsung upload."""
from fastapi import APIRouter, Request
from .settings import ExportSettings, read_settings, write_settings

router = APIRouter(prefix="/api/avian-collage", tags=["avian-collage"])


@router.get("/settings", response_model=ExportSettings)
def get_settings(request: Request):
    return read_settings(request.app.state.settings)


@router.post("/settings", response_model=ExportSettings)
def set_settings(value: ExportSettings, request: Request):
    return write_settings(request.app.state.settings, value)
