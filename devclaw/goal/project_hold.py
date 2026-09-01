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
    candidates: "dict[str, list[tuple[int, int, str]]]" = {}
    for goal_id in store.list_goal_ids():
        try:
            status = store.load_status(goal_id)
            if is_terminal(status):
                continue
            # Skip-over (spec 025 FR-015): a blocked goal is not a candidate —
            # the queued successor takes the lane instead of idling behind a
            # park that only a human can clear.
            if str(getattr(status, "phase", "") or "") == "blocked":
                continue
            goal = store.load_goal(goal_id)
            scope = scope_key(goal)
        except Exception:  # noqa: BLE001 — a bad goal.yaml must not sink the sweep
            continue
        if scope is None:
            continue
        # Runnable-head rule (owner ruling 2026-09-01, generalizing spec 025's
        # blocked skip-over): a goal that CANNOT act this sweep is not a
        # candidate either. Head-of-line blocking is a bug, not a policy — on
        # 2026-08-31 one idle head waiting out its 1d re-plan cadence stranded
        # 7 runnable successors for a whole night. A head with work in flight
        # always holds (single-writer); a head owing only its merge needs no
        # lane at all (the pending-merge finalize runs BEFORE the hold gate by
        # design); an idle head is runnable only when it has unread steering
        # or a due cadence — the same cheap reads the tick's own should_plan
        # gate uses, so this stays zero-token and zero-write. The head
        # reclaims the lane the moment it is runnable again and no successor
        # has work in flight. Deliberately OUTSIDE the try above: these are
        # core-table reads (module failure policy) — swallowing a broken one
        # would silently thin candidacy, which is how single-writer switches
        # itself off. (scope is None already skipped qa goals, whose empty
        # cadence never parses.)
        if status.in_flight is None and (
            status.pending_merge_pr
            or (
                not store.unread_steering_rows(goal_id)
                and not store.cadence_due(goal, status)
            )
        ):
            continue
        # A goal with WORK IN FLIGHT outranks age: when a parked predecessor
        # is resumed while its skip-over successor is mid-task, the successor
        # keeps the lane until its task settles — otherwise the older resumed
        # goal would reclaim holdership and dispatch a second writer against
        # a workspace with a live task in it (the #553/#722 class).
        # Absent creation time sorts LAST, not first: a goal we cannot date must
        # never displace one we can as holder.
        candidates.setdefault(scope, []).append((
            0 if status.in_flight else 1,
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
