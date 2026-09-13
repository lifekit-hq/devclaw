"""Project feeds."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ...project_registry import project_rollup
from .._state import goals, mcp, registry


@mcp.custom_route("/projects/{project_id}.json", methods=["GET"])
async def project_json(request: Request) -> Response:
    p = registry.get(request.path_params["project_id"])
    if p is None:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse(project_rollup(p, goals.list_goals()))


@mcp.custom_route("/projects.json", methods=["GET"])
async def projects_json(_request: Request) -> Response:
    all_goals = goals.list_goals()
    return JSONResponse([project_rollup(p, all_goals) for p in registry.list()])
