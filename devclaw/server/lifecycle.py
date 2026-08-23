"""Entrypoint + serve loops + bearer-token auth middleware."""

from __future__ import annotations

import asyncio
import os
import sys
import urllib.parse

from starlette.responses import JSONResponse

from .. import __version__
from .. import intake as intake_mod
from ..claude_trust import config_path_for, ensure_trusted_in_place
from ._state import (
    AUTH_TOKEN,
    DB_PATH,
    HTTP_HOST,
    HTTP_PORT,
    SERVER_NAME,
    goals,
    mcp,
    queue,
    registry,
)


class AuthMiddleware:
    """Pure-ASGI bearer-token gate. No-op when DEVCLAW_TOKEN is unset. /health
    stays open so container health checks don't need the token."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not AUTH_TOKEN or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        auth = headers.get(b"authorization", b"").decode()
        ok = auth == f"Bearer {AUTH_TOKEN}"
        if not ok:
            qs = urllib.parse.parse_qs(scope.get("query_string", b"").decode())
            ok = qs.get("token", [None])[0] == AUTH_TOKEN
        if ok:
            await self.app(scope, receive, send)
            return
        resp = JSONResponse(
            {"error": "unauthorized"}, status_code=401, headers={"www-authenticate": "Bearer"}
        )
        await resp(scope, receive, send)


def _start_loops() -> None:
    """Start the two heartbeats: the task queue (resumes work + advances DAGs) and
    the goal layer (drives durable goals). Wire the queue's on-settle hook to the
    goal heartbeat so a finished task triggers an immediate goal tick in-process."""
    queue.start_ticking()
    queue.set_on_settle(goals.poke)
    goals.start()


_RECOVERY_TASKS: set = set()


def _kick_readiness_recovery() -> None:
    """Spec 006 P2 hardening: on serve-start, reconcile any intake issue left
    without a readiness label by a prior crash — re-grade the pending set derived
    from GitHub (SC-001). Kicked as a background task so it never blocks serving;
    runs ONCE at startup, NOT on the heartbeat tick (the zero-token idle guard
    stays intact), and spends zero cognition when nothing is pending."""

    async def _run() -> None:
        try:
            n = await intake_mod.recover_pending_grades(registry)
            if n:
                sys.stderr.write(
                    f"{SERVER_NAME}: readiness recovery graded {n} pending intake issue(s)\n"
                )
        except Exception as exc:  # noqa: BLE001 — best-effort startup sweep
            sys.stderr.write(f"{SERVER_NAME}: readiness recovery sweep failed: {exc}\n")

    try:
        task = asyncio.create_task(_run())
    except RuntimeError:
        return  # no running loop — skip rather than crash serve; manual regrade remains
    _RECOVERY_TASKS.add(task)
    task.add_done_callback(_RECOVERY_TASKS.discard)


async def _serve_stdio() -> None:
    _start_loops()
    _kick_readiness_recovery()
    await mcp.run_stdio_async()


async def _serve_http() -> None:
    import uvicorn
    from starlette.middleware import Middleware

    # stateless_http=True: some MCP clients (e.g. ops-agent's thin httpx client)
    # do a single one-shot tools/call POST with no initialize handshake, so
    # they never learn a session id. Stateful mode (FastMCP's default) rejects
    # those with "400 Missing session ID"; stateless mode treats every POST as
    # self-contained, which matches how devclaw-mcp is actually called here.
    app = mcp.http_app(
        path="/mcp", middleware=[Middleware(AuthMiddleware)], stateless_http=True
    )
    _start_loops()
    _kick_readiness_recovery()
    config = uvicorn.Config(app, host=HTTP_HOST, port=HTTP_PORT, log_level="warning")
    await uvicorn.Server(config).serve()


def main() -> None:
    transport = os.environ.get("DEVCLAW_TRANSPORT", "stdio")
    if transport not in ("stdio", "http"):
        raise SystemExit(f'Unknown DEVCLAW_TRANSPORT={transport}; expected "stdio" or "http"')

    # Crash recovery before anything serves: reset tasks left 'running' by a
    # dead process so the heartbeat resumes them. Sync — runs before the loop.
    reaped = queue.recover()

    # #524 P3 migration, ONCE per database (#616): stamp project_id onto goals
    # written before the field, so a long-lived goal in flight at deploy keeps
    # its owning project's pinned knobs (verify_done/autodeploy)
    # instead of falling to the devclaw-wide defaults. Marker-guarded and
    # zero-token — it resolves each goal's owner by the workspace-path match
    # exactly once and never scans again (devclaw/goal/project_id_cutoff.py).
    backfilled = goals.backfill_project_ids()
    if backfilled:
        sys.stderr.write(f"{SERVER_NAME}: backfilled project_id on {backfilled} goal(s)\n")

    # Seed Claude workspace-trust for the cwd cognition runs `claude --print` in
    # (this container's /app). Without it Claude Code (since ~2026-07) ignores
    # the workspace's .claude/settings.json permissions and the planner/evaluator
    # exit non-zero on the untrusted-workspace guard — goals can't even plan.
    # Best-effort, idempotent, pure config (no ANTHROPIC_* — see claude_trust).
    if ensure_trusted_in_place(config_path_for(), os.getcwd()):
        sys.stderr.write(f"{SERVER_NAME}: seeded Claude workspace-trust for {os.getcwd()}\n")

    if transport == "stdio":
        sys.stderr.write(
            f"{SERVER_NAME} v{__version__} ready (stdio, db={DB_PATH}, recovered={reaped})\n"
        )
        asyncio.run(_serve_stdio())
    else:
        sys.stderr.write(
            f"{SERVER_NAME} v{__version__} ready "
            f"(http://{HTTP_HOST}:{HTTP_PORT}/mcp, db={DB_PATH}, recovered={reaped})\n"
        )
        asyncio.run(_serve_http())
