"""Regression tests for the bearer-token gate (``AuthMiddleware``).

Pins the auth behavior (right token passes via header OR ``?token=`` query
param, wrong/missing token 401s, no-op when DEVCLAW_TOKEN unset, /health stays
open) and — the hardening this file was added for — that both token
comparisons are timing-safe (``hmac.compare_digest``), so response timing
can't be used to recover the token byte-by-byte."""

from __future__ import annotations

import asyncio
import inspect

from devclaw.server import lifecycle


def _run(monkeypatch, token_env, *, header=b"", query=b"", path="/mcp"):
    """Drive AuthMiddleware as pure ASGI; return (reached_app, status_or_None)."""
    monkeypatch.setattr(lifecycle, "AUTH_TOKEN", token_env)
    reached = []

    async def app(scope, receive, send):
        reached.append(True)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [(b"authorization", header)] if header else [],
        "query_string": query,
    }
    asyncio.run(lifecycle.AuthMiddleware(app)(scope, receive, send))
    status = next((m["status"] for m in sent if m["type"] == "http.response.start"), None)
    return bool(reached), status


def test_auth_gate_uses_timing_safe_compare(monkeypatch):
    # Structural pin: both the header and query-param comparisons go through
    # hmac.compare_digest — a plain == is a timing-oracle regression.
    src = inspect.getsource(lifecycle.AuthMiddleware)
    assert src.count("hmac.compare_digest(") == 2
    assert " == AUTH_TOKEN" not in src and '== f"Bearer' not in src

    # Behavioral pin: the gate still gates.
    reached, status = _run(monkeypatch, "sekrit", header=b"Bearer sekrit")
    assert reached and status == 200
    reached, status = _run(monkeypatch, "sekrit", header=b"Bearer wrong")
    assert not reached and status == 401
    reached, status = _run(monkeypatch, "sekrit", query=b"token=sekrit")
    assert reached and status == 200
    reached, status = _run(monkeypatch, "sekrit", query=b"token=wrong")
    assert not reached and status == 401


def test_auth_gate_absent_token_401s_not_crashes(monkeypatch):
    # The query-param side yields no value when ?token= is absent — the
    # timing-safe compare must see "" (compare_digest rejects None), so a
    # tokenless request is a clean 401, never a TypeError.
    reached, status = _run(monkeypatch, "sekrit")
    assert not reached and status == 401


def test_auth_gate_noop_when_token_unset_and_health_open(monkeypatch):
    reached, status = _run(monkeypatch, "")  # DEVCLAW_TOKEN unset -> no-op
    assert reached and status == 200
    reached, status = _run(monkeypatch, "sekrit", path="/health")  # health stays open
    assert reached and status == 200
