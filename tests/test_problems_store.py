"""Regression tests for ProblemStore accessor behavior.

Covers the since_ms filter on list_problems — a named regression so the
optional recency window is not silently dropped by future refactors.
"""

from __future__ import annotations

from devclaw.state_store import StateStore
from devclaw.state_store.rows import _now_ms


def _store(tmp_path) -> StateStore:
    return StateStore(str(tmp_path / "problems.db"))


def test_list_problems_since_ms_filters_stale_rows(tmp_path):
    s = _store(tmp_path)

    # Record two distinct problems so they get separate fingerprints.
    s.record_problem(category="task_fail", kind="stale_error", message="stale error detail", recovered=False)
    s.record_problem(category="task_fail", kind="recent_error", message="recent error detail", recovered=False)

    # Backdate the stale row's last_seen_ms well into the past via direct SQL
    # so we have a controlled timestamp gap without sleeping.
    stale_ms = 1_000_000  # epoch + 1000 s — guaranteed older than any real run
    s._db.execute(
        "UPDATE problems SET last_seen_ms = ? WHERE kind = 'stale_error'",
        (stale_ms,),
    )
    s._db.commit()

    # Without a filter, both rows are returned.
    assert len(s.list_problems()) == 2

    # With since_ms set to a point after the stale row's timestamp, only the
    # recent row passes the WHERE last_seen_ms >= ? clause.
    cutoff = stale_ms + 1
    recent = s.list_problems(since_ms=cutoff)
    assert len(recent) == 1
    assert recent[0]["kind"] == "recent_error"
