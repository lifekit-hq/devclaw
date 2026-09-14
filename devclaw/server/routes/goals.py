"""Goal read + verb feeds the console uses."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...dispatch_gate import operator_block
from ...state_store import _now_ms
from .._state import goals, mcp, store
from ._attention import attention, dispatch_facts


def _control() -> dict:
    now = _now_ms()
    hold_on, _r = store.operator_hold()
    blocked, _why = operator_block(store.operator_hold(), store.get_run_schedule(), now)
    until, _reason = store.global_pause()
    return dispatch_facts(hold=hold_on, window_closed=blocked and not hold_on,
                          paused=bool(until and until > now), running=store.count_running())


def _with_attention(row: dict, control: dict) -> dict:
    last = store.latest_task_for_goal(row["id"])
    g = store.get_goal(row["id"])
    try:
        seen = json.loads(g.last_seen_json) if g is not None and g.last_seen_json else None
    except ValueError:
        seen = None
    row["attention"] = attention(row, last, store.list_decisions(row["id"]),
                                 seen if isinstance(seen, dict) else None, control)
    return row


@mcp.custom_route("/goals.json", methods=["GET"])
async def goals_json(_request: Request) -> Response:
    control = _control()
    return JSONResponse([_with_attention(g, control) for g in goals.list_goals()])


@mcp.custom_route("/goals/{goal_id}.json", methods=["GET"])
async def goal_json(request: Request) -> Response:
    try:
        return JSONResponse(_with_attention(goals.get_goal(request.path_params["goal_id"]), _control()))
    except KeyError:
        return JSONResponse({"error": "not_found"}, status_code=404)


@mcp.custom_route("/goals/{goal_id}/cancel", methods=["POST"])
async def goal_cancel(request: Request) -> Response:
    try:
        return JSONResponse(goals.cancel_goal(request.path_params["goal_id"]))
    except KeyError:
        return JSONResponse({"error": "not_found"}, status_code=404)


@mcp.custom_route("/goals/{goal_id}/decide", methods=["POST"])
async def goal_decide(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    text = str((body or {}).get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "text required"}, status_code=400)
    try:
        return JSONResponse(await goals.decide(request.path_params["goal_id"], text))
    except KeyError:
        return JSONResponse({"error": "not_found"}, status_code=404)
    except (ValueError, RuntimeError) as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
