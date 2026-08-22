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
        out_of_scope=[], invariants=[], established=[],
    )


def test_get_goal_surfaces_goal_branch_strategy_for_both_modes(svc):
    # Spec 008 shrink: create_goal stamps lifecycle="executing" for BOTH
    # modes, so every fresh goal surfaces goal-branch delivery.
    _mk(svc, "ledger", mode="long_lived")
    g = svc.get_goal("ledger")
    assert g["delivery_strategy"] == "goal-branch"
    assert g["goal_branch"] == "goal/ledger"
    assert g["lifecycle"] == "executing"  # stamped at creation (#493)

    _mk(svc, "quick", mode="one_shot")
    g2 = svc.get_goal("quick")
    assert g2["delivery_strategy"] == "goal-branch"
    assert g2["goal_branch"] == "goal/quick"
    assert g2["lifecycle"] == "executing"


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


def test_get_goal_surfaces_the_queued_wait_with_its_holder(svc):
    """Spec 010 FR-002 / SC-006: a goal waiting on another goal's project can be
    identified as queued — and told WHICH goal it waits on — from its own status
    surface, with no log-diving.

    Derived on read, never stored: the hold itself is derived (FR-005 as
    amended), and a persisted copy of a derived fact can drift out of step with
    it. It also keeps a queued tick free of per-heartbeat writes."""
    _mk(svc, "holder", mode="long_lived")     # both on /ws → one project
    _mk(svc, "waiter", mode="long_lived")

    holder = svc.get_goal("holder")
    waiter = svc.get_goal("waiter")

    # The holder is working; it waits on nobody.
    assert holder["queued_behind"] is None
    assert holder["queued_reason"] is None
    # The waiter names its holder, and says it resumes without operator action.
    assert waiter["queued_behind"] == "holder"
    assert "holder" in waiter["queued_reason"]
    assert "automatically" in waiter["queued_reason"]
    # Queued is NOT blocked: nothing is wrong and nothing is asked of the owner.
    assert waiter["blocked_on"] is None
    assert waiter["blocked_kind"] in (None, "")


def test_get_goal_shows_no_queue_once_the_holder_is_terminal(svc):
    """FR-003: the handover needs no operator action and no release step — once
    the holder goes terminal the derivation simply names the next goal."""
    _mk(svc, "holder", mode="long_lived")
    _mk(svc, "waiter", mode="long_lived")
    assert svc.get_goal("waiter")["queued_behind"] == "holder"

    svc.cancel_goal("holder")

    assert svc.get_goal("waiter")["queued_behind"] is None
