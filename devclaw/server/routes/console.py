"""The console web app: static serving + the legacy dashboard redirects.

The SPA source is the top-level ``console/`` product; Vite builds it into
``devclaw/server/console_dist`` so the wheel ships it (see pyproject). These
routes serve that bundle and keep the pre-console ``/dashboard*`` URLs alive
as redirects.

``_CONSOLE_DIST`` is resolved from this module's location, so it follows the
package wherever it is installed — never from the working directory.
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
from urllib.parse import quote as _quote

from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from .._state import mcp, store
from ._projections import _safe_parse

# ---- Legacy dashboard → console redirects (#549, one operator surface) ----
# The server-rendered dashboard pages are retired behind 302s onto their
# console equivalents (their renderers are deleted). Deep links map where a
# mapping exists, else fall back to the console goals list; the incoming
# query string (the `?token=` auth) rides along so gated deployments stay
# reachable after the hop.


def _console_redirect(request: Request, to: str) -> Response:
    qs = request.url.query
    return RedirectResponse(url=f"{to}?{qs}" if qs else to, status_code=302)


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard_index(request: Request) -> Response:
    return _console_redirect(request, "/console/goals")


@mcp.custom_route("/dashboard/{program_id}", methods=["GET"])
async def dashboard_program(request: Request) -> Response:
    """A program's operator view is the goal that dispatched it — GoalDetail
    carries the live event tail and per-task drill-ins the old program page
    showed. A parent-less legacy program falls back to the goals list; the
    raw SSE feed at /programs/{id}/events is unchanged for scripts."""
    program = store.get_program(request.path_params["program_id"])
    parent = getattr(program, "parent_goal_id", None) if program else None
    to = f"/console/goals/{_quote(parent)}" if parent else "/console/goals"
    return _console_redirect(request, to)


@mcp.custom_route("/programs/{program_id}/events", methods=["GET"])
async def program_events(request: Request) -> Response:
    """Resumable SSE stream of one program's events.

    Resume protocol: the EventSource Last-Event-Id header (sent by the browser
    on auto-reconnect) is the cursor; each frame's id is the event row's PK.
    Live tail: SQLite has no LISTEN/NOTIFY, so we poll every 750ms after the
    initial backlog (cheap, indexed). Termination: when the program is terminal
    AND the last poll returned nothing new, emit a final `done` frame and close.
    """
    from sse_starlette.sse import EventSourceResponse  # local import: http-only dep path

    program_id = request.path_params["program_id"]
    if not store.get_program(program_id):
        return PlainTextResponse(f"unknown program: {program_id}", status_code=404)

    leh = request.headers.get("last-event-id")
    cursor = int(leh) if (leh and leh.isdigit() and int(leh) > 0) else 0

    async def gen():
        nonlocal cursor
        yield {"comment": "ok"}  # forces EventSource onopen even with zero events
        while True:
            if await request.is_disconnected():
                return
            try:
                drained = store.list_events(program_id=program_id, since_id=cursor, limit=200)
            except Exception as err:
                yield {"event": "error", "data": json.dumps({"message": str(err)})}
                return
            for ev in drained:
                yield {
                    "id": str(ev.id),
                    "data": json.dumps(
                        {
                            "id": ev.id,
                            "type": ev.type,
                            "source": ev.source,
                            "ts": ev.ts,
                            "payload": _safe_parse(ev.payload_json),
                        }
                    ),
                }
                cursor = ev.id
            current = store.get_program(program_id)
            terminal = current is not None and current.status in ("done", "failed")
            if terminal and not drained:
                yield {"event": "done", "data": json.dumps({"status": current.status})}
                return
            await asyncio.sleep(0.75)

    return EventSourceResponse(gen())


@mcp.custom_route("/goals", methods=["GET"])
async def dashboard_goals(request: Request) -> Response:
    return _console_redirect(request, "/console/goals")


@mcp.custom_route("/projects", methods=["GET"])
async def dashboard_projects(request: Request) -> Response:
    return _console_redirect(request, "/console/projects")


# ---- Console (Vite + React SPA, served as a static bundle) ----------------
# The three-screen web console lives under `console/`. `npm run
# build` writes `console/dist/`; the bytes on disk are what these routes serve.
# The SPA does client-side routing under basename="/console", so any path that
# doesn't map to a file falls through to `index.html`.

_CONSOLE_DIST = Path(__file__).resolve().parent / "console_dist"


def _serve_console_file(rel: str) -> Response:
    if not _CONSOLE_DIST.exists():
        return PlainTextResponse(
            "devclaw console bundle not built — run `npm --prefix "
            "console run build`",
            status_code=503,
        )
    # Resolve safely inside dist. `Path.resolve()` normalizes `..`, then we
    # verify the resolved path stays inside the dist tree.
    target = (_CONSOLE_DIST / rel).resolve()
    try:
        target.relative_to(_CONSOLE_DIST)
    except ValueError:
        return PlainTextResponse("forbidden", status_code=403)
    if target.is_file():
        media, _ = mimetypes.guess_type(str(target))
        return FileResponse(str(target), media_type=media)
    # SPA fallback: unknown paths serve the app shell so client-side routing works.
    index = _CONSOLE_DIST / "index.html"
    if not index.is_file():
        return PlainTextResponse("console index.html missing from bundle", status_code=500)
    return FileResponse(str(index), media_type="text/html")


@mcp.custom_route("/", methods=["GET"])
async def root_redirect(_request: Request) -> Response:
    """The human-facing surface is the console; a bare hostname visit should
    land there, not on a 404 (live-found 2026-07-09: the operator's bookmark
    pointed at `/`)."""
    return RedirectResponse(url="/console", status_code=307)


@mcp.custom_route("/console", methods=["GET"])
async def console_index(_request: Request) -> Response:
    return _serve_console_file("index.html")


@mcp.custom_route("/console/{path:path}", methods=["GET"])
async def console_asset(request: Request) -> Response:
    return _serve_console_file(request.path_params["path"] or "index.html")


# ---- JSON API surfaces the console reads ----------------------------------


@mcp.custom_route("/goals/{goal_id}", methods=["GET"])
async def dashboard_goal(request: Request) -> Response:
    """Legacy HTML goal detail → the console's GoalDetail (same data, richer:
    plan, PRs, transcripts, task drill-ins). The JSON feed stays at
    /goals/{goal_id}.json."""
    goal_id = request.path_params["goal_id"]
    return _console_redirect(request, f"/console/goals/{_quote(goal_id)}")
