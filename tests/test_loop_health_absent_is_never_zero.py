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


# ---- PR-B: the usage ledger, its worker source, cost per outcome ------------

import importlib.util  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_lh", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # stdlib-only (spec 011)
    return mod


def _transcript_line(usage, *, request_id="req-1", cwd="/workspace", kind="assistant"):
    return json.dumps({
        "type": kind, "cwd": cwd, "requestId": request_id, "uuid": os.urandom(4).hex(),
        "message": {"id": "msg-" + request_id, "usage": usage},
    })


def test_transcript_reader_reports_none_when_the_agent_recorded_nothing(runner, tmp_path):
    """The worker usage source (research D1): no transcript, an empty
    transcript, or one without usage all read None — never a block of zeros."""
    cfg = tmp_path / "cfg"
    assert runner._claude_transcript_usage(str(cfg), "/workspace", 0.0) is None
    d = cfg / "projects" / "-workspace"
    d.mkdir(parents=True)
    (d / "s1.jsonl").write_text("")
    assert runner._claude_transcript_usage(str(cfg), "/workspace", 0.0) is None
    (d / "s1.jsonl").write_text(_transcript_line(None) + "\n" + json.dumps({"type": "user"}) + "\n")
    assert runner._claude_transcript_usage(str(cfg), "/workspace", 0.0) is None
    # zeros are not a report either
    (d / "s1.jsonl").write_text(_transcript_line({"input_tokens": 0, "output_tokens": 0}) + "\n")
    assert runner._claude_transcript_usage(str(cfg), "/workspace", 0.0) is None


def test_transcript_reader_sums_one_row_per_request_and_skips_other_runs(runner, tmp_path):
    cfg = tmp_path / "cfg"
    d = cfg / "projects" / "-workspace"
    d.mkdir(parents=True)
    u = {"input_tokens": 3, "output_tokens": 100, "cache_read_input_tokens": 500,
         "cache_creation_input_tokens": 200}
    lines = [
        _transcript_line(u, request_id="r1"),
        _transcript_line(u, request_id="r1"),            # same API response, second JSONL line
        _transcript_line(u, request_id="r2"),
        _transcript_line(u, request_id="r3", cwd="/elsewhere"),  # another workspace
    ]
    (d / "s1.jsonl").write_text("\n".join(lines) + "\n")
    old = d / "old.jsonl"
    old.write_text(_transcript_line(u, request_id="r9") + "\n")
    os.utime(old, (time.time() - 3600, time.time() - 3600))  # predates the run
    out = runner._claude_transcript_usage(str(cfg), "/workspace", time.time() - 60)
    assert out == {"input_tokens": 6, "output_tokens": 200, "cache_read_tokens": 1000,
                   "cache_creation_tokens": 400, "source": "transcript"}
    assert runner._is_claude_adapter(["claude-agent-acp"]) and not runner._is_claude_adapter(
        ["python", "tests/acp_fake_agent.py"]
    )


