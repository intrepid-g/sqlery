"""FastAPI adapter for the pure-core HTTP trigger (SMOD-03).

Mounts a ``POST /trigger`` route on the standalone (``sqlery-web``) FastAPI
app. The route builds a :class:`sqlery.core.triggers.TriggerEnvelope` from
the incoming FastAPI :class:`Request`, calls
:func:`sqlery.core.triggers.handle`, and maps the :class:`TriggerResult`
back to a :class:`fastapi.responses.JSONResponse`.

This file is **deliberately thin** — all signature verification,
idempotency caching, and dispatch logic lives in
:mod:`sqlery.core.triggers`. The FastAPI module's only job is the
request/response translation.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from sqlery.core.triggers import TriggerEnvelope, handle

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/trigger")
async def trigger_endpoint(request: Request) -> JSONResponse:
    """Receive an HTTP trigger envelope and dispatch to core.triggers.handle."""
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}

    payload: dict = {}
    if body:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(f"Invalid trigger payload: {e}")
            return JSONResponse({"error": "invalid JSON payload"}, status_code=400)

    envelope = TriggerEnvelope(body=body, headers=headers, payload=payload)
    result = handle(envelope)
    return JSONResponse(result.body, status_code=result.status_code)


__all__ = ["router"]
