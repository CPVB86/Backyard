"""Authenticated, species-scoped Generator API, independent of observations."""
from typing import Annotated
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from generator.store import GenerationConflict, UnsupportedDomain, public_status

router = APIRouter(prefix="/api/generator", tags=["generator"])


def invoke(action):
    try:
        return action()
    except UnsupportedDomain as error:
        raise HTTPException(501, str(error)) from None
    except GenerationConflict as error:
        raise HTTPException(409, str(error)) from None
    except ValueError:
        raise HTTPException(422, "Invalid generator request, asset or metadata") from None
    except RuntimeError:
        raise HTTPException(502, "Generation failed; inspect attempt status and provider usage before retrying") from None


@router.get("")
def domains(request: Request):
    return {domain: {"configured":adapter is not None, "required_assets":list(adapter.required_assets) if adapter else []}
            for domain,adapter in request.app.state.generator.adapters.items()}


@router.get("/{domain}/species")
def species(request: Request, domain: str,
            scientific_name: Annotated[str, Query(min_length=1, max_length=255)]):
    return invoke(lambda: public_status(request.app.state.generator.lookup(domain, scientific_name)))


@router.post("/{domain}/generate", include_in_schema=False)
def generate(domain: str):
    raise HTTPException(405, "Generation is scheduled by accepted observations; consumers use GET")


@router.get("/{domain}/assets/{key}/{asset_id}")
def asset(request: Request, domain: str, key: str, asset_id: str):
    result = invoke(lambda: request.app.state.generator.resolve_key(domain, key))
    item = result["assets"].get(asset_id) if result else None
    if item is None:
        raise HTTPException(404, "Species asset unavailable")
    return FileResponse(item["path"], media_type=item["content_type"],
                        headers={"Cache-Control":"private, max-age=3600", "X-Content-Type-Options":"nosniff"})
