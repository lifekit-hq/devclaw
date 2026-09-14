"""Project feeds."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...project_registry import project_rollup
from .._state import goals, mcp, registry, store
from ._attention import control_facts, with_attention


def _goal_rows() -> list[dict]:
    control = control_facts(store)
    return [with_attention(g, store, control) for g in goals.list_goals()]


@mcp.custom_route("/projects/{project_id}.json", methods=["GET"])
async def project_json(request: Request) -> Response:
    p = registry.get(request.path_params["project_id"])
    if p is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(project_rollup(p, _goal_rows()))


@mcp.custom_route("/projects.json", methods=["GET"])
async def projects_json(_request: Request) -> Response:
    all_goals = _goal_rows()
    return JSONResponse([project_rollup(p, all_goals) for p in registry.list()])
