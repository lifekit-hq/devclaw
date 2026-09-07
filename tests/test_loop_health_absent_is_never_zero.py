"""Spec 039 FR-020 — the one tripwire the feature pins: *absent usage / absent
history is never rendered as zero*, at every layer that reads it.

An empty sample that reads ``0`` (or ``100%``) is the failure class the whole
feature exists to prevent — a constrained quota "costing nothing", a loop
"never stuck" because no tick was ever recorded. Each case here feeds a layer
nothing and asserts it says so (``None`` + the count it is based on) instead
of a number. PR-B/PR-C extend this file for the ledger, cost per outcome and
calibration; never mint a sibling.
"""

from __future__ import annotations

import pytest

from devclaw import loop_health as lh
from devclaw import telemetry
from devclaw.state_store import StateStore


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "devclaw.db"))
    yield s
    s.close()


# ---- the pure leaf ----------------------------------------------------------


@pytest.mark.parametrize("buckets,working,expected", [
    ({}, 0.0, None),                                   # nothing observed → unknown, not 100%
    ({"devclaw": 0, "owner": 0, "no_work": 0}, 0.0, None),
    ({"devclaw": 10, "owner": 0, "no_work": 0}, 0.0, 0.0),   # a measured stuck window IS 0
    ({"devclaw": 0, "owner": 5, "no_work": 5}, 10.0, 1.0),
    ({"devclaw": 5, "owner": 5, "no_work": 0}, 10.0, 0.75),
])
def test_not_stuck_rate_is_none_over_an_empty_window(buckets, working, expected):
    assert lh.not_stuck_rate(buckets, working) == expected


@pytest.mark.parametrize("cause,bucket", [
    ("working", "working"),
    ("unobserved", "unobserved"),
    ("paused", "no_work"), ("empty_backlog", "no_work"),
    ("all_planned_done", "no_work"), ("window_closed", "no_work"),
    ("no_goal_armed", "owner"), ("needs_answer", "owner"), ("operator_hold", "owner"),
    ("mechanical:ci", "devclaw"), ("mechanical:merge_failed", "devclaw"),
    ("bug", "devclaw"), ("lost_ref", "devclaw"), ("dispatch_cap", "devclaw"),
    ("donegate_churn", "devclaw"),
    ("some_future_kind", "devclaw"),                   # unknown → devclaw's, loud
    ("", "devclaw"),
])
def test_every_cause_derives_exactly_one_bucket(cause, bucket):
    """FR-005a: the bucket is a function of the cause — total, never stored."""
    assert lh.bucket_for(cause) == bucket


def test_derive_loop_cause_prefers_a_wedge_over_a_wait_and_reuses_kinds_verbatim():
    goals = [
        lh.GoalView("b-owner", False, "blocked", blocked_kind="needs_answer"),
        lh.GoalView("a-wedge", False, "blocked", blocked_kind="mechanical:merge_failed"),
    ]
    cause, detail = lh.derive_loop_cause(
        goals=goals, pause_active=False, operator_hold=False, window_closed=False,
    )
    assert (cause, detail) == ("mechanical:merge_failed", "a-wedge")
    # working anywhere beats every idle cause
    goals.append(lh.GoalView("c", False, "in_flight", outcome="in_flight"))
    assert lh.derive_loop_cause(
        goals=goals, pause_active=True, operator_hold=True, window_closed=True,
    )[0] == "working"
    # a pause is weather, an operator hold is the owner's, a closed window is no work
    assert lh.derive_loop_cause(goals=[], pause_active=True, operator_hold=True, window_closed=True)[0] == "paused"
    assert lh.derive_loop_cause(goals=[], pause_active=False, operator_hold=True, window_closed=True)[0] == "operator_hold"
    assert lh.derive_loop_cause(goals=[], pause_active=False, operator_hold=False, window_closed=True)[0] == "window_closed"
    assert lh.derive_loop_cause(goals=[], pause_active=False, operator_hold=False, window_closed=False)[0] == "empty_backlog"
    done = [lh.GoalView("d", True, "done")]
    assert lh.derive_loop_cause(goals=done, pause_active=False, operator_hold=False, window_closed=False)[0] == "all_planned_done"
    assert lh.derive_loop_cause(
        goals=done, pause_active=False, operator_hold=False, window_closed=False, unarmed_ready_issues=2,
    )[0] == "no_goal_armed"


# ---- the store + the read surfaces ------------------------------------------


def test_loop_health_reads_unknown_on_an_empty_db(store):
    out = telemetry.compute_loop_health(store, window_hours=24)
    assert out["idle"]["not_stuck_rate"] is None
    assert out["idle"]["observed_seconds"] == 0 and out["idle"]["causes"] == []
    assert out["self_heal"]["rate"] is None and out["self_heal"]["recovered"] == 0
    assert out["clean_cycle"]["rate"] is None and out["clean_cycle"]["total"] == 0
    assert out["first_pass"]["rate"] is None and out["first_pass"]["goals_closed"] == 0


