"""Observability read surfaces — problems, token usage, run traces.

Three read-only projections the console's operator views render:
``/problems.json`` (the deduplicated problems catalog with its lifecycle
stage), ``/usage.json`` (the instance-wide token/cost aggregate) and
``/traces.json`` (the run-trace read surface, every filter applied in SQL).
"""

from __future__ import annotations

import time

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import telemetry as _telemetry
from ...state_store.problems import problem_lifecycle as _problem_lifecycle
from .._state import goals, mcp, registry, store
from ._common import json_limit

_TRACES_JSON_DEFAULT_LIMIT = 200
_TRACES_JSON_MAX_LIMIT = 1000

@mcp.custom_route("/problems.json", methods=["GET"])
async def problems_json(request: Request) -> Response:
    """The deduplicated problems catalog for the console problem-lifecycle
    tracker (ADR 0009 P2 + N2/#372). Each row carries its self-issue-filing
    Stage-1 fields (``issue_number``/``issue_state``) plus a derived
    ``lifecycle`` stage: identified → filed → **fixing** → resolved. ``fixing``
    is the restored §5.5 "being-worked" stage — a *filed & open* issue whose
    deterministic self-fix goal (``self-fix-issue-<n>``) exists; the row then
    carries ``fix_goal_id`` so the console deep-links to that goal (its PR +
    human-merge surface). HONEST: ``fixing`` means "a fix goal is running / a PR
    opens for your review", never autonomous auto-fix (fixing is propose-only).
    Params: ``category`` filter, ``limit`` (default 100, max 1000),
    ``since_ms`` (epoch-ms lower bound on ``last_seen_ms``; defaults to a
    30-day lookback so the default view isn't the full lifetime pile; pass
    ``since_ms=0`` to bypass the window and retrieve all-time). Read-only —
    a SELECT over ``problems`` plus one cheap goal-existence check per filed
    row; never wakes the goal loop."""
    from ...goal.self_issue import self_repo, self_fix_goal_id

    limit, err = json_limit(request)
    if err is not None:
        return err
    _THIRTY_DAYS_MS = 30 * 24 * 60 * 60 * 1000
    since_ms_param = request.query_params.get("since_ms")
    if since_ms_param is not None:
        try:
            raw = int(since_ms_param)
        except ValueError:
            return Response("invalid since_ms: must be an integer", status_code=400)
        # 0 (or negative) → caller wants all-time; positive → use as lower bound.
        since_ms: int | None = raw if raw > 0 else None
    else:
        since_ms = int(time.time() * 1000) - _THIRTY_DAYS_MS
    rows = store.list_problems(
        category=request.query_params.get("category") or None,
        limit=limit,
        include_issue=True,
        since_ms=since_ms,
    )
    for p in rows:
        stage = _problem_lifecycle(p)
        # Restore the §5.5 "being-worked" stage the P2 impl folded away: a filed
        # & open issue with a live self-fix goal reads `fixing`, linking to it.
        if stage == "filed" and p.get("issue_number"):
            gid = self_fix_goal_id(int(p["issue_number"]))
            if goals.has_goal(gid):
                stage = "fixing"
                p["fix_goal_id"] = gid
        p["lifecycle"] = stage
    # `selfRepo` (owner/name, or null when self-issue-filing is off) lets the
    # console build issue links without hardcoding the repo.
    return JSONResponse({"problems": rows, "count": len(rows), "selfRepo": self_repo()})


@mcp.custom_route("/usage.json", methods=["GET"])
async def usage_json(_request: Request) -> Response:
    """Instance-wide usage aggregate (cognition + worker tokens, per-project
    breakdown, cap-pressure history). Read-only over SQLite — no LLM call,
    no write, cheap enough to poll from the console's Usage page."""
    all_goals = goals.list_goals()
    return JSONResponse(_telemetry.compute_instance_usage(store, registry, all_goals))


@mcp.custom_route("/traces.json", methods=["GET"])
async def traces_json(request: Request) -> Response:
    """General telemetry read over the ``traces`` table — the same filters the
    ``devclaw trace list`` CLI exposes, for dashboards/scripts that already
    speak HTTP to this server.

    Query params: ``goal`` (or ``goal_id``), ``kind``, ``role`` (cognition
    payload field), ``since`` (30m/24h/7d or ISO timestamp), ``errors_only``
    (1/true), ``limit`` (default 200, max 1000). Rows come back newest-first.
    Every filter is applied in SQL by ``StateStore.read_traces`` — the
    production table holds 200k+ rows, so this route never loads-then-filters
    in Python. Read-only: auth/token handling is the transport-wide middleware,
    same as every other route here."""
    q = request.query_params
    since_ms = None
    since = q.get("since")
    if since:
        try:
            since_ms = _telemetry.parse_since(since)
        except ValueError as exc:
            return JSONResponse({"error": "bad_since", "detail": str(exc)}, status_code=400)
    try:
        limit = int(q.get("limit", _TRACES_JSON_DEFAULT_LIMIT))
    except ValueError:
        return JSONResponse({"error": "bad_limit"}, status_code=400)
    if limit <= 0:
        return JSONResponse({"error": "bad_limit"}, status_code=400)
    limit = min(limit, _TRACES_JSON_MAX_LIMIT)
    rows = store.read_traces(
        goal_id=q.get("goal") or q.get("goal_id") or None,
        kind=q.get("kind") or None,
        role=q.get("role") or None,
        since_ms=since_ms,
        errors_only=str(q.get("errors_only", "")).lower() in ("1", "true", "yes"),
        limit=limit,
        newest_first=True,
    )
    return JSONResponse({"traces": rows, "count": len(rows), "limit": limit})


# ── cognition transcripts (the FULL prompt + response of every claude --print
# call). The trace EVENTS (/traces.json) carry names + 240-char previews; these
# two routes carry the whole thing, so the console can show what cognition was
# actually fed and said back — the same ground truth as the `devclaw trace_view`
# CLI, reusing its stdlib parser so the wire format can't drift from disk.
#
# Pure read-only: they read `.md` files that already exist under the goals dir.
# No goal/task/store write, no goal-loop wake, no LLM call, no subprocess — same
# bar as get_trace ("never wakes the goal loop / pure SELECT").
