"""The goal_convergence terminal ledger (spec 018 US1) — one row per goal
terminal event, rounds counted from the append-only phase history so a
mid-goal steer's ``donegate_rounds`` streak reset can never hide churn."""

from __future__ import annotations

import json

import pytest

from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)


@pytest.fixture(autouse=True)
def _no_real_deploys(monkeypatch):
    from devclaw.delivery import deploy as deploy_mod

    async def _no_deploy(workspace_dir, slug):
        raise RuntimeError("no deploys under test")

    monkeypatch.setattr(deploy_mod, "deploy_project", _no_deploy)


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


def _convergence_rows(store):
    with store._state._lock:
        return store._state._db.execute(
            "SELECT goal_id, outcome, rounds, workspace_dir, closed_at "
            "FROM goal_convergence ORDER BY goal_id"
        ).fetchall()


async def _tick(store, goal_id, evaluator, engine, notifier):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=True,
    )


ACHIEVED = json.dumps({
    "verdict": "achieved",
    "rationale": "/health exists and is tested",
    "clauses": [
        {"clause": "/health returns 200", "satisfied": True,
         "evidence": "src/Health.cs:12; HealthTests.cs:8"},
    ],
})


@pytest.mark.asyncio
async def test_achieved_close_records_convergence_row_with_lifetime_rounds(tmp_path):
    """The achieved close writes the goal's one convergence row, and rounds
    is the LIFETIME count of done proposals (phase-history 'verifying'
    entries), not the resettable ``donegate_rounds`` streak counter."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # two earlier proposals that did not close (each entered verifying)...
    store.save_status("g", GoalStatus(phase="verifying"))
    store.save_status("g", GoalStatus(phase="idle"))
    store.save_status("g", GoalStatus(phase="verifying"))
    store.save_status("g", GoalStatus(phase="idle"))
    # ...and the closing one — donegate_rounds deliberately 0, as if a human
    # steer reset the streak mid-goal: the ledger must not believe it.
    store.save_status("g", GoalStatus(
        phase="verifying", donegate_rounds=0,
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review text"))

    out = await _tick(store, "g", FakeClaude(ACHIEVED), engine, RecordingNotifier())

    assert out is Outcome.DONE
    rows = _convergence_rows(store)
    assert len(rows) == 1
    assert rows[0]["outcome"] == "achieved"
    assert rows[0]["rounds"] == 3
    assert rows[0]["workspace_dir"]  # goal workspace threaded through
    assert rows[0]["closed_at"]


@pytest.mark.asyncio
async def test_first_pass_close_records_rounds_one(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review text"))

    out = await _tick(store, "g", FakeClaude(ACHIEVED), engine, RecordingNotifier())

    assert out is Outcome.DONE
    rows = _convergence_rows(store)
    assert len(rows) == 1
    assert rows[0]["rounds"] == 1


def _service(tmp_path):
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    db = StateStore(str(tmp_path / "state.db"))
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
    return GoalService(TaskQueue(db), db, config=cfg), db, goals_dir


def test_cancel_records_abandoned(tmp_path):
    svc, db, goals_dir = _service(tmp_path)
    try:
        seed_goal(goals_dir, "g")
        store = svc._goal_store
        store.save_status("g", GoalStatus(phase="verifying"))
        store.save_status("g", GoalStatus(phase="idle"))

        svc.cancel_goal("g")

        rows = _convergence_rows(store)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "abandoned"
        assert rows[0]["rounds"] == 1  # cancelled mid-churn: proposals so far
    finally:
        db.close()


def test_pre_proposal_cancel_records_rounds_zero(tmp_path):
    """A goal killed before any done proposal says so — rounds 0, abandoned;
    the scorecard excludes it from the convergence denominator."""
    svc, db, goals_dir = _service(tmp_path)
    try:
        seed_goal(goals_dir, "g")
        store = svc._goal_store
        store.save_status("g", GoalStatus(phase="idle"))

        svc.cancel_goal("g")

        rows = _convergence_rows(store)
        assert len(rows) == 1
        assert rows[0]["outcome"] == "abandoned"
        assert rows[0]["rounds"] == 0
    finally:
        db.close()


def test_terminal_row_is_written_once(tmp_path):
    """INSERT OR IGNORE: terminal is terminal — a second cancel on an
    already-cancelled goal is a graceful no-op and never clobbers the row."""
    svc, db, goals_dir = _service(tmp_path)
    try:
        seed_goal(goals_dir, "g")
        store = svc._goal_store
        store.save_status("g", GoalStatus(phase="idle"))
        svc.cancel_goal("g")
        first = _convergence_rows(store)[0]

        out = svc.cancel_goal("g")

        assert out["cancelled"] is False
        rows = _convergence_rows(store)
        assert len(rows) == 1
        assert rows[0]["closed_at"] == first["closed_at"]
    finally:
        db.close()
