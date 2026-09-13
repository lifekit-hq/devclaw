"""Helpers shared by more than one tool module."""

from __future__ import annotations

from fastmcp.exceptions import ToolError

from ...project_registry import ResolvedDispatch, UnknownProject
from .._state import registry


def _resolve_project_or_reject(project_id: str, tool: str) -> ResolvedDispatch:
    """The single seam every goal tool crosses: a project reference key →
    its workspace + repo, or a synchronous refusal."""
    if not project_id:
        raise ToolError(f"{tool} requires project_id")
    try:
        return registry.resolve_dispatch(project_id)
    except UnknownProject:
        raise ToolError(f"unknown project_id: {project_id!r} — register it first (register_project / list_projects)")
    except ValueError as exc:
        raise ToolError(str(exc))
