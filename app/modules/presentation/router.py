from typing import Literal
from zoneinfo import ZoneInfoNotFoundError
from fastapi import APIRouter, Request, HTTPException, Query
from .service import snapshot, parse_timezone

router = APIRouter(prefix="/api/presentation", tags=["presentation"])


@router.get("/{module}")
def presentation(request: Request, module: Literal["birds", "bats"],
                 period: Literal["today", "24h", "7d", "30d", "all"] = "all",
                 timezone: str = Query(default="Europe/Amsterdam", max_length=100)):
    try:
        parse_timezone(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(422, "Unknown timezone") from None
    return snapshot(request.app.state.engine, module, period, timezone)
