"""devclaw doctor — read-only, zero-LLM instance + per-project diagnostics.

Codifies the post-redeploy checklist as named checks (spec 016). Doctor is
operator-invoked ONLY (MCP tool / CLI) — it never hooks the heartbeat tick,
never mutates state, and never makes a cognition call. Remedies are named
existing verbs; doctor executes none of them.

Check registry pattern: a check is a function returning ``list[Finding]``,
registered in the ordered ``INSTANCE_CHECKS`` / ``PROJECT_CHECKS`` tuples.
Convention (spec 016 FR-014): a PR that changes persisted state shape or
in-repo boilerplate ships its doctor check here, exactly as a behavior-change
PR ships its named regression test — with a seeded-fault test per check.

The facade wraps every check: a crash yields an ``unknown`` finding carrying
the error — a check is never silently omitted (FR-005), and a fully healthy
instance is reported affirmatively.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Optional

from .checks_instance import INSTANCE_CHECKS
from .checks_project import PROJECT_CHECKS
from .context import InstanceContext
from .model import DoctorReport, Finding, Verdict

if TYPE_CHECKING:  # pragma: no cover
    from ..goal.store import GoalStore
    from ..project_registry import Project, ProjectRegistry
    from ..state_store import StateStore

__all__ = ["DoctorReport", "Finding", "Verdict", "run_doctor"]


def _check_id_of(fn: Callable) -> str:
    # checks name their ids inside the findings; for crash reporting derive a
    # stable id from the function name (check_foo_bar -> foo_bar).
    return fn.__name__.removeprefix("check_")


def _run_one(fn: Callable, *args) -> list[Finding]:
    project = args[1] if len(args) > 1 else None
    project_id = getattr(project, "id", None)
    try:
        findings = fn(*args)
    except Exception as exc:  # noqa: BLE001 — FR-005: a crashed check is loud, never omitted
        return [Finding(
            f"{'project' if project_id else 'instance'}.{_check_id_of(fn)}",
            Verdict.UNKNOWN,
            f"check crashed: {exc!r}",
            project_id=project_id,
        )]
    if not findings:
        return [Finding(
            f"{'project' if project_id else 'instance'}.{_check_id_of(fn)}",
            Verdict.UNKNOWN,
            "check returned no finding (a check must always report)",
            project_id=project_id,
        )]
    return findings


def run_doctor(
    store: "StateStore",
    goal_store: "GoalStore",
    registry: "ProjectRegistry",
    *,
    project_id: Optional[str] = None,
) -> DoctorReport:
    """Run every check and return the report. Read-only by construction."""
    findings: list[Finding] = []
    ctx = InstanceContext(store=store, goal_store=goal_store, registry=registry)
    # Preload goal facts once; a goal whose yaml can't load becomes a finding,
    # never a crash of the whole run.
    for gid in sorted(goal_store.list_goal_ids()):
        try:
            ctx.goals.append(goal_store.load_goal(gid))
        except Exception as exc:  # noqa: BLE001
            findings.append(Finding(
                "instance.goals.readable", Verdict.FAIL,
                f"goal {gid!r} facts unreadable: {exc!r}",
                remedy="inspect the goal.yaml (corrupt state blocks legibly, #185)",
            ))

    for check in INSTANCE_CHECKS:
        findings.extend(_run_one(check, ctx))

    projects: "list[Project]" = sorted(registry.list(), key=lambda p: p.id)
    if project_id is not None:
        projects = [p for p in projects if p.id == project_id]
    for project in projects:
        for check in PROJECT_CHECKS:
            findings.extend(_run_one(check, ctx, project))

    return DoctorReport(findings=findings)
