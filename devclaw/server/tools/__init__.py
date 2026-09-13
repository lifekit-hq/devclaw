"""Every ``@mcp.tool`` — registration is an import side effect."""

from __future__ import annotations

from . import control, doctor, goals, projects  # noqa: F401
from .control import (  # noqa: F401
    clear_usage_pause, get_run_schedule, set_max_concurrent, set_operator_hold, set_run_schedule,
)
from .doctor import doctor as doctor_tool  # noqa: F401
from .goals import cancel_goal, create_goal, decide, get_events, get_goal, get_status, list_goals  # noqa: F401
from .projects import delete_project, list_projects, project_status, register_project, update_project  # noqa: F401

__all__ = [
    "create_goal", "get_goal", "list_goals", "decide", "cancel_goal", "get_status", "get_events",
    "register_project", "list_projects", "project_status", "update_project", "delete_project",
    "get_run_schedule", "set_run_schedule", "set_operator_hold", "clear_usage_pause", "set_max_concurrent",
    "doctor_tool",
]