def test_loop_spans_coalesce_and_a_gap_is_unobserved_not_attributed(store):
    """FR-004: contiguous, none double-counted; research D2: a heartbeat gap
    is its own span and stays out of the not-stuck denominator."""
    t0 = 1_000_000_000_000
    tick = 900_000
    gap = 3 * tick
    store.record_loop_sample(now_ms=t0, cause="empty_backlog", max_gap_ms=gap)          # seed
    store.record_loop_sample(now_ms=t0 + tick, cause="empty_backlog", max_gap_ms=gap)
    store.record_loop_sample(now_ms=t0 + 2 * tick, cause="mechanical:ci", max_gap_ms=gap)
    store.record_loop_sample(now_ms=t0 + 2 * tick + 10 * tick, cause="working", max_gap_ms=gap)  # a 10-tick gap
    spans = store.list_loop_spans(since_ms=0)
    assert [(s["cause"], s["end_ms"] - s["start_ms"]) for s in spans] == [
        ("empty_backlog", tick),
        ("mechanical:ci", tick),
        ("unobserved", 10 * tick),
        ("working", 0),
    ]
    # contiguous
    for a, b in zip(spans, spans[1:]):
        assert a["end_ms"] == b["start_ms"]
    idle = telemetry.compute_idle_attribution(store, since_ms=0, now_ms=t0 + 12 * tick)
    assert idle["unobserved_seconds"] == 10 * tick // 1000
    assert idle["observed_seconds"] == 2 * tick // 1000
    assert idle["buckets"] == {"devclaw": tick // 1000, "owner": 0, "no_work": tick // 1000}
    assert idle["not_stuck_rate"] == 0.5


def test_self_heal_rate_needs_a_denominator(store):
    assert telemetry.compute_self_heal(store, since_ms=0)["rate"] is None
    store.record_problem(category="limit", kind="quota", message="paused", recovered=True)
    store.record_problem(category="limit", kind="quota", message="paused", recovered=True)
    store.record_problem(category="block", kind="mechanical:ci", message="ci red", recovered=False)
    out = telemetry.compute_self_heal(store, since_ms=0)
    assert (out["recovered"], out["terminal"], out["rate"]) == (2, 1, round(2 / 3, 4))
    assert "lifetime" in out["basis"]


# ---- calibration (US6, FR-025): below the floor the read says "not yet" ------


def test_calibration_below_the_floor_is_not_determinable(tmp_path, store):
    """An empty ledger, a ledger predating the columns, and a ledger with a
    handful of predicted goals all read ``determinable: false`` with the
    sample still needed — never a figure from a sample too small to mean
    anything, never a healthy-looking 0."""
    from tests.goal_fakes import Clock, seed_goal
    from devclaw.goal.store import GoalStore

    # a StateStore alone has no goal_convergence table at all
    cal = telemetry.compute_calibration(store)
    assert cal["determinable"] is False and cal["n"] == 0
    assert cal["needed"] == telemetry.CALIBRATION_MIN_SAMPLE
    assert cal["claimed"]["mae"] is None and cal["assessed"]["exact_rate"] is None
    assert cal["note"]

    gstore = GoalStore(tmp_path / "goals", now=Clock())
    for i in range(3):
        seed_goal(tmp_path / "goals", f"g{i}")
        gstore._goal_state.record_convergence(
            f"g{i}", outcome="achieved", rounds=1, workspace_dir=None,
            closed_at="2026-09-07T12:00:00", claimed_units=2, assessed_units=1,
            prediction_issues='{"summed": [1], "missing": []}', dispatches=2, steered=False,
        )
    # a steered goal and an abandoned one are excluded, and counted as such
    seed_goal(tmp_path / "goals", "steered")
    gstore._goal_state.record_convergence(
        "steered", outcome="achieved", rounds=1, workspace_dir=None,
        closed_at="2026-09-07T12:00:00", claimed_units=1, dispatches=9, steered=True,
    )
    seed_goal(tmp_path / "goals", "gone")
    gstore._goal_state.record_convergence(
        "gone", outcome="abandoned", rounds=4, workspace_dir=None,
        closed_at="2026-09-07T12:00:00", claimed_units=1, dispatches=9,
    )
    cal = telemetry.compute_calibration(gstore._state)
    assert cal["n"] == 3 and cal["determinable"] is False
    assert cal["needed"] == telemetry.CALIBRATION_MIN_SAMPLE - 3
    assert cal["steered_excluded"] == 1 and cal["abandoned_excluded"] == 1
    # the raw agreement is still reported next to the sample it rests on
    assert cal["claimed"] == {"n": 3, "mae": 0.0, "exact_rate": 1.0, "within_one_rate": 1.0}
    assert cal["assessed"]["n"] == 3 and cal["assessed"]["mae"] == 1.0
