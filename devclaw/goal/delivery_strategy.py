"""Delivery strategy — how a goal's task work maps onto git branches + PRs.

Every goal accumulates its increments' commits on a shared ``goal/<id>``
branch — one cumulative PR per goal.

It is the seam a second topology (per-task PRs to main) plugs into later,
instead of threading a new conditional through every call site. TODAY it owns
ONLY the branch-selection decision.

Auto-merge eligibility keys off this strategy at its call site
(tick_settle): a goal-branch delivery's cumulative PR stays open for the
done-gate; only a per-action delivery auto-merges.

Resolution mirrors :func:`devclaw.goal.merge.resolve_automerge`: a pure function
of goal state, trivially unit-testable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Protocol

if TYPE_CHECKING:
    from .store import GoalStore


class DeliveryStrategy(Protocol):
    """How one goal's task work maps to branches / PRs."""

    #: stable identifier for logs / telemetry
    name: str

    def goal_branch(self, goal_id: str) -> Optional[str]:
        """The shared branch a goal's writes accumulate on (delivery reuses that
        branch's single PR across items), or ``None`` when each action delivers
        its own branch + PR off the default branch.

        The read-only ``review_repository`` exclusion is deliberately NOT here:
        that's an action-level concern (a read-only action never writes, so it
        runs on the default branch whatever the strategy) applied by the caller.
        """
        ...


class GoalBranchStrategy:
    """Every increment's commits stack on ``goal/<id>``; one cumulative PR
    per goal — the default for every executing goal."""

    name = "goal-branch"

    def goal_branch(self, goal_id: str) -> Optional[str]:
        return f"goal/{goal_id}"


class PerActionStrategy:
    """Each action delivers its own branch + PR off the default branch; no
    shared goal branch — the second topology this seam exists for.

    **Not currently selected by anything.** See :func:`resolve_strategy`."""

    name = "per-action"

    def goal_branch(self, goal_id: str) -> Optional[str]:
        return None


#: stateless singletons — the strategies carry no per-goal state
GOAL_BRANCH: "DeliveryStrategy" = GoalBranchStrategy()
PER_ACTION: "DeliveryStrategy" = PerActionStrategy()


def resolve_strategy(store: "GoalStore", goal_id: str) -> "DeliveryStrategy":
    """The delivery strategy for a goal: ``goal-branch``, for every goal.

    The 2026-08-08 amnesia fix is why accumulation is the rule. A per-action
    strategy resets the workspace to ``origin/main`` before every task, and
    because the goal's single PR isn't merged, that wipes the prior
    increment's work and forces a from-scratch rebuild; ``goal/<id>``
    accumulation preserves compounding progress.

    **What #616 changed, and the live question it exposes.** This used to
    return :data:`PER_ACTION` for a goal whose ``lifecycle`` was NULL — i.e.
    one created before the column existed. Both modes have stamped
    ``executing`` at creation since the spec 008 shrink, so in production that
    branch stopped being reachable then; the cutoff migrated the last rows
    that could take it, and the selection rule is gone.

    The consequence is worth stating plainly rather than leaving to be
    rediscovered: **auto-merge has therefore been unreachable in production
    since the 008 shrink.** ``tick_settle`` skips merging for any goal-branch
    delivery (the cumulative PR must stay open for the done-gate, #486), and
    every goal is now goal-branch. The tests that appeared to cover
    auto-merging were reaching it by seeding a goal with no lifecycle — a
    shape production had already stopped producing. They now select
    :data:`PER_ACTION` explicitly instead, so they still exercise the merge
    machinery without implying the loop can get there on its own.

    The machinery is deliberately KEPT, not deleted: whether devclaw should
    regain a per-action topology (and with it auto-merge) is a design
    decision, not demolition, and #611 explicitly puts behavior change out of
    scope. This stays a function of the store rather than a constant because
    it is the seam that decision plugs into. A pure read of goal state; no LLM
    call, no writer.
    """
    return GOAL_BRANCH
