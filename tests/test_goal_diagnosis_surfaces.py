"""Goal diagnosis surfaces tell the truth (#495 + #496).

During the 2026-08-12 ledger night-1 hunt, the single most load-bearing
runtime decision per goal — which delivery strategy engaged — was visible
nowhere but a workspace ``git reflog`` on the VPS, and ``get_goal`` rendered a
NULL lifecycle as ``"executing"`` while ``resolve_strategy`` branched on the
raw NULL: the display actively pointed diagnosis away from the #493 bug.

These pin the fix: ``get_goal``/``tail_goal``/``list_goals`` surface the
RESOLVED ``delivery_strategy`` + ``goal_branch``, and every read surface
returns the RAW stored lifecycle (null for legacy rows) — displayed state and
behavior can never disagree again.
"""

from __future__ import annotations

import pytest

from devclaw.goal.service import GoalConfig, GoalService
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue


@pytest.fixture
def svc(tmp_path):
    store = StateStore(str(tmp_path / "t.db"))
    cfg = GoalConfig(
        goals_dir=tmp_path / "goals",
        notify_url="",
        tick_seconds=900,
        eval_every=5,
        verify_done=False,
    )
    service = GoalService(TaskQueue(store), store, cfg)
    yield service
    store.close()


def _mk(svc, goal_id, *, mode):
    svc.create_goal(
        goal_id,
        objective="ship it",
        workspace_dir="/ws",
        done_when="the test command exits 0 and at least one assertion runs.",
        backlog=["scaffold project", "first feature"],
        mode=mode,
    )


def test_get_goal_surfaces_goal_branch_strategy_for_long_lived_executing(svc):
    _mk(svc, "ledger", mode="long_lived")
    g = svc.get_goal("ledger")
    assert g["delivery_strategy"] == "goal-branch"
    assert g["goal_branch"] == "goal/ledger"
    assert g["lifecycle"] == "executing"  # stamped at creation (#493)


def test_get_goal_surfaces_per_action_for_one_shot_without_checklist(svc):
    _mk(svc, "quick", mode="one_shot")
    g = svc.get_goal("quick")
    assert g["delivery_strategy"] == "per-action"
    assert g["goal_branch"] is None


def test_surfaces_return_raw_lifecycle_never_coalesced(svc):
    """#496 named regression: a legacy row's NULL lifecycle renders as null on
    get_goal / tail_goal / list_goals — never "helpfully" coalesced to
    "executing" while resolve_strategy branches on the raw value."""
    import dataclasses

    _mk(svc, "legacy", mode="one_shot")
    s = svc._goal_store.load_status("legacy")
    # simulate a pre-lifecycle legacy row
    svc._goal_store.save_status("legacy", dataclasses.replace(s, lifecycle=None))
    assert svc._goal_store.load_status("legacy").lifecycle is None

    assert svc.get_goal("legacy")["lifecycle"] is None
    assert svc.tail_goal("legacy")["lifecycle"] is None
    row = next(r for r in svc.list_goals() if r["id"] == "legacy")
    assert row["lifecycle"] is None
    # and the strategy shown is what delivery actually resolves: per-action
    assert row["delivery_strategy"] == "per-action"


def test_get_goal_degrades_to_unresolvable_on_corrupt_contract(svc, monkeypatch):
    """Display path: a corrupt checklist contract must not 500 the read
    surface (the tick blocks the goal loudly) — but it must surface as an
    explicit "unresolvable", never a silently-wrong strategy name."""
    _mk(svc, "corrupt", mode="long_lived")
    monkeypatch.setattr(
        svc._goal_store,
        "read_checklist",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("corrupt contract")),
    )
    g = svc.get_goal("corrupt")
    assert g["delivery_strategy"] == "unresolvable"
    assert g["goal_branch"] is None


def test_get_goal_next_shows_objective_never_advance_brief(svc):
    """#550 named regression (read-side guard): a status row that stored the
    raw thin-advance brief as ``next`` — written before the dispatch-side fix —
    still renders the goal's embedded objective on every read surface;
    get_goal and tail_goal never leak the brief."""
    import dataclasses

    _mk(svc, "ledger", mode="long_lived")
    brief = (
        "Advance this goal by one substantive, shippable increment using "
        "speckit, then stop.\n"
        "Find the CURRENT feature: the smallest not-yet-complete specs/NNN-*/.\n"
        "\n"
        "Goal: ship it"
    )
    s = svc._goal_store.load_status("ledger")
    svc._goal_store.save_status("ledger", dataclasses.replace(s, next=brief))

    assert svc.get_goal("ledger")["next"] == "ship it"
    assert svc.tail_goal("ledger")["next"] == "ship it"
