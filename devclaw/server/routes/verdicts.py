"""Done-gate verdicts, read back from the review sessions' own output with
the gate's own parser (spec 047 US4). Nothing stored: ``parse_verdict`` runs
per row on request, so the page shows exactly what the gate decided —
unreadable included. ``head`` is the review's ``pre_run_sha``: the checkout
the review ran on is the delivered head."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...goal import donegate as _donegate
from ...state_store.rows import EXIT_REVIEW, Task
from .._state import mcp, store
from ._common import json_limit


def _pr_url(goal_id: str | None) -> str:
    if not goal_id:
        return ""
    for t in store.list_tasks(parent_goal_id=goal_id, limit=20):
        if t.pr_url:
            return t.pr_url
    return ""


def verdict_row(t: Task) -> dict:
    try:
        result = json.loads(t.result_json or "{}")
    except ValueError:
        result = {}
    if not isinstance(result, dict):
        result = {}
    v = _donegate.parse_verdict(str(result.get("agent_output") or ""))
    if t.status != "done":
        v = _donegate.Verdict(False, unreadable=True, raw_error=t.error or "review session failed")
    clauses = list(v.clauses)
    return {
        "taskId": t.id, "goalId": t.parent_goal_id, "createdAt": t.created_at,
        "completedAt": t.completed_at, "head": t.pre_run_sha or "",
        "achieved": v.achieved, "unreadable": v.unreadable, "rawError": v.raw_error,
        "question": v.question, "structuralHealth": v.structural_health,
        "concerns": list(v.concerns), "summary": v.summary, "clauses": clauses,
        "satisfied": sum(1 for c in clauses if c.get("satisfied") and c.get("evidence")),
        "total": len(clauses), "prUrl": _pr_url(t.parent_goal_id),
    }


def verdict_rows(*, parent_goal_id: str | None = None, limit: int = 100) -> list[dict]:
    tasks = store.list_tasks(exit=EXIT_REVIEW, parent_goal_id=parent_goal_id, limit=limit)
    return [verdict_row(t) for t in tasks]


@mcp.custom_route("/verdicts.json", methods=["GET"])
async def verdicts_json(request: Request) -> Response:
    limit, err = json_limit(request)
    if err is not None:
        return err
    rows = verdict_rows(limit=limit)
    return JSONResponse({"verdicts": rows, "count": len(rows), "truncated": len(rows) >= limit})
