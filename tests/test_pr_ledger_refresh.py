"""The pr_ledger refresh (spec 018 US2, clarified option B): platform state
enters the ledger ONLY through the once-per-cycle bounded refresh — the
scorecard read stays a pure store read, staleness and cap-truncation are
persisted loudly, and per-URL failures degrade to 'unknown', never to a
stalled batch."""

from __future__ import annotations

import json

import pytest

from devclaw.goal.service import GoalConfig, GoalService
from devclaw.state_store import StateStore, _now_ms
from devclaw.task_queue import TaskQueue


class FakePrStates:
    """Scripted url → state (or Exception). ``.calls`` counts platform reads."""

    def __init__(self, states: dict | None = None):
        self._states = dict(states or {})
        self.calls = 0

    async def __call__(self, url: str) -> str:
        self.calls += 1
        v = self._states.get(url)
        if isinstance(v, Exception):
            raise v
        return v or "unknown"


def _service(tmp_path):
    goals_dir = tmp_path / "goals"
    db = StateStore(str(tmp_path / "state.db"))
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
    return GoalService(TaskQueue(db), db, config=cfg), db


def _seed_pr(db, url, *, state="open", opened_ms=None):
    with db._lock:
        db._db.execute(
            "INSERT INTO pr_ledger (pr_url, workspace_dir, opened_at_ms, state) "
            "VALUES (?, '/w', ?, ?)",
            (url, opened_ms if opened_ms is not None else _now_ms(), state),
        )
        db._db.commit()


def _rows(db):
    with db._lock:
        return {
            r["pr_url"]: (r["state"], r["state_as_of_ms"])
            for r in db._db.execute("SELECT * FROM pr_ledger").fetchall()
        }


@pytest.mark.asyncio
async def test_refresh_touches_only_nonmerged_in_window(tmp_path):
    """merged is the ONE terminal state (a rejected PR can be reopened, so it
    stays in the refresh set); out-of-window rows are not re-read."""
    svc, db = _service(tmp_path)
    try:
        _seed_pr(db, "https://gh/x/1", state="open")
        _seed_pr(db, "https://gh/x/2", state="merged")
        _seed_pr(db, "https://gh/x/3", state="rejected")
        _seed_pr(db, "https://gh/x/4", state="open",
                 opened_ms=_now_ms() - 90 * 24 * 3600 * 1000)  # out of window
        fake = FakePrStates({"https://gh/x/1": "merged", "https://gh/x/3": "open"})
        svc._pr_state_fetcher = fake

        await svc._refresh_pr_ledger()

        rows = _rows(db)
        assert rows["https://gh/x/1"][0] == "merged"
        assert rows["https://gh/x/3"][0] == "open"     # reopened rejected → open
        assert rows["https://gh/x/2"][1] is None        # merged: never re-read
        assert rows["https://gh/x/4"][1] is None        # out of window: untouched
        assert fake.calls == 2
    finally:
        db.close()


@pytest.mark.asyncio
async def test_refresh_cap_persists_truncation_loudly(tmp_path):
    svc, db = _service(tmp_path)
    try:
        for i in range(4):
            _seed_pr(db, f"https://gh/x/{i}", state="open")
        db.PR_REFRESH_CAP = 2  # instance override for the test
        fake = FakePrStates({f"https://gh/x/{i}": "open" for i in range(4)})
        svc._pr_state_fetcher = fake

        await svc._refresh_pr_ledger()

        assert fake.calls == 2  # the cap held
        meta = json.loads(db.get_meta(db.PR_REFRESH_META_KEY))
        assert meta["truncated"] is True
    finally:
        db.close()


@pytest.mark.asyncio
async def test_unreachable_pr_lands_unknown_without_stopping_the_batch(tmp_path):
    svc, db = _service(tmp_path)
    try:
        _seed_pr(db, "https://gh/x/1", state="open")
        _seed_pr(db, "https://gh/x/2", state="open")
        fake = FakePrStates({
            "https://gh/x/1": RuntimeError("boom"),
            "https://gh/x/2": "merged",
        })
        svc._pr_state_fetcher = fake

        await svc._refresh_pr_ledger()

        rows = _rows(db)
        assert rows["https://gh/x/1"][0] == "unknown"
        assert rows["https://gh/x/1"][1] is not None    # the read RAN — stamped
        assert rows["https://gh/x/2"][0] == "merged"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_empty_refresh_still_stamps_the_summary(tmp_path):
    """'Refreshed, nothing to do' must be distinguishable from 'never ran' —
    the staleness stamp is the scorecard's honesty signal."""
    svc, db = _service(tmp_path)
    try:
        svc._pr_state_fetcher = FakePrStates()
        await svc._refresh_pr_ledger()
        meta = json.loads(db.get_meta(db.PR_REFRESH_META_KEY))
        assert meta["at_ms"] > 0 and meta["truncated"] is False
    finally:
        db.close()


def test_settle_creates_one_ledger_row_per_distinct_pr(tmp_path):
    """Row creation rides the settle projection: increments sharing the
    cumulative goal-branch PR upsert ONE row (INSERT OR IGNORE on the URL)."""
    db = StateStore(str(tmp_path / "state.db"))
    try:
        for i in range(3):
            tid = f"t{i}"
            db.create_task(id=tid, kind="implement_feature", workspace_dir="/w", goal="g")
            db.mark_done(tid, json.dumps({"ok": True}), pr_url="https://gh/x/shared")
        assert set(_rows(db)) == {"https://gh/x/shared"}
        assert _rows(db)["https://gh/x/shared"][0] == "open"
    finally:
        db.close()
