"""Azure Functions entry point (Python v2 programming model, HTTP streaming).

  POST /api/ask        Bearer token, {"question": str, "history": [...]}  -> text/event-stream
  POST /api/feedback   Bearer token, {"request_id": str, "helpful": bool, "comment": str}
  GET  /api/health
"""
from __future__ import annotations

import json
import os
from functools import lru_cache

import azure.functions as func
import httpx
from azurefunctions.extensions.http.fastapi import JSONResponse, Request, StreamingResponse

from groundwork.auth import AuthError, OnBehalfOf, Principal, TokenValidator
from groundwork.cache import MemoryCache
from groundwork.config import Settings
from groundwork.pipeline import Assistant
from groundwork.providers import provider_from_settings
from groundwork.sources.graph import GraphError, GraphSource
from groundwork.sources.local import LocalSource
from groundwork.telemetry import event, redact

# Identity is checked in code against Entra ID, so no function key is required.
app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

DEV_MODE = os.environ.get("GW_DEV_MODE") == "1"


@lru_cache(maxsize=1)
def runtime():
    """Built on first request, then reused for the life of the instance."""
    s = Settings.from_env()
    if DEV_MODE and s.source != "local":
        raise RuntimeError("GW_DEV_MODE is only allowed with GW_SOURCE=local")
    validator = obo = None
    if not DEV_MODE:
        validator = TokenValidator(s.tenant_id, s.api_client_id, s.api_scope)
        obo = OnBehalfOf(s.tenant_id, s.api_client_id, s.api_client_secret)
    assistant = Assistant(s, provider_from_settings(s), MemoryCache())
    return s, validator, obo, assistant, httpx.AsyncClient()


def sse(name: str, data) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"


def auth_failure(e: AuthError) -> JSONResponse:
    headers = {"WWW-Authenticate": f'Bearer error="{e.code}"'} if e.status == 401 else {}
    return JSONResponse({"error": e.code, "message": str(e)}, status_code=e.status, headers=headers)


def authenticate(req: Request) -> tuple[Principal, object]:
    s, validator, obo, _, http = runtime()
    if DEV_MODE:
        return Principal("", "dev-user", "dev", "Developer"), LocalSource(s.local_root, user=req.headers.get("x-dev-user"))
    principal = validator.validate(req.headers.get("authorization"))
    token = obo.graph_token(principal)
    return principal, GraphSource(token, http, s.search_scope)


@app.route(route="ask", methods=[func.HttpMethod.POST])
async def ask(req: Request):
    try:
        principal, source = authenticate(req)
    except AuthError as e:
        return auth_failure(e)

    try:
        body = await req.json()
    except ValueError:
        return JSONResponse({"error": "bad_request", "message": "Body must be JSON"}, status_code=400)
    question = str(body.get("question", "")).strip()
    history = body.get("history") if isinstance(body.get("history"), list) else []
    if not 2 <= len(question) <= 1000:
        return JSONResponse({"error": "bad_request", "message": "question must be 2 to 1000 characters"}, status_code=400)

    _, _, _, assistant, _ = runtime()

    async def stream():
        try:
            async for name, data in assistant.ask(source, question, history, user=principal.pseudonym):
                yield sse(name, data)
        except GraphError as e:
            payload = {"code": "graph_error", "status": e.status}
            if e.status == 429:
                payload.update(code="throttled", retry_after=e.retry_after)
            yield sse("error", payload)
        except Exception as e:  # noqa: BLE001 - never leak internals to the browser
            event("groundwork.error", user=principal.pseudonym, error=type(e).__name__)
            yield sse("error", {"code": "internal_error"})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@app.route(route="feedback", methods=[func.HttpMethod.POST])
async def feedback(req: Request):
    try:
        principal, _ = authenticate(req)
    except AuthError as e:
        return auth_failure(e)
    body = await req.json()
    event(
        "groundwork.feedback",
        user=principal.pseudonym,
        request_id=str(body.get("request_id", ""))[:32],
        helpful=bool(body.get("helpful")),
        comment=redact(str(body.get("comment", "")), limit=500),
    )
    return JSONResponse({"ok": True}, status_code=202)


@app.route(route="health", methods=[func.HttpMethod.GET])
async def health(req: Request):
    return JSONResponse({"status": "ok", "version": __import__("groundwork").__version__})
