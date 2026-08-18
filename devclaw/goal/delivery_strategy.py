"""Delivery strategy — how a goal's task work maps onto git branches + PRs.

An executing goal accumulates every increment's commits on a shared
``goal/<id>`` branch (one cumulative PR per goal); a legacy goal with no
recorded lifecycle delivers each action as its own branch + PR off the
default branch.

It is the seam a second topology (per-task PRs to main) plugs into later,
instead of threading a new conditional through every call site. TODAY it owns
ONLY the branch-selection decision.

Auto-merge eligibility (the tick still keys off ``bool(addresses)``) and the
scheduler dep-gate stay at their call sites on purpose: they carry latent
per-action-vs-per-goal signal mismatches that must be reconciled *deliberately*
in the PR that actually changes their behaviour — not laundered through a
"pure refactor".

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
    shared goal branch. Legacy goals (no recorded lifecycle) only."""

    name = "per-action"

    def goal_branch(self, goal_id: str) -> Optional[str]:
        return None


#: stateless singletons — the strategies carry no per-goal state
GOAL_BRANCH: "DeliveryStrategy" = GoalBranchStrategy()
PER_ACTION: "DeliveryStrategy" = PerActionStrategy()


def resolve_strategy(store: "GoalStore", goal_id: str) -> "DeliveryStrategy":
    """The delivery strategy for a goal:

    * ``goal-branch`` for any goal in the **explicit** ``executing`` lifecycle
      — both modes stamp it at creation now (spec 008 shrink: one execution
      path). The 2026-08-08 amnesia fix is the reason accumulation is the
      default: a per-action strategy resets the workspace to ``origin/main``
      before every task, and because the goal's single PR isn't merged that
      wipes the prior increment's work and forces a from-scratch rebuild —
      ``goal/<id>`` accumulation preserves compounding progress.
    * ``per-action`` only for legacy goals with no lifecycle recorded
      (``lifecycle is None``/pre-shrink strings read as executing elsewhere,
      but delivery stays per-action for them, unchanged — the tick's heal
      re-stamps a pre-shrink row on first touch, after which it accumulates).

    (The old ``read_checklist is not None`` gate died with the checklist —
    the checklist WAS the goal-branch signal; the explicit lifecycle string
    is the signal now.) A pure read of goal state; no LLM call, no writer.
    """
    status = store.load_status(goal_id)
    if status.lifecycle == "executing":
        return GOAL_BRANCH
    return PER_ACTION
