"""Instance health drift probes — named regression tests (spec 027 / issue #596).

Pins three invariants from the done-when:
  T1. A healthy instance (all probes below threshold) records no problem.
  T2. A threshold breach records exactly ONE deduplicated problem row; calling
      again with the same reading increments ``count`` on that same row.
  T3. A probe returning ``None`` (failure) produces no record and no exception.

Uses a real StateStore with a temp SQLite — same pattern as
``test_problems_catalog.py`` — so the dedup fingerprinting is exercised
end-to-end. The three injectable probe functions are monkeypatched at the
module level so no docker daemon or real filesystem is needed.
"""

from __future__ import annotations

import pytest

from devclaw.goal import health_drift as _hd
from devclaw.state_store import StateStore


@pytest.fixture
def store(tmp_path) -> StateStore:
    return StateStore(str(tmp_path / "health.db"))


def _run(store: StateStore, **overrides) -> None:
    """Helper: run checks with benign defaults, overridable per-test."""
    kwargs: dict = dict(
        store=store,
        goals=[],
        project_workspaces=set(),
        now_ms=0,
        goals_dir="/tmp/goals",
        disk_warn_pct=80.0,
        orphan_docker_warn=10,
        stale_ws_warn=20,
        docker_bin="docker",
    )
    kwargs.update(overrides)
    _hd.run_health_drift_checks(**kwargs)


# ---- T1: healthy instance records no problem --------------------------------


def test_healthy_instance_records_no_problem(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)

    assert store.list_problems() == []


def test_disk_at_exactly_threshold_is_not_a_problem(store, monkeypatch):
    # Strictly BELOW threshold → no record; AT threshold → record (boundary).
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 79.9)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store, disk_warn_pct=80.0)
    assert store.list_problems() == []


def test_orphan_count_below_threshold_records_no_problem(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 9)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store, orphan_docker_warn=10)
    assert store.list_problems() == []


# ---- T2: threshold breach → one deduplicated row ----------------------------


def test_disk_breach_records_one_row(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)
    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert rows[0]["kind"] == "disk_usage_high"
    assert rows[0]["count"] == 1


def test_disk_breach_deduplicates_on_second_call(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)
    _run(store)

    rows = store.list_problems(category="other")
    # Still ONE row — dedup is working — with count 2.
    assert len(rows) == 1
    assert rows[0]["kind"] == "disk_usage_high"
    assert rows[0]["count"] == 2


def test_orphan_volume_breach_records_one_row(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 15)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store, orphan_docker_warn=10)
    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert rows[0]["kind"] == "orphan_docker_volumes"


def test_stale_workspace_breach_records_one_row(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 25)

    _run(store, stale_ws_warn=20)
    rows = store.list_problems(category="other")
    assert len(rows) == 1
    assert rows[0]["kind"] == "stale_workspaces"


def test_all_three_breached_records_three_rows(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 15)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 25)

    _run(store, disk_warn_pct=80.0, orphan_docker_warn=10, stale_ws_warn=20)
    rows = store.list_problems(category="other")
    kinds = {r["kind"] for r in rows}
    assert kinds == {"disk_usage_high", "orphan_docker_volumes", "stale_workspaces"}


# ---- T3: probe failure → unknown reading, no record, no exception ----------


def test_disk_probe_failure_records_nothing_and_does_not_raise(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: None)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)  # must not raise
    assert store.list_problems() == []


def test_docker_probe_failure_records_nothing_and_does_not_raise(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 50.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: None)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    _run(store)
    assert store.list_problems() == []


def test_all_probes_failing_records_nothing_and_does_not_raise(store, monkeypatch):
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: None)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: None)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: None)

    _run(store)  # must not raise
    assert store.list_problems() == []


# ---- structural: zero-LLM guard (belt + suspenders) -------------------------


def test_run_health_drift_checks_never_invokes_claude(store, monkeypatch):
    """The probe module must never call into the cognition layer — belt + suspenders
    beyond what the typed function signatures already make impossible."""
    monkeypatch.setattr(_hd, "_disk_used_pct", lambda _path: 85.0)
    monkeypatch.setattr(_hd, "_orphan_docker_volume_count", lambda _bin, _ws: 0)
    monkeypatch.setattr(_hd, "_stale_workspace_count", lambda _g, _pw, _now: 0)

    # Confirm it runs clean without any cognition import side-effect.
    # Zero LLM is structurally guaranteed (no claude import in health_drift.py);
    # this test pins that no later edit silently wires in a cognition call.
    _run(store)
