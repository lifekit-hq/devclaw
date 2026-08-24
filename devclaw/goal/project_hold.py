"""The single-writer project hold — at most one goal works a project at a time
(spec 010 P1).

Two independent plans on one repository cannot be reconciled: sandbox and
worktree isolation stop *mechanical* collisions, but nothing stops two planners
that don't know about each other from drifting apart, and the drift only
surfaces at integration (#553 was one symptom — two goals allocating the same
``specs/009-…`` directory). So devclaw serializes whole plans per project.

**The hold is DERIVED, not stored** (FR-005, amended by owner ruling
2026-08-22). The holder of a project is a pure function of rows that already
exist: the first non-terminal goal on that project, ordered by age and
tie-broken on goal id. There is no acquire, no release, and no holder column.

That is the whole point. A stored lock has a state the derived form cannot
enter — a holder that dies, is force-cancelled, or is lost to a crash leaves a
lock nobody releases, which then needs a timeout, a heal budget, or an operator
unwedge verb. Here, a dead goal is either terminal (so the derivation stops
naming it) or non-terminal (so it still holds, correctly, and the operator
resumes or cancels it — exactly what FR-008 already prescribes for a blocked
holder). Nothing leaks, so nothing needs healing. It also adds no writer to a
layer whose single-writer discipline is the thing under test (constitution IV).

Everything here is read-only, cheap, and never raises: it runs before any
cognition on the dispatch path, and a hiccup must degrade to "no hold" —
today's behaviour — rather than wedge the heartbeat.
"""

from __future__ import annotations

from .models import Goal

#: A goal is a candidate holder unless it has reached a terminal phase. Blocked,
#: idle, in-flight and verifying goals ALL still hold their project: a blocked
#: holder keeping the project is the clarify ruling behind FR-008 — releasing it
#: would let a second goal plan against a repo whose unmerged spec directories
#: are invisible on the holder's branch, re-opening the #553 class.
TERMINAL_PHASES = frozenset({"done", "cancelled"})


def scope_key(goal: Goal) -> "str | None":
    """The project a goal contends for, or ``None`` when it contends for
    nothing.

    ``project_id`` is the registered reference key (#524 P3) and wins when set.
    Goals predating it — and self-fix goals with no registered project — fall
    back to the workspace path, which is what actually collides. A goal with
    neither is never queued: there is no shared repository to serialize.

    A ``qa`` goal (spec 015 US3) contends for nothing: its validation runs are
    read-only toward the repo and execute in the qa goal's own workspace, so
    it must neither hold the project's single-writer slot nor be blocked by
    it."""
    if goal.mode == "qa":
        return None
    pid = (goal.project_id or "").strip()
    if pid:
        return pid
    ws = (goal.workspace_dir or "").strip().rstrip("/")
    return ws or None


def is_terminal(status: object) -> bool:
    """Whether a goal status has reached a terminal phase. Defensive about the
    attribute so a partially-loaded status can never wedge the sweep."""
    return str(getattr(status, "phase", "") or "") in TERMINAL_PHASES


def holder_map(store) -> "dict[str, str]":
    """``scope_key -> holding goal_id`` across the whole fleet.

    Computed ONCE per heartbeat sweep and threaded into each tick: it reads
    every goal, so making it per-goal would turn one sweep into an N² scan.

    Ordering is age ascending, tie-broken on goal id. Goals carry no priority
    field today, so FR-003's "priority band, then oldest" reduces to age; the id
    tie-break is what keeps the holder deterministic instead of dependent on
    which goal happened to be read first.

    Failure policy — deliberately narrow. A goal whose ``goal.yaml`` will not
    load is skipped: that is an expected, isolated condition with precedent
    (tick_all already applies the same rule to its per-goal resolvers), and one
    corrupt file must not sink the sweep.

    Everything else is allowed to RAISE. An earlier draft wrapped the store
    reads in a blanket ``except`` that degraded to an empty map — and an empty
    map does not mean "be careful", it means "nothing is held", so every goal
    dispatches and the single-writer invariant silently switches itself off.
    That swallow immediately hid a real bug (this function queried a column that
    does not exist, and the fleet quietly fell back to id-ordering). A broken
    read of a core table is a bug to surface, never a reason to ship the
    unguarded behaviour (constitution VI)."""
    created = store.goal_created_at_map()
    candidates: "dict[str, list[tuple[int, str]]]" = {}
    for goal_id in store.list_goal_ids():
        try:
            status = store.load_status(goal_id)
            if is_terminal(status):
                continue
            scope = scope_key(store.load_goal(goal_id))
        except Exception:  # noqa: BLE001 — a bad goal.yaml must not sink the sweep
            continue
        if scope is None:
            continue
        # Absent creation time sorts LAST, not first: a goal we cannot date must
        # never displace one we can as holder.
        candidates.setdefault(scope, []).append((created.get(goal_id, 1 << 62), goal_id))
    return {scope: min(entries)[1] for scope, entries in candidates.items()}


def waiting_reason(holder_id: str) -> str:
    """The operator-facing explanation on a queued goal's own status surface.

    Derived at read time rather than persisted — for the same reason the hold
    itself is (FR-005 as amended): a stored copy of a derived fact can disagree
    with it, and the disagreement is the wedge. Deriving also keeps a queued
    tick at zero writes, not merely zero tokens."""
    return (
        f"queued — goal {holder_id} is working this project; "
        "this goal starts automatically when that one finishes"
    )
