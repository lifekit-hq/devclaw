"""Entrypoint + serve loops + bearer-token auth middleware."""

from __future__ import annotations

import asyncio
import hmac
import sys
import urllib.parse

from starlette.responses import JSONResponse

from .. import __version__
from .. import config as _config
from ..boot_guard import assert_required_env
from ._state import AUTH_TOKEN, DB_PATH, HTTP_HOST, HTTP_PORT, SERVER_NAME, goals, mcp, queue


#: Liveness reads that carry no secret and whose readers hold no token.
OPEN_PATHS = frozenset({"/health", "/metrics"})


class AuthMiddleware:
    """Pure-ASGI bearer-token gate. No-op when DEVCLAW_TOKEN is unset; /health
    and /metrics stay open so the container healthcheck and the box's
    Prometheus scrape (the dead-man watcher) need no token."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not AUTH_TOKEN or scope.get("path") in OPEN_PATHS:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        ok = hmac.compare_digest(auth, f"Bearer {AUTH_TOKEN}")
        if not ok:
            qs = urllib.parse.parse_qs(scope.get("query_string", b"").decode())
            ok = hmac.compare_digest(qs.get("token", [""])[0] or "", AUTH_TOKEN)
        if ok:
            await self.app(scope, receive, send)
            return
        resp = JSONResponse({"error": "unauthorized"}, status_code=401, headers={"www-authenticate": "Bearer"})
        await resp(scope, receive, send)


def _start_loops() -> None:
    """The two heartbeats: the queue pump and the goal tick; a settled
    session wakes the goal tick at once."""
    queue.start_ticking()
    queue.set_on_settle(goals.poke)
    goals.start()


async def _serve_stdio() -> None:
    _start_loops()
    await mcp.run_stdio_async()


async def _serve_http() -> None:
    import uvicorn
    from starlette.middleware import Middleware

    app = mcp.http_app(path="/mcp", middleware=[Middleware(AuthMiddleware)], stateless_http=True)
    _start_loops()
    config = uvicorn.Config(app, host=HTTP_HOST, port=HTTP_PORT, log_level="warning")
    await uvicorn.Server(config).serve()


def main() -> None:
    transport = _config.transport()
    if transport not in ("stdio", "http"):
        raise SystemExit(f'Unknown DEVCLAW_TRANSPORT={transport}; expected "stdio" or "http"')
    # Fail closed at boot: production never runs without its credentials.
    assert_required_env()
    reaped = queue.recover()
    if transport == "stdio":
        sys.stderr.write(f"{SERVER_NAME} v{__version__} ready (stdio, db={DB_PATH}, recovered={reaped})\n")
        asyncio.run(_serve_stdio())
    else:
        sys.stderr.write(f"{SERVER_NAME} v{__version__} ready (http://{HTTP_HOST}:{HTTP_PORT}/mcp, "
                         f"db={DB_PATH}, recovered={reaped})\n")
        asyncio.run(_serve_http())
