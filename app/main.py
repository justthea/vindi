"""HTTP API and static UI.

    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from .config import ROOT, get_settings
from .llm import write_draft
from .models import BriefRequest, DraftRequest, Identity
from .pipeline import load_cache, scan, verify_quote
from .youcom import YouClient

settings = get_settings()
you = YouClient(settings)

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "operator": {"type": "string", "description": "Company or person that runs the site"},
        "what_they_sell": {"type": "string"},
        "size": {"type": "string", "description": "Rough size: followers, employees, revenue or traffic"},
        "location": {"type": "string"},
        "contact": {"type": "string", "description": "Business contact email or contact page URL"},
        "creator_partnerships": {"type": "string", "description": "Known deals with creators or influencers"},
    },
    "required": ["operator", "what_they_sell", "size", "location", "contact", "creator_partnerships"],
    "additionalProperties": False,  # You.com output_schema requires this on every object
}


def _sse(event: dict) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


def _bad_request(exc: Exception) -> JSONResponse:
    return JSONResponse({"error": str(exc)[:500]}, status_code=400)


async def index(request: Request):
    return FileResponse(ROOT / "web" / "index.html")


async def health(request: Request):
    return JSONResponse(
        {
            "ready": settings.ready,
            "missing": settings.missing(),
            "mock": settings.mock,
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "extraction_mode": settings.extraction_mode,
        }
    )


async def scan_endpoint(request: Request):
    try:
        identity = Identity.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _bad_request(exc)
    if not settings.ready:
        return JSONResponse({"error": "Missing settings: " + ", ".join(settings.missing())}, status_code=503)

    async def stream():
        try:
            async for event in scan(identity, settings, you):
                yield _sse(event)
        except Exception as exc:  # tell the UI instead of dropping the connection
            yield _sse({"event": "error", "data": {"message": str(exc)[:300]}})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def cached(request: Request):
    data = load_cache()
    return JSONResponse(data) if data else JSONResponse({"error": "No saved scan yet"}, status_code=404)


async def brief(request: Request):
    """Seller background from the You.com Research API."""
    try:
        req = BriefRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _bad_request(exc)
    question = (
        f"Who operates the website {req.domain}? The page in question is {req.url}"
        + (f' titled "{req.title}"' if req.title else "")
        + ". Report: the company or person operating it, what they sell, their approximate size, "
        "where they are based, a business contact email or contact page, and any known partnerships "
        'with creators or influencers. Write "unknown" for anything you cannot find. Do not guess.'
    )
    try:
        return JSONResponse(await you.research(question, schema=BRIEF_SCHEMA, domain=req.domain))
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:300]}, status_code=502)


async def draft(request: Request):
    """Re-reads the page with the Contents API, then writes the email."""
    try:
        req = DraftRequest.model_validate(await request.json())
    except (ValidationError, ValueError) as exc:
        return _bad_request(exc)

    fresh = await you.contents(req.hit.url)
    evidence = {
        "url": req.hit.url,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "refetched": fresh is not None,
        # None means we could not re-read the page, which is different from "gone".
        "quote_still_present": verify_quote(req.hit.evidence_quote, fresh) if fresh else None,
        # Quotes often come from the page's meta description, which the page body omits,
        # so also report whether the matched name/handle is still on the live page.
        "name_still_present": verify_quote(req.hit.matched_identifier or req.identity.name, fresh) if fresh else None,
    }
    try:
        email = await write_draft(req.identity, req.hit, req.mode, req.brief, settings)
    except Exception as exc:
        return JSONResponse({"error": str(exc)[:300], "evidence": evidence}, status_code=502)
    return JSONResponse({**email, "evidence": evidence})


@asynccontextmanager
async def lifespan(app):
    yield
    await you.aclose()


app = Starlette(
    lifespan=lifespan,
    routes=[
        Route("/", index),
        Route("/api/health", health),
        Route("/api/scan", scan_endpoint, methods=["POST"]),
        Route("/api/cached", cached),
        Route("/api/brief", brief, methods=["POST"]),
        Route("/api/draft", draft, methods=["POST"]),
    ],
)
