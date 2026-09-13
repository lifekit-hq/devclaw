"""The goal surface (spec 046): create, read, decide, cancel. No steer, no
resume, no correction — an instruction is a comment mentioning the bot."""

from __future__ import annotations

import json
from typing import Annotated, Optional

from fastmcp.exceptions import ToolError
from pydantic import Field

from ...goal.github import IssueError
from .._state import goals, mcp, store
from ._common import _resolve_project_or_reject


@mcp.tool
async def create_goal(goal_id: str, project_id: str, issues: list[int], objective: str = "") -> str:
    """Register a goal devclaw drives to completion. ``issues`` are the
    issue numbers on the project's repository — the issue IS the contract:
    its ``## Done when`` (or ``## Acceptance``) section is what the done-gate
    judges, read live on every round. Every session works on branch
    ``goal/<goal_id>`` and delivers one cumulative PR; a confirmed-achieved
    close squash-merges it. ``objective`` is display text only."""
    if not goal_id or not goal_id.replace("-", "").replace("_", "").isalnum():
        raise ToolError("create_goal requires a slug-shaped goal_id")
    if not issues:
        raise ToolError("create_goal requires issues — the issue is the contract")
    resolved = _resolve_project_or_reject(project_id, "create_goal")
    if not resolved.repo_url:
        raise ToolError(f"project {project_id!r} has no repo_url — set it with update_project")
    try:
        out = await goals.create_goal(
            goal_id, project_id=resolved.project_id or project_id,
            workspace_dir=resolved.workspace_dir, repo_url=resolved.repo_url,
            issues=list(issues), objective=objective,
        )
    except FileExistsError:
        raise ToolError(f"goal {goal_id!r} already exists")
    except (ValueError, IssueError) as exc:
        raise ToolError(str(exc))
    return json.dumps(out, indent=2)


@mcp.tool
async def get_goal(goal_id: str) -> str:
    """One goal: its facts, derived state, recent sessions (how each ended),
    the last world fingerprint it was given, and its decisions."""
    try:
        return json.dumps(goals.get_goal(goal_id), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


@mcp.tool
async def list_goals() -> str:
    """Every goal with its derived state and last session."""
    return json.dumps(goals.list_goals(), indent=2)


@mcp.tool
async def decide(goal_id: str, text: str) -> str:
    """The owner's verb. Posts ``text`` on the goal's issue as an instruction
    mentioning the bot and records it as a Decision; the next tick sees a
    newer instruction than the last stop and spawns a session that reads it.
    Use it to answer a BLOCKED question, accept or reject a done-gate verdict,
    or change direction."""
    if not goal_id or not (text or "").strip():
        raise ToolError("decide requires goal_id and text")
    try:
        return json.dumps(await goals.decide(goal_id, text), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")
    except (ValueError, RuntimeError) as exc:
        raise ToolError(str(exc))


@mcp.tool
async def cancel_goal(goal_id: str) -> str:
    """Permanently stop a goal: its running session is torn down, the goal
    is closed ``cancelled``, its checkout removed. The branch and PR stay."""
    if not goal_id:
        raise ToolError("cancel_goal requires goal_id")
    try:
        return json.dumps(goals.cancel_goal(goal_id), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


@mcp.tool
async def get_status() -> str:
    """The instance at a glance: goals with state, sessions running, the
    quota pause, the operator hold, the run window, the last tick."""
    return json.dumps(goals.status(), indent=2)


@mcp.tool
async def get_events(task_id: str, since_id: int = 0,
                     limit: Annotated[int, Field(ge=1, le=1000)] = 200) -> str:
    """The raw event stream of one session (agent messages, tool calls, the
    verify result), in emission order; ``since_id`` resumes after a cursor."""
    if not task_id:
        raise ToolError("get_events requires task_id")
    events = store.list_events(task_id=task_id, since_id=since_id or None, limit=limit)
    return json.dumps([e.to_dict() for e in events], indent=2)


__all__ = ["create_goal", "get_goal", "list_goals", "decide", "cancel_goal", "get_status", "get_events", "Optional"]
