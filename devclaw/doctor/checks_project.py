"""Per-project doctor checks — registry/goal link integrity + workspace state.

Same posture as the instance checks: mechanical, read-only, zero cognition.
US2/US3 extend this module with manifest / revision / marker / scaffold
checks (spec 016 FR-003).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pathlib import Path

from .. import project_manifest as _manifest
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


# ---- manifest / boilerplate drift (spec 016 US3) --------------------------


def check_manifest(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Presence + validity + revision currency of the repo's devclaw.json.
    Worktree read on purpose — doctor reports the repo's CURRENT state; the
    gate reads stay pinned to the merged base (FR-009)."""
    pid = project.id
    ws = project.workspace_dir or ""
    if not ws or not Path(ws).exists():
        return [Finding("project.manifest.presence", Verdict.UNKNOWN,
                        "workspace not on disk — manifest state unknowable",
                        project_id=pid)]
    try:
        manifest = _manifest.load_manifest(ws)
    except _manifest.ManifestError as exc:
        return [Finding("project.manifest.valid", Verdict.FAIL, str(exc),
                        remedy="fix devclaw.json by PR (it is human-owned)",
                        project_id=pid)]
    if manifest is None:
        return [Finding("project.manifest.presence", Verdict.WARN,
                        "no devclaw.json — instance defaults apply; per-project "
                        "declarations (strictness, surface, verify_cmd) unavailable",
                        remedy="onboard (the install PR seeds one)",
                        project_id=pid)]
    findings = [Finding("project.manifest.presence", Verdict.OK,
                        f"devclaw.json present (schema {manifest.schema_version})",
                        project_id=pid)]
    if manifest.boilerplate_revision < _manifest.BOILERPLATE_REVISION:
        findings.append(Finding(
            "project.manifest.revision", Verdict.WARN,
            f"boilerplate revision {manifest.boilerplate_revision} behind the "
            f"instance's {_manifest.BOILERPLATE_REVISION}",
            remedy="onboard (re-onboard migrates via a reviewable PR)",
            project_id=pid))
    else:
        findings.append(Finding("project.manifest.revision", Verdict.OK,
                                f"boilerplate revision current "
                                f"({manifest.boilerplate_revision})",
                                project_id=pid))
    return findings


def check_marker_integrity(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    cid = "project.markers.integrity"
    pid = project.id
    agents = Path(project.workspace_dir or "") / "AGENTS.md"
    if not agents.exists():
        return [Finding(cid, Verdict.OK, "no AGENTS.md (nothing to bound)",
                        project_id=pid)]
    problem = _manifest.managed_marker_problem(agents.read_text(encoding="utf-8"))
    if problem:
        return [Finding(cid, Verdict.FAIL, f"AGENTS.md: {problem} — a re-onboard "
                        "cannot tell devclaw-owned from human-owned content",
                        remedy="fix the marker pair by PR, then onboard",
                        project_id=pid)]
    return [Finding(cid, Verdict.OK, "devclaw:managed markers well-formed",
                    project_id=pid)]


def check_scaffold_drift(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Diff the repo's committed-in-worktree ``.specify/`` scaffold against the
    packaged canonical source (the file-copied half of the boilerplate — the
    #610 silent-fork class, detectable per repo). Extra repo-local files are
    fine; a canonical file that is missing or differs is drift."""
    cid = "project.scaffold.drift"
    pid = project.id
    ws = Path(project.workspace_dir or "")
    dest = ws / ".specify"
    if not dest.is_dir():
        return [Finding(cid, Verdict.OK, "no .specify/ scaffold (not onboarded yet)",
                        project_id=pid)]
    from ..speckit_setup import _SCAFFOLD_DIRS, _SCAFFOLD_FILES, _speckit_source
    src = _speckit_source()
    if not src.is_dir():
        return [Finding(cid, Verdict.UNKNOWN,
                        f"vendored speckit source missing at {src}", project_id=pid)]
    drifted: list[str] = []
    for dirname in _SCAFFOLD_DIRS:
        s = src / dirname
        if not s.is_dir():
            continue
        for f in sorted(p for p in s.rglob("*") if p.is_file()):
            rel = f.relative_to(src)
            target = dest / rel
            if not target.exists() or target.read_bytes() != f.read_bytes():
                drifted.append(str(rel))
    for name in _SCAFFOLD_FILES:
        s = src / name
        if s.is_file():
            target = dest / name
            if not target.exists() or target.read_bytes() != s.read_bytes():
                drifted.append(name)
    if drifted:
        shown = ", ".join(drifted[:5]) + (f" (+{len(drifted) - 5} more)" if len(drifted) > 5 else "")
        return [Finding(cid, Verdict.WARN,
                        f".specify/ drifted from the packaged scaffold: {shown}",
                        remedy="onboard (re-onboard refreshes the scaffold via PR)",
                        project_id=pid)]
    return [Finding(cid, Verdict.OK, ".specify/ matches the packaged scaffold",
                    project_id=pid)]


def check_issue_refs_shape(ctx: "InstanceContext", project: "Project") -> list[Finding]:
    """Referenced-goal records parse (spec 019 US1): every goal on this
    project whose goal.yaml carries ``issue_refs`` must load, and the refs
    must be positive integers — a hand-edited or corrupted record would
    otherwise surface only as a mid-night dispatch crash."""
    cid = "project.goals.issue_refs"
    bad: list[str] = []
    for gid in ctx.goal_store.list_goal_ids():
        try:
            g = ctx.goal_store.load_goal(gid)
        except Exception as exc:  # noqa: BLE001 — the unparseable record IS the finding
            bad.append(f"{gid} (goal.yaml unreadable: {exc})")
            continue
        if g.project_id != project.id:
            continue
        if any(isinstance(n, bool) or not isinstance(n, int) or n <= 0 for n in g.issue_refs):
            bad.append(f"{gid} (issue_refs {g.issue_refs!r} not positive ints)")
    if bad:
        return [Finding(
            cid, Verdict.FAIL,
            "referenced-goal record(s) malformed: " + "; ".join(sorted(bad)[:5]),
            remedy="cancel + recreate the goal (goals are durable — no field patches)",
            project_id=project.id,
        )]
    return [Finding(cid, Verdict.OK, "all referenced-goal records parse", project_id=project.id)]


PROJECT_CHECKS: tuple = (
    check_workspace_preflight,
    check_dangling_links,
    check_unstamped_goals,
    check_manifest,
    check_marker_integrity,
    check_scaffold_drift,
    check_issue_refs_shape,
)
