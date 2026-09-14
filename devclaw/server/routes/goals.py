"""Goal read + verb feeds the console uses."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .._state import goals, mcp, store
from ._attention import control_facts, with_attention


def _with_usage(row: dict) -> dict:
    row["usage"] = store.usage_totals(parent_goal_id=row["id"])
    return row


@mcp.custom_route("/goals.json", methods=["GET"])
async def goals_json(_request: Request) -> Response:
    control = control_facts(store)
    return JSONResponse([_with_usage(with_attention(g, store, control)) for g in goals.list_goals()])


@mcp.custom_route("/goals/{goal_id}.json", methods=["GET"])
async def goal_json(request: Request) -> Response:
    try:
        row = with_attention(goals.get_goal(request.path_params["goal_id"]), store, control_facts(store))
        return JSONResponse(_with_usage(row))
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
