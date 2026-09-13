"""Helpers shared by more than one route module."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

JSON_DEFAULT_LIMIT = 100
JSON_MAX_LIMIT = 1000


def json_limit(request: Request) -> "tuple[int, Response | None]":
    try:
        limit = int(request.query_params.get("limit", JSON_DEFAULT_LIMIT))
    except (TypeError, ValueError):
        return 0, JSONResponse({"error": "bad_limit"}, status_code=400)
    if limit <= 0:
        return 0, JSONResponse({"error": "bad_limit"}, status_code=400)
    return min(limit, JSON_MAX_LIMIT), None


def _task_row(t) -> dict:
    return {
        "id": t.id, "kind": t.kind, "status": t.status, "workspaceDir": t.workspace_dir,
        "parentGoalId": t.parent_goal_id, "createdAt": t.created_at, "completedAt": t.completed_at,
        "prUrl": t.pr_url, "exit": t.exit, "exitDetail": t.exit_detail,
    }
