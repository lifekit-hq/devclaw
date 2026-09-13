"""The project registry — which repos devclaw works on."""

from __future__ import annotations

import json
from typing import Literal, Optional

from fastmcp.exceptions import ToolError

from ...engine.workspace import remove_goal_checkout
from ...project_registry import ProjectExists, project_rollup
from .._state import goals, mcp, registry


@mcp.tool
async def register_project(project_id: str, name: str, repo_url: Optional[str] = None,
                           workspace_dir: Optional[str] = None, notes: str = "") -> str:
    """Register a repository. ``project_id`` is a stable slug; ``repo_url`` is
    the GitHub clone URL every goal on it reads and delivers to;
    ``workspace_dir`` is the host checkout goals clone their own trees from."""
    if not project_id or not name:
        raise ToolError("register_project requires project_id and name")
    try:
        p = registry.create(id=project_id, name=name, repo_url=repo_url,
                            workspace_dir=workspace_dir, notes=notes)
    except ProjectExists:
        raise ToolError(f"project already exists: {project_id}")
    except ValueError as exc:
        raise ToolError(str(exc))
    return json.dumps(p.to_dict(), indent=2)


@mcp.tool
async def list_projects(status: Optional[str] = None) -> str:
    """Registered projects with their goals' live state and a health word."""
    all_goals = goals.list_goals()
    return json.dumps([project_rollup(p, all_goals) for p in registry.list(status=status)],  # type: ignore[arg-type]
                      indent=2)


@mcp.tool
async def project_status(project_id: str) -> str:
    """One project: its facts plus every goal driving it."""
    p = registry.get(project_id)
    if p is None:
        raise ToolError(f"unknown project_id: {project_id}")
    return json.dumps(project_rollup(p, goals.list_goals()), indent=2)


@mcp.tool
async def update_project(project_id: str, name: Optional[str] = None, repo_url: Optional[str] = None,
                         workspace_dir: Optional[str] = None,
                         status: Optional[Literal["active", "paused", "archived"]] = None,
                         notes: Optional[str] = None, sandbox_image: Optional[str] = None,
                         sandbox_memory: Optional[str] = None, sandbox_cpus: Optional[str] = None) -> str:
    """Change a project's facts — only the fields you pass. ``sandbox_*``
    pin this project's sandbox image / memory / cpus ('inherit' clears)."""
    kwargs: dict = {}
    for k, v in (("sandbox_image", sandbox_image), ("sandbox_memory", sandbox_memory), ("sandbox_cpus", sandbox_cpus)):
        if v is not None:
            kwargs[k] = None if v == "inherit" else v
    try:
        p = registry.update(project_id, name=name, repo_url=repo_url, workspace_dir=workspace_dir,
                            status=status, notes=notes, **kwargs)
    except KeyError:
        raise ToolError(f"unknown project_id: {project_id}")
    except ValueError as exc:
        raise ToolError(str(exc))
    return json.dumps(p.to_dict(), indent=2)


@mcp.tool
async def delete_project(project_id: str) -> str:
    """Remove a project from the registry. Refused while any of its goals is
    open — cancel them first. Goal checkouts under the workspace are removed;
    the project checkout itself is left alone."""
    p = registry.get(project_id)
    if p is None:
        raise ToolError(f"unknown project_id: {project_id}")
    open_goals = [g["id"] for g in goals.list_goals() if g.get("projectId") == project_id and not g.get("outcome")]
    if open_goals:
        raise ToolError(f"project {project_id!r} has open goal(s) {open_goals} — cancel them first")
    if p.workspace_dir:
        for g in goals.list_goals():
            if g.get("projectId") == project_id:
                remove_goal_checkout(p.workspace_dir, g["id"])
    registry.delete(project_id)
    return json.dumps({"project_id": project_id, "deleted": True}, indent=2)
