"""The project registry (control plane) — the portfolio view.

Register/update/link/delete projects; status is joined live from the goal
layer, never cached.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from fastmcp.exceptions import ToolError

from ... import host_resources as _host_resources
from ...project_registry import ProjectExists, project_rollup
from .._state import goals, mcp, registry, store


# ===== project registry (control plane) ======================================
# The portfolio view: which repos devclaw owns + the live status of each. The
# registry links repos to their driving goals; status is joined live.


@mcp.tool
async def register_project(
    project_id: str,
    name: str,
    repo_url: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    preview_url: Optional[str] = None,
    notes: str = "",
    autodeploy: Optional[Literal["on", "off"]] = None,
    review_gate: Optional[Literal["on", "off"]] = None,
    verify_done: Optional[Literal["on", "off"]] = None,
) -> str:
    """Register a repo in the project registry — the control plane's source of
    truth for 'what is devclaw working on'. ``project_id`` is a stable slug (e.g.
    'todo-fullstack-demo'). Link the goal(s) driving it with link_goal. Idempotent
    failure: a taken id is an error (use update_project to change it).

    Per-project override knobs (each overrides its devclaw-wide env default;
    omit to inherit — the usual choice). This is the ONLY place these are
    configured per repo; a goal itself carries none of them:
      - ``autodeploy`` — deploy on goal completion (devclaw default: conditional —
        on only when the workspace has an app surface the preview launcher can
        serve; a pure library gets no preview container. 'on'/'off' pins it).
      - ``review_gate`` — run the pre-PR review gate (devclaw default: on).
      - ``verify_done`` — grounded done-gate re-check before closing (devclaw default: on)."""
    if not project_id or not name:
        raise ToolError("register_project requires project_id and name")
    _onoff = {"on": True, "off": False}
    try:
        p = registry.create(
            id=project_id, name=name, repo_url=repo_url,
            workspace_dir=workspace_dir, preview_url=preview_url, notes=notes,
            autodeploy=(None if autodeploy is None else _onoff[autodeploy]),
            review_gate=(None if review_gate is None else _onoff[review_gate]),
            verify_done=(None if verify_done is None else _onoff[verify_done]),
        )
    except ProjectExists:
        raise ToolError(f"project already exists: {project_id}")
    return json.dumps(p.to_dict(), indent=2)


@mcp.tool
async def list_projects(status: Optional[str] = None) -> str:
    """List registered projects with a live status rollup (each project's linked
    goals' phase/direction + a derived health: working/blocked/done/idle/archived).
    Filter by status (active|paused|archived). This is the 'show me everything'
    surface for chat / API / CLI."""
    all_goals = goals.list_goals()
    items = [
        project_rollup(p, all_goals)
        for p in registry.list(status=status)  # type: ignore[arg-type]
    ]
    return json.dumps(items, indent=2)


@mcp.tool
async def project_status(project_id: str) -> str:
    """Full status of one registered project: its facts (repo, workspace, preview)
    plus the LIVE status of every goal driving it and a derived health signal."""
    p = registry.get(project_id)
    if p is None:
        raise ToolError(f"unknown project_id: {project_id}")
    return json.dumps(project_rollup(p, goals.list_goals()), indent=2)


@mcp.tool
async def update_project(
    project_id: str,
    name: Optional[str] = None,
    repo_url: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    preview_url: Optional[str] = None,
    status: Optional[Literal["active", "paused", "archived"]] = None,
    notes: Optional[str] = None,
    autodeploy: Optional[Literal["on", "off", "inherit"]] = None,
    review_gate: Optional[Literal["on", "off", "inherit"]] = None,
    verify_done: Optional[Literal["on", "off", "inherit"]] = None,
    sandbox_image: Optional[str] = None,
    sandbox_memory: Optional[str] = None,
    sandbox_cpus: Optional[str] = None,
    bench: Optional[bool] = None,
) -> str:
    """Update a registered project's facts — only the fields you pass change. Use to
    record a preview URL, pause/archive it, or correct the repo/workspace.

    Per-project override knobs — ``autodeploy`` / ``review_gate`` /
    ``verify_done`` / ``sandbox_image`` —
    each take a concrete value to PIN this project (overriding its devclaw-wide
    env default), 'inherit' to CLEAR a prior override back to that default, or
    omit to leave whatever is currently set untouched. (bool knobs take
    'on'/'off'; sandbox_image takes a docker image ref — ADR 0005's escape hatch, e.g. pin
    'devclaw-sandbox-dotnet:local' until the mise path passes its gate;
    sandbox_memory takes a docker memory string like '6g' and sandbox_cpus a
    number string like '4.0' — spec 020's per-project sizing, validated at
    write time incl. host admittability, so an impossible value errors HERE
    instead of wedging dispatch later.)"""
    override_kwargs: dict = {}
    _onoff = {"on": True, "off": False, "inherit": None}
    for field, val in (("autodeploy", autodeploy),
                       ("review_gate", review_gate), ("verify_done", verify_done)):
        if val is not None:
            override_kwargs[field] = _onoff[val]
    if sandbox_image is not None:
        override_kwargs["sandbox_image"] = None if sandbox_image == "inherit" else sandbox_image
    if sandbox_memory is not None:
        override_kwargs["sandbox_memory"] = None if sandbox_memory == "inherit" else sandbox_memory
    if sandbox_cpus is not None:
        override_kwargs["sandbox_cpus"] = None if sandbox_cpus == "inherit" else sandbox_cpus
    if bench is not None:
        # bench (spec 018 US2): evidence/shakedown marker — excluded from every
        # ratchet-facing scorecard rate. Plain bool, no inherit state.
        override_kwargs["bench"] = bool(bench)
    try:
        p = registry.update(
            project_id, name=name, repo_url=repo_url, workspace_dir=workspace_dir,
            preview_url=preview_url, status=status, notes=notes,
            **override_kwargs,
        )
    except KeyError:
        raise ToolError(f"unknown project_id: {project_id}")
    except ValueError as exc:
        # flag-shaped/empty sandbox_image etc. — surface the registry's
        # validation verdict instead of a bare 500
        raise ToolError(str(exc))
    return json.dumps(p.to_dict(), indent=2)


_TERMINAL_GOAL_PHASES = {"done", "cancelled", "error", "achieved"}


def _project_active_goal_ids(project) -> list[str]:
    """All non-terminal goal ids that belong to this project — by ``project_id``
    match (the authoritative join, #524 P3) OR by the advisory ``goal_ids`` list.

    Used by the one-goal-per-project warn (2026-07-04): both entry points
    (create_goal against this project, or link_goal directly) count toward the
    "already-has-active-goal" state. Re-keyed off the old normalized-workspace
    match so a workspace rename can't drift the count."""
    seen: set[str] = set()
    active: list[str] = []
    for g in goals.list_goals():
        gid = g.get("id")
        if not gid:
            continue
        if g.get("phase") in _TERMINAL_GOAL_PHASES:
            continue
        matches_project = g.get("project_id") == project.id
        matches_link = gid in (project.goal_ids or [])
        if matches_project or matches_link:
            if gid not in seen:
                seen.add(gid)
                active.append(gid)
    return active


@mcp.tool
async def link_goal(project_id: str, goal_id: str, unlink: bool = False) -> str:
    """Attach (or, with unlink=True, detach) a durable goal to/from a project. The
    link is by id only — the goal's status is joined live in list_projects /
    project_status, never copied. Idempotent.

    Warn-first one-goal-per-project (2026-07-04): if the project already has
    an active goal, linking a second one still succeeds but the response
    carries a ``warning`` field and the console renders a banner. Hard reject
    lands in a follow-up PR after the warn phase has bake time. Under the
    standing rule, a project pursues one well-defined goal at a time — if
    you need a new direction, cancel + refile."""
    try:
        p = (
            registry.unlink_goal(project_id, goal_id)
            if unlink
            else registry.link_goal(project_id, goal_id)
        )
    except KeyError:
        raise ToolError(f"unknown project_id: {project_id}")
    out = p.to_dict()
    if not unlink:
        other_active = [gid for gid in _project_active_goal_ids(p) if gid != goal_id]
        if other_active:
            out["warning"] = {
                "code": "multiple_active_goals",
                "message": (
                    "This project already has "
                    f"{len(other_active)} active goal(s): "
                    f"{', '.join(other_active)}. Under the one-goal-per-project "
                    "rule (2026-07-04) a project pursues one goal at a time — "
                    "cancel + refile instead of stacking. This will become a "
                    "hard error after the warn-first phase."
                ),
                "otherActiveGoalIds": other_active,
            }
    return json.dumps(out, indent=2)


@mcp.tool
async def delete_project(
    project_id: str,
    release_resources: bool = True,
    dry_run: bool = False,
) -> str:
    """Permanently remove a project from the registry (HARD delete) AND release the
    durable host resources it owned — its workspace checkout and its per-project
    toolchain volume. The goals it linked are untouched as records (they live in
    the goal store and are just unlinked from this view). To retire a project while
    keeping its record + a paper trail, prefer update_project(status='archived').
    Raises if the id is unknown (so a typo doesn't silently no-op).

    Release is REFUSED — and the registry row is kept — while any goal on that
    workspace is non-terminal or any task on it is still running; the response
    names the blockers. Pass release_resources=False to drop the record only (the
    pre-#595 behavior, which leaks the disk), or dry_run=True to see what would be
    released without deleting anything.
    """
    project = registry.get(project_id)
    if project is None:
        raise ToolError(f"unknown project_id: {project_id}")

    out: dict = {"project_id": project_id}
    if release_resources:
        release = _host_resources.release_for_project(
            getattr(project, "workspace_dir", None),
            goals=goals.list_goals(),
            running_tasks=store.list_tasks(status="running", limit=500),
            dry_run=dry_run,
        )
        out["resources"] = release
        if release["blocked"]:
            # Live state on that workspace — keep the record too. Deleting it
            # while its resources must stay would orphan them permanently: the
            # record is the only thing that can ever find them again.
            out["deleted"] = False
            return json.dumps(out, indent=2)

    if dry_run:
        out["deleted"] = False
        return json.dumps(out, indent=2)

    if not registry.delete(project_id):
        raise ToolError(f"unknown project_id: {project_id}")
    out["deleted"] = True
    return json.dumps(out, indent=2)
