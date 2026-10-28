"""End to end through the Function's HTTP handler in dev mode (local corpus,
scripted model), checking the wire format the browser actually reads."""
from __future__ import annotations

import importlib
import json

import pytest
from starlette.requests import Request

from .conftest import CORPUS, ScriptedProvider


@pytest.fixture
def fn(monkeypatch):
    monkeypatch.setenv("GW_DEV_MODE", "1")
    monkeypatch.setenv("GW_SOURCE", "local")
    monkeypatch.setenv("GW_LOCAL_ROOT", str(CORPUS))
    from groundwork import providers
    monkeypatch.setattr(providers, "provider_from_settings", lambda s: ScriptedProvider("Within 30 days [1]."))
    import function_app
    module = importlib.reload(function_app)
    module.runtime.cache_clear()
    return module


def request(body: dict, headers: dict | None = None) -> Request:
    raw = json.dumps(body).encode()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": raw, "more_body": False}

    hdrs = [(b"content-type", b"application/json")] + [(k.encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "POST", "path": "/api/ask", "headers": hdrs, "query_string": b""}, receive)


async def read_sse(response) -> list[tuple[str, dict]]:
    text = "".join([chunk async for chunk in response.body_iterator])
    out = []
    for block in text.strip().split("\n\n"):
        name, data = block.split("\n")
        out.append((name[len("event: "):], json.loads(data[len("data: "):])))
    return out


async def test_ask_returns_event_stream(fn):
    response = await fn.ask(request({"question": "When is a travel claim due?"}))
    assert response.media_type == "text/event-stream"
    assert response.headers["cache-control"].startswith("no-cache")
    events = await read_sse(response)
    assert events[-1][0] == "done"
    assert dict(events)["citations"][0]["title"] == "travel-and-expenses.md"


async def test_rejects_bad_input(fn):
    response = await fn.ask(request({"question": "x"}))
    assert response.status_code == 400


def test_dev_mode_refuses_graph_source(fn, monkeypatch):
    monkeypatch.setenv("GW_SOURCE", "graph")
    fn.runtime.cache_clear()
    with pytest.raises(RuntimeError):
        fn.runtime()
