"""The single-writer project hold — at most one goal works a project at a time
(spec 010 P1).

Two independent plans on one repository cannot be reconciled: sandbox and
worktree isolation stop *mechanical* collisions, but nothing stops two planners
that don't know about each other from drifting apart, and the drift only
surfaces at integration (#553 was one symptom — two goals allocating the same
``specs/009-…`` directory). So devclaw serializes whole plans per project.

**The hold is DERIVED, not stored** (FR-005, amended by owner ruling
2026-08-22). The holder of a project is a pure function of rows that already
exist: the first goal on that project that could actually act this sweep
(work in flight, unread steering, or a due cadence — blocked and merge-owing
goals excepted), ordered by age with in-flight work outranking it, tie-broken
on goal id. There is no acquire, no release, and no holder column. The
"could actually act" clause is the runnable-head rule (owner ruling
2026-09-01): head-of-line blocking is a bug, not a policy — see holder_map.

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

from . import decisions as _decisions
from .models import Goal

#: A goal is a candidate holder unless it has reached a terminal phase.
#:
#: BLOCKED goals are additionally skipped as candidates (spec 025 FR-015,
#: ruled by Denys 2026-08-29 — reversing spec 010 FR-008's blocked-holder
#: ruling): a parked goal must not idle its whole project lane, the queued
#: successor starts instead ("skip-over"). The risk FR-008 named — a
#: successor planning against a repo missing the parked goal's unmerged work
#: — is accepted deliberately: goals are filed independent of one another,
#: and a successor that did depend on the parked work fails its own
#: done-gate loudly rather than shipping wrong. (The 2026-08-28 night was
#: the evidence: one OOM-blocked goal idled the devclaw lane for 14 hours
#: while a healthy successor sat queued behind it.)
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


#: A goal's next move, as the lane sees it — ONE answer to "what can this
#: goal do on its next tick", read by BOTH the holder derivation below and the
#: tick's hold gate + plan gate (tinyspec ``one-definition-of-runnable``,
#: 2026-09-08). Before it, the two consumers each carried their own inline
#: definition and spec 041 updated one of them: a goal whose only move was the
#: owner's ``accept_close`` was (rightly) no lane candidate here, yet the tick
#: queued it behind the holder all evening; and a goal whose only move was a
#: dispatching Decision was work for the tick but no candidate here — two such
#: goals on one project both dispatched in one sweep, the #553/#722 class.
MOVE_NONE = "none"              # nothing to do
MOVE_HEAL = "heal"              # a mechanical:ci hold owing a done proposal — its heal re-drives the gate
MOVE_LANE_FREE = "lane_free"    # a close on mechanical facts (merge retry, owner accept_close): no checkout, no lane
MOVE_LANE = "lane"              # a dispatch into the project's checkout
MOVE_IN_FLIGHT = "in_flight"    # work already running — holds, outranks age

#: The moves that make a goal a holder candidate.
HOLDING_MOVES = frozenset({MOVE_IN_FLIGHT, MOVE_HEAL, MOVE_LANE})
#: The moves the tick plans on (everything the advance handler does past its
#: gates); ``heal`` acts through the blocked-phase heal, never through a plan.
PLANNING_MOVES = frozenset({MOVE_LANE, MOVE_LANE_FREE})


def waits_for_lane(move: str) -> bool:
    """Whether a goal with this move is queued behind another holder. Only a
    lane-free move passes: it touches no checkout, so a successor mid-task is
    no reason to hold a decided close (spec 041 FR-003 says "before any
    cadence or work gate"; the lane gate is one of those)."""
    return move != MOVE_LANE_FREE


def next_move(goal: Goal, status: object, store, *, settled: bool = False) -> str:
    """The goal's next move. Pure reads on rows the CAS'd transition discipline
    already governs — steering, Decisions, cadence — zero cognition, and the
    same reads the tick performs right after its gate, so a queued tick costs
    what it cost before. ``settled`` is the tick's "a task just settled on
    this tick" (its ``finished_detail``); the sweep never sets it, because an
    unsettled task is ``in_flight``.

    Mirrors the tick's advance handler branch for branch, in its order:
    a blocked goal moves only on a settle or HUMAN steering (machine rows —
    the churn brake's own corrections — must not un-park it), except that a
    ``mechanical:ci`` hold owing a done proposal HOLDS the lane for the heal
    that re-drives its gate (2026-09-06: dropping it handed the lane to a
    successor for the very sweep the hold cleared). Past the block: a settle
    retries (lane); a merge retry is lane-free; a held done proposal
    re-drives the gate (lane) unless the owner's ``accept_close`` stands, in
    which case it finalizes on mechanical facts (lane-free); unread steering
    dispatches first (lane); the owner's standing ``accept_close`` closes
    (lane-free); a pending Decision is work (lane); a due cadence advances
    (lane)."""
    if is_terminal(status):
        return MOVE_NONE
    if getattr(status, "in_flight", None) is not None:
        return MOVE_IN_FLIGHT
    goal_id = goal.id
    if str(getattr(status, "phase", "") or "") == "blocked":
        if settled or store.has_unread_human_steering(goal_id):
            return MOVE_LANE
        if (
            str(getattr(status, "blocked_kind", "") or "") == "mechanical:ci"
            and getattr(status, "pending_done_proposal", False)
        ):
            return MOVE_HEAL
        return MOVE_NONE
    if settled:
        return MOVE_LANE
    if getattr(status, "pending_merge_pr", ""):
        return MOVE_LANE_FREE
    rows = store.decisions(goal_id)
    accepted = _decisions.accepted_close(rows) is not None
    if getattr(status, "pending_done_proposal", False):
        # The ci-settled re-drive, EXCEPT when the owner's accept_close
        # stands: the tick then finalizes on mechanical facts alone (spec
        # 041 FR-004) — a close, not a gate dispatch, so it needs no lane.
        return MOVE_LANE_FREE if accepted else MOVE_LANE
    if store.unread_steering_rows(goal_id):
        return MOVE_LANE
    if accepted:
        return MOVE_LANE_FREE
    if _decisions.pending_since(rows, getattr(status, "last_plan_at", None)):
        return MOVE_LANE
    if store.cadence_due(goal, status):
        return MOVE_LANE
    return MOVE_NONE


def holder_map(store) -> "dict[str, str]":
    """``scope_key -> holding goal_id`` across the whole fleet.

    Computed ONCE per heartbeat sweep and threaded into each tick: it reads
    every goal, so making it per-goal would turn one sweep into an N² scan.

    A candidate is a goal whose :func:`next_move` is a holding move. Ordering
    is in-flight first, then age ascending, tie-broken on goal id: when a
    parked predecessor is resumed while its skip-over successor is mid-task,
    the successor keeps the lane until its task settles — otherwise the older
    resumed goal would reclaim holdership and dispatch a second writer against
    a workspace with a live task in it (the #553/#722 class). Goals carry no
    priority field today, so FR-003's "priority band, then oldest" reduces to
    age; the id tie-break keeps the holder deterministic instead of dependent
    on which goal happened to be read first. Absent creation time sorts LAST,
    not first: a goal we cannot date must never displace one we can.

    Skip-over (spec 025 FR-015) and the runnable-head rule (owner ruling
    2026-09-01) both live in :func:`next_move` now: a blocked goal, a goal
    owing only its merge or its accepted close, and an idle goal with nothing
    to do are not candidates — head-of-line blocking is a bug, not a policy.

    Failure policy — deliberately narrow. A goal whose ``goal.yaml`` will not
    load is skipped: that is an expected, isolated condition with precedent
    (tick_all already applies the same rule to its per-goal resolvers), and one
    corrupt file must not sink the sweep.

    Everything else is allowed to RAISE — :func:`next_move` runs OUTSIDE the
    try on purpose. An earlier draft wrapped the store reads in a blanket
    ``except`` that degraded to an empty map — and an empty map does not mean
    "be careful", it means "nothing is held", so every goal dispatches and the
    single-writer invariant silently switches itself off. That swallow
    immediately hid a real bug (this function queried a column that does not
    exist, and the fleet quietly fell back to id-ordering). A broken read of a
    core table is a bug to surface, never a reason to ship the unguarded
    behaviour (constitution VI)."""
    created = store.goal_created_at_map()
    candidates: "dict[str, list[tuple[int, int, str]]]" = {}
    for goal_id in store.list_goal_ids():
        try:
            status = store.load_status(goal_id)
            if is_terminal(status):
                continue
            goal = store.load_goal(goal_id)
            scope = scope_key(goal)
        except Exception:  # noqa: BLE001 — a bad goal.yaml must not sink the sweep
            continue
        if scope is None:
            continue
        move = next_move(goal, status, store)
        if move not in HOLDING_MOVES:
            continue
        candidates.setdefault(scope, []).append((
            0 if move == MOVE_IN_FLIGHT else 1,
            created.get(goal_id, 1 << 62),
            goal_id,
        ))
    return {scope: min(entries)[2] for scope, entries in candidates.items()}


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
