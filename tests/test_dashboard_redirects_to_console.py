"""One operator surface (#549) — the legacy server-rendered dashboard routes
302 onto their console equivalents instead of serving a second UI.

The live confusion this pins against (2026-08-17): the operator was pointed at
`/dashboard` by a skill link while knowing `/console` as *the* console — two
surfaces, one of them stale. These tests guard the redirect map:

  * `/dashboard`               → `/console/goals`   (programs are goal-dispatched runs)
  * `/dashboard/{program_id}`  → `/console/goals/{parent_goal_id}` when the
    program has a durable goal owner, else the goals list — never a 404 page
    on the retired surface,
  * `/goals` · `/projects` · `/goals/{goal_id}` (the legacy HTML family that
    shared the dashboard nav) → their console pages,
  * the auth `?token=` query rides along, so a gated deployment's deep link
    still lands authenticated after the hop.

JSON/SSE surfaces (`/goals/{id}.json`, `/programs/{id}/events`, …) are NOT
redirected — only the HTML pages move.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.requests import Request

import devclaw.server.routes.console as console_routes
from devclaw.state_store import StateStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    s = StateStore(str(tmp_path / "devclaw.db"))
    monkeypatch.setattr(console_routes, "store", s)
    return s


def _get(fn, path_params: dict | None = None, query: bytes = b""):
    scope = {
        "type": "http", "method": "GET", "path": "/legacy",
        "path_params": path_params or {}, "headers": [], "query_string": query,
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    resp = asyncio.run(fn(Request(scope, receive)))
    return resp.status_code, resp.headers.get("location")


def test_dashboard_redirects_to_console(store):
    status, location = _get(console_routes.dashboard_index)
    assert status == 302
    assert location == "/console/goals"


def test_dashboard_program_deep_link_maps_to_owning_goal(store):
    store.create_program(
        id="prog-1", goal="build it", workspace_dir="/w",
        notify_url=None, parent_goal_id="ledger-2026-08-12",
    )
    status, location = _get(console_routes.dashboard_program, {"program_id": "prog-1"})
    assert status == 302
    assert location == "/console/goals/ledger-2026-08-12"


def test_dashboard_program_without_goal_owner_falls_back_to_goals_list(store):
    store.create_program(
        id="prog-2", goal="standalone", workspace_dir="/w", notify_url=None,
    )
    for params in ({"program_id": "prog-2"}, {"program_id": "no-such-program"}):
        status, location = _get(console_routes.dashboard_program, params)
        assert status == 302
        assert location == "/console/goals"


def test_legacy_goals_and_projects_html_redirect_to_console_pages(store):
    assert _get(console_routes.dashboard_goals) == (302, "/console/goals")
    assert _get(console_routes.dashboard_projects) == (302, "/console/projects")
    assert _get(console_routes.dashboard_goal, {"goal_id": "g1"}) == (
        302, "/console/goals/g1",
    )


def test_redirect_preserves_auth_token_query(store):
    status, location = _get(console_routes.dashboard_index, query=b"token=sekret")
    assert status == 302
    assert location == "/console/goals?token=sekret"
