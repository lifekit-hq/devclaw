"""Delivery strategy — how a goal's task work maps onto git branches + PRs.

Every goal accumulates its increments' commits on a shared ``goal/<id>``
branch — one cumulative PR per goal.

This stays a seam (a Protocol plus a resolver) rather than a hardcoded
``f"goal/{goal_id}"`` at four call sites, so a second topology has one place to
plug in. It owns ONLY the branch-selection decision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .store import GoalStore


class DeliveryStrategy(Protocol):
    """How one goal's task work maps to branches / PRs."""

    #: stable identifier for logs / telemetry
    name: str

    def goal_branch(self, goal_id: str) -> str:
        """The shared branch a goal's writes accumulate on — delivery reuses
        that branch's single PR across items.

        The read-only ``review_repository`` exclusion is deliberately NOT here:
        that's an action-level concern (a read-only action never writes, so it
        runs on the default branch whatever the strategy) applied by the caller.
        """
        ...


class GoalBranchStrategy:
    """Every increment's commits stack on ``goal/<id>``; one cumulative PR
    per goal — the strategy for every executing goal."""

    name = "goal-branch"

    def goal_branch(self, goal_id: str) -> str:
        return f"goal/{goal_id}"


#: stateless singleton — the strategy carries no per-goal state
GOAL_BRANCH: "DeliveryStrategy" = GoalBranchStrategy()


def resolve_strategy(store: "GoalStore", goal_id: str) -> "DeliveryStrategy":
    """The delivery strategy for a goal: ``goal-branch``, for every goal.

    The 2026-08-08 amnesia fix is why accumulation is the rule. The per-action
    strategy that used to live here reset the workspace to ``origin/main``
    before every task, and because the goal's single PR isn't merged, that
    wiped the prior increment's work and forced a from-scratch rebuild;
    ``goal/<id>`` accumulation preserves compounding progress.

    That strategy — and the auto-merge it was the selector for — were deleted
    in #641 after the spec 008 shrink made them unreachable (both modes stamp
    ``executing`` at creation, so nothing could resolve to per-action any
    more). Stays a function of the store rather than a constant because that is
    the seam a future topology decision plugs into. A pure read of goal state;
    no LLM call, no writer.
    """
    return GOAL_BRANCH
