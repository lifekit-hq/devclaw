"""Session (task) drill-ins: the row and its event stream."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...goal import donegate as _donegate
from ...state_store.rows import EXIT_BLOCKED
from .._state import mcp, store
from ._common import _task_row


def _valid_task_id(task_id: str) -> bool:
    return bool(task_id) and "/" not in task_id and "\\" not in task_id and ".." not in task_id


@mcp.custom_route("/tasks/{task_id}/events.json", methods=["GET"])
async def task_events_json(request: Request) -> Response:
    task_id = request.path_params["task_id"]
    if not _valid_task_id(task_id):
        return JSONResponse({"error": "bad_task_id"}, status_code=400)
    q = request.query_params
    since = int(q["since"]) if q.get("since", "").isdigit() else None
    limit = max(1, min(int(q["limit"]) if q.get("limit", "").isdigit() else 500, 1000))
    events = store.list_events(task_id=task_id, since_id=since, limit=limit)
    rows = [e.to_dict() for e in events]
    return JSONResponse({"events": rows, "count": len(rows),
                         "nextCursor": events[-1].id if len(events) == limit else None})


@mcp.custom_route("/tasks/{task_id}.json", methods=["GET"])
async def task_json(request: Request) -> Response:
    task_id = request.path_params["task_id"]
    if not _valid_task_id(task_id):
        return JSONResponse({"error": "bad_task_id"}, status_code=400)
    t = store.get_task(task_id)
    if t is None:
        return JSONResponse({"error": "unknown_task"}, status_code=404)
    row = _task_row(t)
    row["error"] = t.error
    row["goal"] = t.goal
    verify = delivery = change = agent_output = None
    if t.result_json:
        try:
            rj = json.loads(t.result_json)
            if isinstance(rj, dict):
                verify, delivery, change = rj.get("verify"), rj.get("delivery"), rj.get("change")
                agent_output = rj.get("agent_output")
        except ValueError:
            pass
    block = _donegate.block_fields(t.result_json, t.exit_detail) if t.exit == EXIT_BLOCKED else None
    return JSONResponse({"task": row, "verify": verify, "delivery": delivery, "change": change,
                         "agentOutput": agent_output, "block": block})
