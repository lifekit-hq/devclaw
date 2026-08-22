"""The console's list payload (`_goal_row`) carries the goal's `objective`.

The Overview and global Goals views render one row per goal. Before this, the
row shape carried only `id` (a machine handle) + `action` (the current motion),
so the landing list could label each goal only by its UUID — a debug-dump feel,
not "here's what each goal IS". Threading the durable `objective` into the row
lets the list read as intent. `objective` is a goal's identity, distinct from
`action`; a missing goal degrades to "" (never a KeyError, never a 500).
"""

from __future__ import annotations

import devclaw.server._state as state_mod
import devclaw.server.routes.goals as http_mod
from devclaw.goal.service import GoalConfig, GoalService
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue
from tests.goal_fakes import RecordingNotifier, seed_goal


def _svc(tmp_path):
    db = StateStore(str(tmp_path / "t.db"))
    goals_dir = tmp_path / "goals"
    cfg = GoalConfig(
        goals_dir=goals_dir, notify_url="", tick_seconds=900,
        verify_done=False,
    )
    svc = GoalService(
        TaskQueue(db), db, config=cfg, notifier=RecordingNotifier(),
    )
    return svc, goals_dir, db


def test_goal_row_carries_objective_as_identity(tmp_path, monkeypatch):
    svc, goals_dir, db = _svc(tmp_path)
    try:
        seed_goal(goals_dir, "g")  # objective: "Drive the demo repo to done."
        monkeypatch.setattr(state_mod, "goals", svc)

        row = http_mod._goal_row("g")

        # The durable goal statement is present as its own field — the list's
        # primary label — and is NOT the machine id or the action.
        assert row["objective"] == "Drive the demo repo to done."
        assert row["id"] == "g"
        assert "action" in row  # action stays: it's the current motion, not identity
    finally:
        db.close()


def test_goal_row_missing_goal_degrades_objective_to_empty(tmp_path, monkeypatch):
    svc, _goals_dir, db = _svc(tmp_path)
    try:
        monkeypatch.setattr(state_mod, "goals", svc)

        row = http_mod._goal_row("nope")

        # A goal the store can't resolve yields "" — a blank label the UI falls
        # back from (to the id), never a KeyError bubbling into a 500.
        assert row["objective"] == ""
        assert row["phaseLabel"] == "Missing"
    finally:
        db.close()