def test_ledger_rows_without_usage_are_records_not_zeros(store):
    """FR-011: a run that reported nothing is a reported=0 row; every rollup
    says how many records it stands on and reads None for the tokens."""
    store.create_task(id="t1", kind="implement_feature", workspace_dir="/ws", goal="g")
    store.record_task_usage("t1", attempt=0, usage=None)
    store.record_task_usage("t1", attempt=1, usage={"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
    hist = telemetry.compute_usage_history(store)
    (month,) = hist["months"]
    assert month["worker"] == {"tokens": None, "cost_usd": None, "records": 2, "reported": 0}
    store.record_task_usage("t1", attempt=2, usage={"input_tokens": 10, "output_tokens": 5, "source": "transcript"})
    (month,) = telemetry.compute_usage_history(store)["months"]
    assert month["worker"] == {"tokens": 15, "cost_usd": None, "records": 3, "reported": 1}
    # idempotent under (source, ref_id, attempt)
    store.record_task_usage("t1", attempt=2, usage={"input_tokens": 999, "output_tokens": 999})
    assert telemetry.compute_usage_history(store)["months"][0]["worker"]["tokens"] == 15

    # the same rule at goal grain (US6 `cost_tokens`): a goal whose runs
    # reported nothing costs unknown, and cache reads are not consumption.
    assert store.goal_usage_tokens("no-such-goal") is None
    store.create_task(
        id="t2", kind="implement_feature", workspace_dir="/ws",
        goal="objective", parent_goal_id="g2",
    )
    store.record_task_usage("t2", attempt=0, usage=None)
    assert store.goal_usage_tokens("g2") is None
    store.record_task_usage(
        "t2", attempt=1,
        usage={"input_tokens": 7, "output_tokens": 3, "cache_read_tokens": 900},
    )
    assert store.goal_usage_tokens("g2") == 10


def test_cognition_trace_writes_its_ledger_row_in_the_same_commit(store):
    real = store.append_trace_event(
        trace_id="tr", goal_id="g", kind="cognition",
        payload={"role": "evaluator", "tokens_in": 100, "tokens_out": 20, "cost_usd": 0.01},
    )
    est = store.append_trace_event(
        trace_id="tr", goal_id="g", kind="cognition",
        payload={"role": "evaluator", "tokens_in_est": 500, "tokens_out_est": 50},
    )
    rows = {r["ref_id"]: r for r in store.usage_ledger_rows(since_ms=0)}
    assert rows[str(real)]["reported"] == 1 and rows[str(real)]["input_tokens"] == 100
    assert rows[str(est)]["reported"] == 0 and rows[str(est)]["input_tokens"] is None  # never the len/4 guess


def test_cost_per_outcome_reads_unknown_on_an_empty_ledger(store):
    out = telemetry.compute_cost_per_outcome(store, since_ms=0)
    assert out["merged_goals"]["tokens_per"] is None
    assert out["merged_standalone_prs"]["tokens_per"] is None
    assert out["records"] == 0 and out["reported"] == 0
    sc = telemetry.compute_scorecard(store, window_hours=24)
    assert "tokens_per_merged_pr" not in sc["usage"] and "cost_per_merged_pr_usd" not in sc["usage"]
    assert sc["usage"]["cost_per_outcome"]["merged_goals"]["tokens_per"] is None


def test_cost_per_outcome_segments_by_shape_and_excludes_unknown_pr_state(store):
    """FR-014a/FR-015: a merged PR behind one dispatch is standalone, behind
    several is goal-cumulative; an unrefreshed PR is an explicit third
    bucket; runs with no PR are shipped-nothing."""
    def _task(tid, goal, pr=None, kind="implement_feature", tokens=100):
        store.create_task(id=tid, kind=kind, workspace_dir="/ws", goal="x", parent_goal_id=goal)
        store.claim_pending(tid)
        store.record_task_usage(tid, attempt=0, usage={"input_tokens": tokens, "output_tokens": 0})
        store.mark_done(tid, json.dumps({"status": "ok"}), pr_url=pr)

    _task("a1", "goal-a", pr="https://x/pr/1")
    _task("a2", "goal-a", pr="https://x/pr/1")
    _task("b1", "goal-b", pr="https://x/pr/2")
    _task("c1", "goal-c")                      # shipped nothing
    _task("d1", "goal-d", pr="https://x/pr/4")  # never refreshed → unknown
    store.upsert_pr_states({"https://x/pr/1": "merged", "https://x/pr/2": "merged"}, as_of_ms=1, truncated=False)
    out = telemetry.compute_cost_per_outcome(store, since_ms=0)
    assert out["merged_goals"] == {"count": 1, "tokens_total": 200, "tokens_per": 200, "cost_usd_per": None}
    assert out["merged_standalone_prs"] == {"count": 1, "tokens_total": 100, "tokens_per": 100, "cost_usd_per": None}
    assert out["shipped_nothing"] == {"count": 1, "tokens_total": 100}
    assert out["unknown"] == {"count": 1, "tokens_total": 100}
    assert (out["records"], out["reported"], out["tasks_without_record"]) == (5, 5, 0)
