"""One operator surface (#549) — the legacy server-rendered dashboard routes
302 onto their console equivalents instead of serving a second UI.

The live confusion this pins against (2026-08-17): the operator was pointed at
`/dashboard` by a skill link while knowing `/console` as *the* console — two
surfaces, one of them stale. These tests guard the redirect map:

  * `/dashboard`               → `/console/goals`
  * `/goals` · `/projects` · `/goals/{goal_id}` (the legacy HTML family that
    shared the dashboard nav) → their console pages,
  * the auth `?token=` query rides along, so a gated deployment's deep link
    still lands authenticated after the hop.

JSON/SSE surfaces (`/goals/{id}.json`, …) are NOT redirected — only the HTML
pages move. (The `/dashboard/{program_id}` deep link and its tests died with
the program/DAG lane's read surface — spec 022 US3 demolition.)
"""

from __future__ import annotations

import asyncio

from starlette.requests import Request

import devclaw.server.routes.console as console_routes


def _get(fn, path_params: dict | None = None, query: bytes = b""):
    scope = {
        "type": "http", "method": "GET", "path": "/legacy",
        "path_params": path_params or {}, "headers": [], "query_string": query,
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = asyncio.run(fn(Request(scope, receive)))
    return resp.status_code, resp.headers.get("location")


def test_dashboard_redirects_to_console():
    status, location = _get(console_routes.dashboard_index)
    assert status == 302
    assert location == "/console/goals"


def test_legacy_goals_and_projects_html_redirect_to_console_pages():
    assert _get(console_routes.dashboard_goals) == (302, "/console/goals")
    assert _get(console_routes.dashboard_projects) == (302, "/console/projects")
    assert _get(console_routes.dashboard_goal, {"goal_id": "g1"}) == (
        302, "/console/goals/g1",
    )


def test_redirect_preserves_auth_token_query():
    status, location = _get(console_routes.dashboard_index, query=b"token=sekret")
    assert status == 302
    assert location == "/console/goals?token=sekret"
