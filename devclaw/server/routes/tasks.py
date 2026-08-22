"""Task read surfaces — the console's task drill-in.

``GET /tasks/{id}.json`` (one task's row + its worker usage) and
``GET /tasks/{id}/events.json`` (its decoded worker trace).
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .._state import mcp, store
from ._common import _task_row

def _valid_task_id(task_id: str) -> bool:
    """A task id is an opaque handle used only as a parameterized SQL bind —
    reject the obviously-malformed (empty / path-shaped) for hygiene."""
    return bool(task_id) and "/" not in task_id and "\\" not in task_id and ".." not in task_id


@mcp.custom_route("/tasks/{task_id}/events.json", methods=["GET"])
async def task_events_json(request: Request) -> Response:
    """Turn-by-turn execution trace of ONE task — the worker's actual run,
    readable AFTER it settles. Decodes each stored worker event into a
    readable row (agent message text / tool action / observation output), with
    the full untruncated ``raw`` payload attached so nothing is hidden (the #455
    guarantee). ``?since=<id>`` resumes after a cursor; ``?limit=<n>`` (default
    500, capped 1000) bounds one page and ``nextCursor`` is set when more remain.
    A task that never emitted events returns an empty list, not a 404. Read-only."""
    from .. import worker_events

    task_id = request.path_params["task_id"]
    if not _valid_task_id(task_id):
        return JSONResponse({"error": "bad_task_id"}, status_code=400)
    q = request.query_params
    since = int(q["since"]) if q.get("since", "").isdigit() else None
    limit = int(q["limit"]) if q.get("limit", "").isdigit() else 500
    limit = max(1, min(limit, 1000))
    try:
        events = store.list_events(task_id=task_id, since_id=since, limit=limit)
    except Exception as err:  # noqa: BLE001 — read-only surface, degrade to 500 not crash
        return JSONResponse({"error": str(err)}, status_code=500)
    rows = [worker_events.decode_event(ev) for ev in events]
    next_cursor = events[-1].id if len(events) == limit else None
    return JSONResponse({"events": rows, "count": len(rows), "nextCursor": next_cursor})


@mcp.custom_route("/tasks/{task_id}.json", methods=["GET"])
async def task_json(request: Request) -> Response:
    """One task as a first-class drill-in feed — the SAME anatomy no matter how
    the task was born (goal-dispatched or standalone dispatch_task): the header
    facts (kind/status/PR/timestamps/branch), the CONTRACT (the full prompt/goal
    text the worker was handed), and the settled verdicts decomposed from
    result_json (verify / delivery / diff stats / usage) — WITHOUT the bulky
    agent transcript (the turn-by-turn lives at /tasks/{id}/events.json, #455).
    404 on an unknown id — a typo is not an empty task. Read-only."""
    task_id = request.path_params["task_id"]
    if not _valid_task_id(task_id):
        return JSONResponse({"error": "bad_task_id"}, status_code=400)
    t = store.get_task(task_id)
    if t is None:
        return JSONResponse({"error": "unknown_task"}, status_code=404)
    row = _task_row(t)
    row["error"] = t.error
    row["verifyCmd"] = t.verify_cmd
    verify = delivery = diff_stats = usage = None
    result_corrupt = False
    if t.result_json:
        try:
            rj = json.loads(t.result_json)
            if isinstance(rj, dict):
                verify = rj.get("verify")
                delivery = rj.get("delivery")
                diff_stats = rj.get("diff_stats")
                usage = rj.get("usage")
        except Exception:  # noqa: BLE001 — a corrupt result must not 500 the drill-in;
            result_corrupt = True  # header + contract still render; the gap is FLAGGED
    return JSONResponse({
        "task": row,
        "verify": verify,
        "delivery": delivery,
        "diffStats": diff_stats,
        "usage": usage,
        "resultCorrupt": result_corrupt,
    })
