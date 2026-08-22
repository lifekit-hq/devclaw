"""Unit tests for the delivery-strategy seam — branch selection only.

Every goal accumulates its increments on ``goal/<id>``. The seam used to have
a second answer, ``per-action``, selected by a goal whose ``lifecycle`` was
NULL — i.e. one created before the column existed. Both modes have stamped
``executing`` at creation since the spec 008 shrink, so that branch stopped
being reachable then; the #616 cutoff migrated the last rows that could take
it and deleted the selection rule.

``PER_ACTION`` itself is deliberately KEPT as the named second topology —
whether devclaw should regain it (and with it auto-merge, which only fires
for a per-action delivery) is a design decision, not demolition.
"""

from dataclasses import dataclass

from devclaw.goal import delivery_strategy as ds


@dataclass
class _FakeStatus:
    lifecycle: "str | None" = None


class _FakeStore:
    """Minimal stand-in exposing the ``load_status`` read resolve_strategy
    performs — the whole decision is a pure function of the stored lifecycle."""

    def __init__(self, lifecycle=None):
        self._status = _FakeStatus(lifecycle=lifecycle)

    def load_status(self, goal_id):
        return self._status


def test_executing_lifecycle_resolves_goal_branch():
    # THE amnesia fix (2026-08-08): an executing goal accumulates its
    # increments on ``goal/<id>`` so the per-task reset-to-main never wipes
    # prior work. Since spec 008 shrink this is the path for BOTH modes.
    store = _FakeStore(lifecycle="executing")
    strat = ds.resolve_strategy(store, "g1")
    assert strat is ds.GOAL_BRANCH
    assert strat.goal_branch("g1") == "goal/g1"


def test_no_stored_lifecycle_value_can_select_per_action_delivery():
    """#616 regression. A NULL lifecycle used to resolve to PER_ACTION, and a
    pre-shrink "investigating"/"firming" string did too. Those rows are gone
    (the cutoff migrated them) and so is the rule — but the rule is the part
    that matters: resurrecting per-action must be a deliberate change to this
    function, never an accident of a row shape nobody expected.

    Every value, including one from a history nobody documented, resolves to
    goal-branch."""
    for lc in (None, "investigating", "firming", "executing", "some-forgotten-phase"):
        assert ds.resolve_strategy(_FakeStore(lifecycle=lc), "g1") is ds.GOAL_BRANCH


def test_per_action_remains_available_as_the_second_topology():
    """It is unselected, not deleted: auto-merge keys off ``goal_branch(...)
    is None``, so removing the class would silently delete that subsystem's
    only reachable trigger along with it."""
    assert ds.PER_ACTION.goal_branch("g1") is None
    assert ds.PER_ACTION.name == "per-action"


def test_fresh_goals_of_both_modes_resolve_goal_branch_delivery(tmp_path):
    # Regression (live-found: ledger night 1, 2026-08-10). create_goal(mode=
    # "long_lived") once persisted NO lifecycle — resolve_strategy's
    # deliberately-explicit ``executing`` requirement then downgraded every
    # FRESH goal to per-action delivery: reset-to-main on each dispatch,
    # unmerged scaffold PRs, main never moved — the exact amnesia #486 was
    # written to kill. Creation must stamp the lifecycle it claims, end to
    # end through the real service + store — for BOTH modes now (spec 008
    # shrink: one execution path).
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    store = StateStore(str(tmp_path / "t.db"))
    queue = TaskQueue(store)
    cfg = GoalConfig(
        goals_dir=tmp_path / "goals",
        notify_url="",
        tick_seconds=900,
        verify_done=False,
    )
    svc = GoalService(queue, store, cfg)
    try:
        for goal_id, mode in (("ledger", "long_lived"), ("oneshot", "one_shot")):
            svc.create_goal(
                goal_id,
                objective="ship it",
                workspace_dir="/ws",
                done_when="the test command exits 0 and at least one assertion runs.",
                backlog=["scaffold project", "first feature"],
                mode=mode,
            )
            goal_store = svc._goal_store
            assert goal_store.load_status(goal_id).lifecycle == "executing"
            strat = ds.resolve_strategy(goal_store, goal_id)
            assert strat is ds.GOAL_BRANCH
            assert strat.goal_branch(goal_id) == f"goal/{goal_id}"
    finally:
        store.close()
