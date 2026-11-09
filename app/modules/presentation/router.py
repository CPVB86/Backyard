from typing import Literal
from zoneinfo import ZoneInfoNotFoundError
from fastapi import APIRouter, Request, HTTPException, Query
from app.core.monitoring import monitoring_started_at
from .service import snapshot, parse_timezone

router = APIRouter(prefix="/api/presentation", tags=["presentation"])


@router.get("/{module}")
def presentation(request: Request, module: Literal["birds", "bats"],
                 period: Literal["today", "24h", "7d", "30d", "all"] = "all",
                 timezone: str = Query(default="Europe/Amsterdam", max_length=100),
                 identity: Literal["otje"] | None = None):
    if identity is not None and module != "birds":
        raise HTTPException(422, "Identity is only available for birds")
    try:
        parse_timezone(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Unknown timezone") from None
    result = snapshot(request.app.state.engine, module, period, timezone, identity_filter=identity)
    result["monitoring_started_at"] = monitoring_started_at(request.app.state.settings)
    return result
