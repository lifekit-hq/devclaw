"""Helpers shared by more than one tool module.

Anything here is used by at least two modules. A helper used by exactly one
tool module belongs in that module — this is not a junk drawer. State is
rebound at module level (``from .._state import registry``): a test patches
``_common.registry`` to stub project resolution for every tool that crosses
this seam.
"""

from __future__ import annotations

from pathlib import Path as _Path

from fastmcp.exceptions import ToolError

from ...engine.workspace import (
    WorkspaceError,
    prepare_workspace,
    workspace_is_dispatchable,
)
from ...project_registry import ResolvedDispatch, UnknownProject
from .._state import registry


def _resolve_project_or_reject(project_id: str, tool: str) -> ResolvedDispatch:
    """Resolve a dispatch reference key (spec 003 / #520) into its concrete
    workspace + repo, or reject the tool call synchronously. This is the single
    seam every dispatch/goal tool crosses instead of taking a raw path — an
    unknown project fails HERE with an actionable ToolError and zero task/engine
    work, never a claimed task that dies deep in the engine."""
    if not project_id:
        raise ToolError(f"{tool} requires project_id")
    try:
        return registry.resolve_dispatch(project_id)
    except UnknownProject:
        raise ToolError(
            f"unknown project_id: {project_id!r} — register it first "
            f"(register_project / list_projects)"
        )
    except ValueError as exc:
        raise ToolError(str(exc))


async def _preflight_or_prep(resolved: ResolvedDispatch, project_id: str) -> None:
    """Direct-path dispatch preflight + auto-prep (spec 003 US2/US4, #520 P2).

    P1 (US2): reject at admission if the resolved workspace isn't a real git
    checkout, BEFORE queue.submit claims the task — never a late sandbox failure.

    P2 (US4, #523): when the workspace is simply ABSENT and the project carries a
    ``repo_url``, auto-clone it from that repo instead of rejecting — extending
    to the direct path the convenience the goal path already has (its workspace
    is cloned by ``prepare_workspace`` on the tick). Scope is deliberately narrow:
    - absent + repo_url  → clone, then proceed (or reject loud if the clone fails)
    - absent + no repo_url → reject (nothing to clone from)
    - exists-but-non-git → reject (git clone can't land in a non-empty dir; and
      resetting an existing checkout is not the direct path's job)

    Still zero-token (git subprocess only; no LLM). The GOAL path routes through
    ``prepare_workspace`` + ``_block_on_prep_failure`` and does not use this."""
    reason = workspace_is_dispatchable(resolved.workspace_dir)
    if reason is None:
        return
    absent = not _Path(resolved.workspace_dir).exists()
    if absent and resolved.repo_url:
        try:
            await prepare_workspace(resolved.workspace_dir, resolved.repo_url)
        except WorkspaceError as exc:
            raise ToolError(
                f"cannot dispatch to project {project_id!r}: auto-prep from "
                f"{resolved.repo_url!r} failed: {exc}"
            )
        return
    raise ToolError(f"cannot dispatch to project {project_id!r}: {reason}")
