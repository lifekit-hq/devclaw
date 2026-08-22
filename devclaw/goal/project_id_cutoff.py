"""One migration, one cutoff — the queue-half's only real migration (#616).

:mod:`devclaw.goal.store.legacy_cutoff` retired the goal-layer row shapes. This
is the one site the queue half of the same sweep found that was genuinely a
*migration* rather than stale vocabulary: the ``project_id`` backfill (#524 P3).

It was the same class the whole tranche is about, in its purest form — **a
migration with no cutoff**. It ran on EVERY boot, re-scanning every goal on
disk, kept honest only by its own idempotency, and it was the sole surviving
caller of the workspace-path→project match that #524 P3 replaced with an
id-keyed join. A migration that never ends is not a migration, it is a second
resolution path the next reader has to reason about.

**The cutoff is 2026-08-22** — the same date the goal half stamps, because it is
the same sweep. The backfill runs ONCE per database and then never again: after
it, every goal that could be matched to a project carries its ``project_id``,
and a goal created later gets one at creation. A goal whose workspace matches no
registered project is *correctly* project-less; re-running the scan for it every
boot never produced a different answer.

Crash-safety follows the shape :mod:`devclaw.goal.store.view_migration`
established: the sweep is idempotent on its own, and the marker is stamped only
after it completes, so a crash part-way through resumes rather than half-applying.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..project_registry import ProjectRegistry
    from ..state_store import StateStore
    from .store import GoalStore

#: Stamped in ``meta`` once the backfill completes; its presence makes it one-shot.
CUTOFF_META_KEY = "goal_project_id_backfill_done_at_ms"

#: The date goals are supported back to. A goal written before it is migrated by
#: the sweep below, once; after that there is no un-stamped shape left to read.
CUTOFF_DATE = "2026-08-22"


def backfill_project_ids_once(
    state: "StateStore",
    goal_store: "GoalStore",
    registry: "ProjectRegistry | None",
    now_ms: int,
) -> int:
    """Stamp ``project_id`` onto goals written before the field existed, ONCE.

    Resolves each goal's owning project by workspace path
    (``find_by_workspace_dir`` — this is its sole surviving caller; the runtime
    joins are all id-keyed now). Without the stamp, a long-lived goal in flight
    at deploy time would lose its owning project's pinned knobs
    (automerge/verify_done/autodeploy) and fall to the devclaw-wide defaults.

    Zero-token and zero-cognition. Returns the count stamped — 0 on every call
    after the first, and 0 (without stamping) when there is no registry to
    resolve against, since that is "could not run", not "ran and found nothing".
    A corrupt ``goal.yaml`` is skipped; it never blocks startup.
    """
    if registry is None:
        return 0
    if state.get_meta(CUTOFF_META_KEY):
        return 0
    stamped = 0
    for gid in goal_store.list_goal_ids():
        try:
            g = goal_store.load_goal(gid)
        except Exception:
            continue  # a half-written / corrupt goal.yaml never blocks startup
        if g.project_id:
            continue
        project = registry.find_by_workspace_dir(g.workspace_dir)
        if project is not None:
            goal_store.set_project_id(gid, project.id)
            stamped += 1
    state.set_meta(CUTOFF_META_KEY, str(int(now_ms)))
    return stamped
