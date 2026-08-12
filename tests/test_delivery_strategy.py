"""Unit tests for the delivery-strategy seam (PR1 — branch selection only)."""

from dataclasses import dataclass

import pytest

from devclaw.goal import delivery_strategy as ds
from devclaw.goal.models import Checklist, ChecklistItem


@dataclass
class _FakeGoal:
    mode: str = "long_lived"


@dataclass
class _FakeStatus:
    lifecycle: "str | None" = None


class _FakeStore:
    """Minimal stand-in exposing ``read_checklist`` plus the ``load_goal`` /
    ``load_status`` reads the long_lived branch needs — records the kwargs
    ``read_checklist`` was called with so we can assert resolve_strategy keeps the
    fail-loud default. A callable ``checklist`` is invoked (so a test can raise)."""

    def __init__(self, checklist, *, mode="long_lived", lifecycle=None):
        self._checklist = checklist
        self._goal = _FakeGoal(mode=mode)
        self._status = _FakeStatus(lifecycle=lifecycle)
        self.calls: list[dict] = []

    def read_checklist(self, goal_id, *, on_corrupt="raise"):
        self.calls.append({"goal_id": goal_id, "on_corrupt": on_corrupt})
        if isinstance(self._checklist, Exception):
            raise self._checklist
        return self._checklist

    def load_goal(self, goal_id):
        return self._goal

    def load_status(self, goal_id):
        return self._status


def test_checklist_present_resolves_goal_branch():
    store = _FakeStore(Checklist(items=[
        ChecklistItem(id="1", requirement="do a thing", evidence_target="thing.py")
    ]))
    strat = ds.resolve_strategy(store, "g1")
    assert strat is ds.GOAL_BRANCH
    assert strat.goal_branch("g1") == "goal/g1"


def test_legacy_no_checklist_no_lifecycle_resolves_per_action():
    # A legacy goal: no checklist, no recorded lifecycle (``None``). It reads-as-
    # executing elsewhere, but delivery stays PER_ACTION — unchanged by the
    # long_lived branch (which requires an EXPLICIT ``executing`` lifecycle).
    store = _FakeStore(None, mode="long_lived", lifecycle=None)
    strat = ds.resolve_strategy(store, "g1")
    assert strat is ds.PER_ACTION
    assert strat.goal_branch("g1") is None


def test_long_lived_executing_no_checklist_resolves_goal_branch():
    # THE amnesia fix (2026-08-08): a long_lived goal in the executing lifecycle
    # with no checklist accumulates its nightly increments on ``goal/<id>`` so the
    # per-task reset-to-main no longer wipes prior nights' work.
    store = _FakeStore(None, mode="long_lived", lifecycle="executing")
    strat = ds.resolve_strategy(store, "g1")
    assert strat is ds.GOAL_BRANCH
    assert strat.goal_branch("g1") == "goal/g1"


def test_long_lived_pre_executing_lifecycle_stays_per_action():
    # Before the goal reaches ``executing`` (still investigating/firming) there is
    # no accumulated work to preserve — delivery stays PER_ACTION.
    for lc in ("investigating", "firming"):
        store = _FakeStore(None, mode="long_lived", lifecycle=lc)
        assert ds.resolve_strategy(store, "g1") is ds.PER_ACTION


def test_one_shot_no_checklist_stays_per_action():
    # A one_shot goal delivers its plan through a checklist; before that checklist
    # exists (or on the legacy/one-shot no-checklist path) it is NOT long_lived, so
    # the new branch must not fire — it stays PER_ACTION exactly as before.
    store = _FakeStore(None, mode="one_shot", lifecycle="executing")
    assert ds.resolve_strategy(store, "g1") is ds.PER_ACTION


def test_empty_checklist_still_goal_branch():
    # Checklist is a plain frozen dataclass — always truthy even with no items —
    # so the pre-refactor split between `is not None` (remote-checks site) and
    # truthiness (dispatch/done-gate sites) collapses to one predicate. This test
    # pins that equivalence: an item-less checklist must still be goal-branch.
    store = _FakeStore(Checklist(items=[]))
    assert ds.resolve_strategy(store, "g1") is ds.GOAL_BRANCH


def test_resolve_keeps_fail_loud_default_and_propagates_corruption():
    # resolve_strategy must NOT pass on_corrupt="none" — it inherits the call
    # sites' default fail-loud behaviour (T0.4), so a corrupt contract raises
    # here rather than being silently downgraded to per-action.
    boom = RuntimeError("corrupt contract")
    store = _FakeStore(boom)
    with pytest.raises(RuntimeError, match="corrupt contract"):
        ds.resolve_strategy(store, "g1")
    assert store.calls == [{"goal_id": "g1", "on_corrupt": "raise"}]


def test_fresh_long_lived_goal_resolves_goal_branch_delivery(tmp_path):
    # Regression (live-found: ledger night 1, 2026-08-10). create_goal(mode=
    # "long_lived") persisted NO lifecycle — the "starts executing directly"
    # comment was never written to the status row — and resolve_strategy's
    # deliberately-explicit ``executing`` requirement then downgraded every
    # FRESH long_lived goal to per-action delivery: reset-to-main on each
    # dispatch, three unmerged scaffold PRs, main never moved — the exact
    # amnesia #486 was written to kill. Creation must stamp the lifecycle it
    # claims, end to end through the real service + store.
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    store = StateStore(str(tmp_path / "t.db"))
    queue = TaskQueue(store)
    cfg = GoalConfig(
        goals_dir=tmp_path / "goals",
        notify_url="",
        tick_seconds=900,
        eval_every=5,
        verify_done=False,
    )
    svc = GoalService(queue, store, cfg)
    try:
        svc.create_goal(
            "ledger",
            objective="ship it",
            workspace_dir="/ws",
            done_when="the test command exits 0 and at least one assertion runs.",
            backlog=["scaffold project", "first feature"],
            mode="long_lived",
        )
        goal_store = svc._goal_store
        assert goal_store.load_status("ledger").lifecycle == "executing"
        strat = ds.resolve_strategy(goal_store, "ledger")
        assert strat is ds.GOAL_BRANCH
        assert strat.goal_branch("ledger") == "goal/ledger"
    finally:
        store.close()
