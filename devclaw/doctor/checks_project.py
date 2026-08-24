"""Per-project doctor checks — registry/goal link integrity + workspace state.

Same posture as the instance checks: mechanical, read-only, zero cognition.
US2/US3 extend this module with manifest / revision / marker / scaffold
checks (spec 016 FR-003).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..engine.workspace import workspace_is_dispatchable
from .model import Finding, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from ..project_registry import Project
    from .context import InstanceContext


def check_workspace_preflight(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    cid = "project.workspace.preflight"
    reason = workspace_is_dispatchable(project.workspace_dir)
    if reason:
        return [Finding(cid, Verdict.FAIL, reason,
                        remedy="update_project (or restore the workspace checkout)",
                        project_id=project.id)]
    return [Finding(cid, Verdict.OK,
                    f"workspace {project.workspace_dir} dispatchable",
                    project_id=project.id)]


def check_dangling_links(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Advisory ``goal_ids`` entries pointing at goals that no longer exist.

    Today this drift is INVISIBLE: ``project_rollup`` joins on ``project_id``
    only and simply emits nothing for a dangling link (nothing ever sets the
    vestigial ``missing`` marker). Doctor is the producer of that finding —
    as a report line, never a row mutation.
    """
    cid = "project.links.dangling"
    dangling = sorted(gid for gid in (project.goal_ids or []) if not ctx.goal_store.exists(gid))
    if dangling:
        return [Finding(
            cid, Verdict.WARN,
            f"goal_ids entr{'ies' if len(dangling) > 1 else 'y'} resolving to no goal: "
            f"{', '.join(dangling)} (cancel+refile drift)",
            remedy="link_goal (relink or unlink the stale id)",
            project_id=project.id,
        )]
    return [Finding(cid, Verdict.OK, "all advisory goal links resolve", project_id=project.id)]


def check_unstamped_goals(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Goals whose workspace maps onto this project but carry no project_id.

    The one-shot backfill (goal/project_id_cutoff.py) never re-runs, so a goal
    created in a gap stays unstamped forever and silently drops out of every
    project rollup.
    """
    cid = "project.links.unstamped_goals"
    unstamped = sorted(
        g.id for g in ctx.goals
        if g.project_id is None and g.workspace_dir and g.workspace_dir == project.workspace_dir
    )
    if unstamped:
        return [Finding(
            cid, Verdict.WARN,
            f"goal(s) on this workspace with no project_id stamp: {', '.join(unstamped)} — "
            "invisible to project rollups",
            remedy="link_goal",
            project_id=project.id,
        )]
    return [Finding(cid, Verdict.OK, "no unstamped goals on this workspace", project_id=project.id)]


PROJECT_CHECKS: tuple = (
    check_workspace_preflight,
    check_dangling_links,
    check_unstamped_goals,
)
