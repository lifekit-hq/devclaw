"""Loop health — the ONE definition of "why is the loop not running" (spec 038).

Pure and store-free (a leaf, like ``dispatch_gate``): the cause vocabulary,
the responsibility bucket each cause derives to (FR-005a — derived, never
stored), the deterministic cause derivation the heartbeat runs once per sweep
(FR-004/FR-004a), and the rate math every read surface shares. Domain:
STATE (what the loop was doing) and MONEY (what the owner is paying for an
idle instance) — measurement only; nothing here gates, delays or re-shapes a
dispatch (FR-026 applies to this whole feature).

Absence is never zero: ``not_stuck_rate`` returns ``None`` over an empty
window, and an ``unobserved`` span (a heartbeat gap) is excluded from every
denominator rather than attributed to whatever the restart happened to see.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

# ---- vocabulary ---------------------------------------------------------------

#: the loop did work this interval (a dispatch, a running action, a close)
WORKING = "working"
#: a heartbeat gap longer than the tolerance — attributed to nobody
UNOBSERVED = "unobserved"

#: FR-003 — the previously unnamed "stopped because there is nothing to do"
NOTHING_TO_DO: tuple[str, ...] = (
    "empty_backlog", "no_goal_armed", "all_planned_done", "window_closed", "paused",
)
#: plan addition (research D3): the owner's deliberate stop is the owner's turn
OPERATOR_HOLD = "operator_hold"

#: the existing human-gated block kinds (verbatim, FR-002)
_OWNER_BLOCK_KINDS: frozenset[str] = frozenset({"needs_answer"})
#: the existing devclaw-caused block kinds (verbatim, FR-002); every
#: ``mechanical:*`` kind is devclaw-caused by prefix
_DEVCLAW_BLOCK_KINDS: frozenset[str] = frozenset(
    {"bug", "lost_ref", "dispatch_cap", "donegate_churn"}
)

#: outcomes (``goal.tick.Outcome`` values) that mean the loop was working
WORKING_OUTCOMES: frozenset[str] = frozenset(
    {"dispatched", "verifying", "in_flight", "done", "conflict"}
)

BUCKET_WORKING = "working"
BUCKET_DEVCLAW = "devclaw"
BUCKET_OWNER = "owner"
BUCKET_NO_WORK = "no_work"
BUCKET_UNOBSERVED = "unobserved"
#: the three responsibility buckets of FR-005 (working and unobserved sit outside)
RESPONSIBILITY_BUCKETS: tuple[str, ...] = (BUCKET_DEVCLAW, BUCKET_OWNER, BUCKET_NO_WORK)


def bucket_for(cause: str) -> str:
    """The responsibility bucket a cause derives to (FR-005 / FR-005a). Total:
    an unknown string is devclaw's until it is named — loud, never lenient."""
    c = (cause or "").strip()
    if c == WORKING:
        return BUCKET_WORKING
    if c == UNOBSERVED:
        return BUCKET_UNOBSERVED
    if c in NOTHING_TO_DO and c != "no_goal_armed":
        return BUCKET_NO_WORK
    if c == "no_goal_armed" or c == OPERATOR_HOLD or c in _OWNER_BLOCK_KINDS:
        return BUCKET_OWNER
    return BUCKET_DEVCLAW


# ---- derivation (one call per heartbeat sweep) --------------------------------


@dataclass(frozen=True)
class GoalView:
    """What the derivation needs to know about one goal — a projection of
    ``GoalStatus`` so the leaf never imports the goal layer."""

    goal_id: str
    terminal: bool
    phase: str
    blocked_kind: str = ""
    outcome: str = ""
    window_closed: bool = False


def derive_loop_cause(
    *,
    goals: Iterable[GoalView],
    pause_active: bool,
    pause_reason: str = "",
    operator_hold: bool,
    hold_reason: str = "",
    window_closed: bool,
    window_reason: str = "",
    unarmed_ready_issues: int = 0,
) -> tuple[str, str]:
    """The single cause for the interval that just elapsed, plus a display
    detail (research D3 precedence). Deterministic: the same inputs always
    give the same cause, and ties between blocked goals resolve by goal id."""
    views = list(goals)
    for v in sorted(views, key=lambda g: g.goal_id):
        if v.outcome in WORKING_OUTCOMES:
            return WORKING, v.goal_id
    if pause_active:
        return "paused", pause_reason[:120]
    if operator_hold:
        return OPERATOR_HOLD, hold_reason[:120]
    if window_closed:
        return "window_closed", window_reason[:120]
    if not views:
        return "empty_backlog", ""
    live = [v for v in views if not v.terminal]
    if not live:
        if unarmed_ready_issues > 0:
            return "no_goal_armed", f"{unarmed_ready_issues} graded-ready issue(s) without a goal"
        return "all_planned_done", ""
    blocked = [v for v in live if v.phase == "blocked"]
    if blocked:
        def _rank(v: GoalView) -> tuple[int, str]:
            kind = v.blocked_kind or "needs_answer"
            # a wedge outranks a wait — devclaw-caused first, then owner
            return (0 if bucket_for(kind) == BUCKET_DEVCLAW else 1, v.goal_id)
        top = sorted(blocked, key=_rank)[0]
        return (top.blocked_kind or "needs_answer"), top.goal_id
    for v in sorted(live, key=lambda g: g.goal_id):
        if v.outcome == "error":
            return "bug", v.goal_id
    if all(v.window_closed for v in live):
        return "window_closed", "every live goal is outside its own run window"
    return "all_planned_done", ""


# ---- rate math (shared by every read surface) ---------------------------------


def not_stuck_rate(bucket_seconds: Mapping[str, float], working_seconds: float) -> Optional[float]:
    """Fraction of OBSERVED time spent working or idle for a non-devclaw
    reason (FR-005b). ``None`` when nothing was observed — never 100%."""
    devclaw = float(bucket_seconds.get(BUCKET_DEVCLAW, 0) or 0)
    owner = float(bucket_seconds.get(BUCKET_OWNER, 0) or 0)
    no_work = float(bucket_seconds.get(BUCKET_NO_WORK, 0) or 0)
    observed = working_seconds + devclaw + owner + no_work
    if observed <= 0:
        return None
    return round((working_seconds + owner + no_work) / observed, 4)


def clip_span(start_ms: int, end_ms: int, since_ms: int, until_ms: int) -> int:
    """Milliseconds of ``[start, end]`` that fall inside ``[since, until]``."""
    lo = max(int(start_ms), int(since_ms))
    hi = min(int(end_ms), int(until_ms))
    return max(0, hi - lo)
