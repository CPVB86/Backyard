"""Browser-facing AvianVisitors API; internal reads stay in Python services."""
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request

from app.modules.avian_visitors import service

router = APIRouter(prefix="/api/avian-visitors", tags=["avian-visitors"])


@router.get("/search")
def search(request: Request,
           q: Annotated[str, Query(min_length=1, max_length=100)],
           limit: Annotated[int, Query(ge=1, le=8)] = 8):
    return {"query": q, "results": service.search(request.app.state.engine, q, limit)}


@router.get("/detail/{scientific_name:path}")
def detail(request: Request,
           scientific_name: Annotated[str, Path(min_length=1, max_length=255)],
           locale: Literal["nl", "en", "de"] = "nl",
           identity: Annotated[str | None, Query(max_length=64)] = None):
    result = service.detail(request.app.state.engine, request.app.state.settings,
                            request.app.state.generator, scientific_name, locale, identity)
    if result is None:
        raise HTTPException(404, "Species not found")
    return result


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
