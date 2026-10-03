"""Browser-facing AvianVisitors API; internal reads stay in Python services."""
from typing import Annotated, Literal

from fastapi import APIRouter, Query, Request

from app.modules.avian_visitors import service

router = APIRouter(prefix="/api/avian-visitors", tags=["avian-visitors"])


@router.get("/recent")
def recent(request: Request,
           hours: Annotated[int, Query(ge=1, le=1_000_000)] = 24,
           locale: Literal["nl", "en", "de"] = "nl"):
    return service.recent(request.app.state.engine, request.app.state.generator, hours, locale)


@router.get("/stats")
def stats(request: Request,
          hours: Annotated[int, Query(ge=1, le=1_000_000)] = 24,
          locale: Literal["nl", "en", "de"] = "nl"):
    return service.stats(request.app.state.engine, hours, locale)


@router.get("/lifelist")
def lifelist(request: Request, locale: Literal["nl", "en", "de"] = "nl"):
    return service.lifelist(request.app.state.engine, request.app.state.generator, locale)
