"""Regression test: /problems.json recency-window filter.

Default view uses a 30-day lookback so old problems don't clutter the console.
Passing since_ms=0 bypasses the window and returns all-time results.
"""

from __future__ import annotations

import json
import time

import pytest

from devclaw.state_store import StateStore


@pytest.fixture
def store(tmp_path):
    return StateStore(str(tmp_path / "s.db"))


class _FakeGoals:
    def has_goal(self, goal_id: str) -> bool:
        return False


@pytest.fixture
def http_mod(store, monkeypatch):
    from devclaw.server.routes import observability as http_mod

    monkeypatch.setattr(http_mod, "store", store)
    monkeypatch.setattr(http_mod, "goals", _FakeGoals())
    return http_mod


async def test_problems_json_defaults_to_recency_window_and_all_time_param_bypasses_it(
    http_mod, store
):
    from starlette.requests import Request

    # Seed an old problem (60 days ago — outside the 30-day default window).
    store.record_problem(category="task_fail", kind="old_error", message="old", recovered=False)
    sixty_days_ago_ms = int(time.time() * 1000) - 60 * 24 * 60 * 60 * 1000
    with store._lock:
        store._db.execute(
            "UPDATE problems SET last_seen_ms = ?, first_seen_ms = ? WHERE kind = 'old_error'",
            (sixty_days_ago_ms, sixty_days_ago_ms),
        )
        store._db.commit()

    # Seed a recent problem (now — inside the 30-day default window).
    store.record_problem(category="task_fail", kind="new_error", message="new", recovered=False)

    # --- Default (no since_ms): only the recent problem appears ---
    req_default = Request({
        "type": "http", "method": "GET", "path": "/problems.json",
        "query_string": b"", "headers": [],
    })
    body_default = json.loads((await http_mod.problems_json(req_default)).body)
    kinds_default = {p["kind"] for p in body_default["problems"]}
    assert "new_error" in kinds_default, "recent problem must appear in default view"
    assert "old_error" not in kinds_default, "60-day-old problem must be excluded by default window"

    # --- since_ms=0: bypass filter — both problems appear ---
    req_all = Request({
        "type": "http", "method": "GET", "path": "/problems.json",
        "query_string": b"since_ms=0", "headers": [],
    })
    body_all = json.loads((await http_mod.problems_json(req_all)).body)
    kinds_all = {p["kind"] for p in body_all["problems"]}
    assert "new_error" in kinds_all, "recent problem must appear in all-time view"
    assert "old_error" in kinds_all, "old problem must appear when since_ms=0 bypasses the window"
