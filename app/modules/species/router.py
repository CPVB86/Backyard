from typing import Annotated, Literal
from fastapi import APIRouter, HTTPException, Path, Query, Request
from app.modules.species import service

router = APIRouter(prefix="/api/species", tags=["species"])


@router.get("/{domain}/{scientific_name:path}")
def species_detail(request: Request, domain: Literal["bird", "bat"],
                   scientific_name: Annotated[str, Path(min_length=1, max_length=255)],
                   locale: Annotated[Literal["nl", "en", "de"], Query()] = "nl"):
    try:
        result = service.detail(request.app.state.engine, request.app.state.settings,
                                request.app.state.generator, domain, scientific_name, locale)
    except ValueError:
        raise HTTPException(422, "Invalid species identity or locale") from None
    if result is None:
        raise HTTPException(404, "Species not found")
    return result
