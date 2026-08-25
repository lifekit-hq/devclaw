"""L8 scorecard telemetry tests — proves the rollup reads what's actually in
state_store, over a window, without any cognition call."""
from __future__ import annotations

import json
import time

import pytest

from devclaw.state_store import StateStore, _now_ms
from devclaw.telemetry import compute_scorecard, format_scorecard


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _land_task(store: StateStore, *, workspace: str, status: str, pr_url: str = "") -> str:
    """Create a task and drive it to a terminal state as if the queue had run
    it. Bypasses TaskQueue — the scorecard reads state_store directly, so we
    exercise the state_store surface without the async runner in the way."""
    tid = f"tid-{time.time_ns()}"
    store.create_task(id=tid, kind="implement_feature", workspace_dir=workspace, goal="g")
    if status == "done":
        store.mark_done(tid, json.dumps({"ok": True}), pr_url=pr_url or None)
    elif status == "failed":
        store.mark_failed(tid, "boom")
    elif status == "cancelled":
        store.mark_task_cancelled(tid)
    return tid


def _emit_evaluator_verdict(store: StateStore, goal_id: str, verdict: str) -> None:
    """Simulate a cognition trace row the evaluator would emit — enough of the
    real shape (role + response_text) for compute_scorecard to classify it."""
    store.append_trace_event(
        trace_id=f"trace-{time.time_ns()}",
        goal_id=goal_id,
        kind="cognition",
        payload={
            "kind": "cognition",
            "role": "evaluator",
            "model": "sonnet",
            "response_text": json.dumps({"verdict": verdict, "rationale": "test"}),
        },
    )


def test_empty_store_returns_zero_metrics(store):
    sc = compute_scorecard(store, window_hours=24)
    assert sc["tasks"]["total_terminal"] == 0
    assert "merge_rate" not in sc            # replaced by the pr block (spec 018 US2)
    assert "merged_with_pr" not in sc["tasks"]
    assert sc["pr"]["opened"] == 0 and sc["pr"]["decided_merge_rate"] is None
    assert sc["pr"]["state_as_of_ms"] is None   # never refreshed → stale, said out loud
    assert sc["workspace_breaks_tripped"] == 0
    assert sc["evaluator"]["total_calls"] == 0
    assert sc["evaluator"]["steer_rate"] == 0.0
    assert "first_pass_hit_rate" not in sc["evaluator"]  # replaced by per-goal convergence (spec 018)
    c = sc["convergence"]
    assert c["goals_closed"] == 0 and c["first_pass_rate"] is None
    # a bare StateStore has no goal tables — the degrade is an explicit note
    assert any("convergence unknown" in n for n in sc["estimate_notes"])
    assert isinstance(sc["estimate_notes"], list) and len(sc["estimate_notes"]) >= 1


def test_task_counts_and_distinct_pr_ledger(store):
    """PRs are counted by URL identity, never by task rows: three increments
    sharing one goal-branch PR are ONE opened PR (the audited 36-rows-vs-18-
    PRs collapse), and a PR is 'merged' only when the platform said so —
    pr_url presence proves nothing (spec 018 US2)."""
    _land_task(store, workspace="/w", status="done", pr_url="https://gh/x/1")
    _land_task(store, workspace="/w", status="done", pr_url="https://gh/x/1")
    _land_task(store, workspace="/w", status="done", pr_url="https://gh/x/1")
    _land_task(store, workspace="/w", status="done", pr_url="https://gh/x/2")
    _land_task(store, workspace="/w", status="done")  # review-style: no PR, moves no PR number
    _land_task(store, workspace="/w", status="failed")
    _land_task(store, workspace="/w", status="cancelled")

    sc = compute_scorecard(store, window_hours=24)
    assert sc["tasks"]["total_terminal"] == 7
    assert sc["tasks"]["done"] == 5
    assert sc["pr"]["opened"] == 2          # 4 pr-carrying rows → 2 distinct PRs
    assert sc["pr"]["open"] == 2            # never refreshed: everything still 'open'
    assert sc["pr"]["merged"] == 0          # a pr_url is NOT a merge
    assert sc["pr"]["decided_merge_rate"] is None


def test_evaluator_verdicts_and_derived_rates(store):
    for _ in range(3):
        _emit_evaluator_verdict(store, "g1", "achieved")
    for _ in range(2):
        _emit_evaluator_verdict(store, "g1", "off_track")
    _emit_evaluator_verdict(store, "g1", "on_track")

    sc = compute_scorecard(store, window_hours=24)
    v = sc["evaluator"]["verdicts"]
    assert v["achieved"] == 3
    assert v["off_track"] == 2
    assert v["on_track"] == 1
    assert v["stalled"] == 0
    assert v["needs_human"] == 0
    assert sc["evaluator"]["total_calls"] == 6
    # 2 off_track out of 6 classified → steer_rate 33.3%
    assert sc["evaluator"]["steer_rate"] == pytest.approx(2 / 6, abs=1e-4)
    # the verdict-weighted first_pass_hit_rate is GONE (spec 018): achieved
    # verdict share is not a per-goal first-pass rate
    assert "first_pass_hit_rate" not in sc["evaluator"]


def test_non_evaluator_cognition_is_ignored(store):
    """Planner / decomposer / grill cognition rows must NOT count toward the
    evaluator rollup — they share the ``cognition`` trace kind but ``role``
    is the discriminator."""
    store.append_trace_event(
        trace_id="t1", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "planner",
                 "response_text": json.dumps({"decision": "act"})},
    )
    store.append_trace_event(
        trace_id="t2", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "grill",
                 "response_text": "some prose"},
    )
    _emit_evaluator_verdict(store, "g", "achieved")

    sc = compute_scorecard(store, window_hours=24)
    assert sc["evaluator"]["total_calls"] == 1
    assert sc["evaluator"]["verdicts"]["achieved"] == 1


def test_unparseable_response_lands_in_the_unparseable_bucket(store):
    store.append_trace_event(
        trace_id="t1", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "evaluator",
                 "response_text": "the model just returned prose without JSON"},
    )
    sc = compute_scorecard(store, window_hours=24)
    assert sc["evaluator"]["total_calls"] == 1
    assert sc["evaluator"]["unparseable_responses"] == 1
    # nothing counted in verdicts, so steer/first-pass rates stay 0
    assert sum(sc["evaluator"]["verdicts"].values()) == 0


def test_window_excludes_old_rows(store):
    # A completed task backdated to 8 days ago; a 1-week window should ignore it.
    tid = "tid-old"
    store.create_task(id=tid, kind="implement_feature", workspace_dir="/w", goal="g")
    store.mark_done(tid, json.dumps({"ok": True}), pr_url="https://gh/x/1")
    old_ms = _now_ms() - int(8 * 24 * 3600 * 1000)
    with store._lock:
        store._db.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (old_ms, tid))
        store._db.commit()

    _land_task(store, workspace="/w", status="done", pr_url="https://gh/x/2")

    # backdate the old PR's ledger row alongside its task row
    with store._lock:
        store._db.execute(
            "UPDATE pr_ledger SET opened_at_ms = ? WHERE pr_url = 'https://gh/x/1'", (old_ms,))
        store._db.commit()

    sc = compute_scorecard(store, window_hours=168)
    assert sc["tasks"]["total_terminal"] == 1
    assert sc["pr"]["opened"] == 1          # only the in-window PR


def test_workspace_break_events_counted(store):
    # simulate two trip events landing at "now"
    for i in range(2):
        store.append_event(
            task_id=f"tid-{i}", program_id=None,
            type="workspace_break_tripped", source="devclaw",
            payload_json=json.dumps({"workspace_dir": "/w"}),
        )
    sc = compute_scorecard(store, window_hours=24)
    assert sc["workspace_breaks_tripped"] == 2


def test_format_scorecard_smoke(store):
    """format_scorecard must render every metric — a smoke that catches a
    silently-dropped field better than parametrizing over dict keys."""
    _land_task(store, workspace="/w", status="done", pr_url="https://gh/x/1")
    _emit_evaluator_verdict(store, "g", "achieved")
    _emit_evaluator_verdict(store, "g", "off_track")

    text = format_scorecard(compute_scorecard(store, window_hours=24))
    for token in (
        "window:", "tasks (terminal):", "PRs (distinct):",
        "workspace breaks:", "evaluator calls:", "verdicts:",
        "steer rate:", "convergence:", "estimate notes:",
    ):
        assert token in text, f"format_scorecard dropped {token!r}"


def _emit_evaluator_with_structural(store: StateStore, goal_id: str, verdict: str, grade: str) -> None:
    """Simulate a done-gate evaluator response that carries both verdict AND
    the C3 structural_health grade, in the one field the tracer writes."""
    store.append_trace_event(
        trace_id=f"trace-{time.time_ns()}",
        goal_id=goal_id,
        kind="cognition",
        payload={
            "kind": "cognition",
            "role": "evaluator",
            "model": "sonnet",
            "response_text": json.dumps(
                {"verdict": verdict, "structural_health": grade, "rationale": "test"}
            ),
        },
    )


def test_structural_grades_counted_per_done_gate_response(store):
    """C3: done-gate responses now carry structural_health. Telemetry counts
    the grade distribution; progress-check calls (no structural_health) don't
    inflate the denominator."""
    _emit_evaluator_with_structural(store, "g", "achieved", "clean")
    _emit_evaluator_with_structural(store, "g", "achieved", "clean")
    _emit_evaluator_with_structural(store, "g", "off_track", "poor")
    _emit_evaluator_with_structural(store, "g", "off_track", "concerns")
    # A progress-check response without structural_health — should NOT count.
    _emit_evaluator_verdict(store, "g", "on_track")

    sc = compute_scorecard(store, window_hours=24)
    grades = sc["evaluator"]["structural_grades"]
    assert grades == {"clean": 2, "concerns": 1, "poor": 1}
    # verdict counting still works over the full 5 responses.
    assert sc["evaluator"]["total_calls"] == 5


def test_verdict_extracted_from_full_response_text_past_preview_horizon(store):
    """The verdict sits AFTER 240 chars of rationale — the horizon the deleted
    240-char preview truncated at. ``response_text`` carries the whole
    response, so it classifies."""
    full = json.dumps({"rationale": "r" * 400, "verdict": "achieved"})
    assert '"verdict"' not in full[:240]  # premise: a 240-char cut can't see it
    store.append_trace_event(
        trace_id="t", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "evaluator", "response_text": full},
    )
    sc = compute_scorecard(store, window_hours=24)
    assert sc["evaluator"]["verdicts"]["achieved"] == 1
    assert sc["evaluator"]["unparseable_responses"] == 0


def test_response_preview_is_not_a_second_read_path(store):
    """#616 deletion guard: ``response_text`` is the ONLY field the scorecard
    reads. A row carrying a verdict solely in the deleted ``response_preview``
    key is unparseable, not a fallback — and a row whose preview disagrees
    with its text is classified by the text."""
    store.append_trace_event(
        trace_id="t1", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "evaluator",
                 "response_preview": json.dumps({"verdict": "on_track"})},
    )
    store.append_trace_event(
        trace_id="t2", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "evaluator",
                 "response_preview": json.dumps({"verdict": "stalled"}),
                 "response_text": json.dumps({"verdict": "off_track"})},
    )
    sc = compute_scorecard(store, window_hours=24)
    assert sc["evaluator"]["total_calls"] == 2
    assert sc["evaluator"]["verdicts"]["on_track"] == 0
    assert sc["evaluator"]["verdicts"]["stalled"] == 0
    assert sc["evaluator"]["verdicts"]["off_track"] == 1
    assert sc["evaluator"]["unparseable_responses"] == 1


def test_structural_grade_extracted_from_full_response_text(store):
    """The axis-B structural_health grade also rides the full text — a
    done-gate response whose grade sits past the old 240-char horizon."""
    full = json.dumps(
        {"rationale": "y" * 300, "verdict": "achieved", "structural_health": "clean"}
    )
    store.append_trace_event(
        trace_id="t", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "evaluator",
                 "response_text": full},
    )
    sc = compute_scorecard(store, window_hours=24)
    assert sc["evaluator"]["structural_grades"]["clean"] == 1


def _land_task_with_usage(store: StateStore, *, pr_url: str = "", usage: dict | None = None) -> str:
    """A done task whose result_json carries the runner's per-task usage block
    (mission-control borrow item 2) — the exact payload shape mark_done stores."""
    tid = f"tid-{time.time_ns()}"
    store.create_task(id=tid, kind="implement_feature", workspace_dir="/w", goal="g")
    result: dict = {"status": "ok"}
    if usage is not None:
        result["usage"] = usage
    store.mark_done(tid, json.dumps(result), pr_url=pr_url or None)
    return tid


def test_scorecard_usage_sums_worker_and_cognition_into_tokens_per_merged_pr(store):
    """Item 2's legibility number: the window's total token spend (cognition +
    worker) divided by merged PRs. OAuth runs report no dollar cost, so the
    dollar ratio stays None when no real cost was recorded — tokens are the
    honest cross-billing unit."""
    _land_task_with_usage(
        store, pr_url="https://gh/x/1",
        usage={"input_tokens": 1000, "output_tokens": 500, "cache_read_tokens": 0, "cost_usd": 0.0},
    )
    _land_task_with_usage(
        store, pr_url="https://gh/x/2",
        usage={"input_tokens": 300, "output_tokens": 200, "cache_read_tokens": 10, "cost_usd": 0.0},
    )
    # Legacy row without a usage block — contributes nothing, breaks nothing.
    _land_task(store, workspace="/w", status="done")
    # Cognition rows: one with REAL usage, one legacy estimate-only.
    store.append_trace_event(
        trace_id="c1", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "goal_planner",
                 "tokens_in": 400, "tokens_out": 100, "cost_usd": 0.0},
    )
    store.append_trace_event(
        trace_id="c2", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "goal_planner",
                 "tokens_in_est": 80, "tokens_out_est": 20},
    )

    # ground truth: both delivered PRs actually merged (refresh stamped)
    store.upsert_pr_states(
        {"https://gh/x/1": "merged", "https://gh/x/2": "merged"},
        as_of_ms=_now_ms(), truncated=False,
    )
    sc = compute_scorecard(store, window_hours=24)
    u = sc["usage"]
    assert u["worker_input_tokens"] == 1300
    assert u["worker_output_tokens"] == 700
    assert u["worker_cache_read_tokens"] == 10
    assert u["tasks_with_usage"] == 2
    assert u["cognition_tokens_in"] == 480  # 400 real + 80 estimated fallback
    assert u["cognition_tokens_out"] == 120
    assert u["total_tokens"] == 1300 + 700 + 480 + 120
    # 2 MERGED (ground-truth) PRs → integer tokens-per-PR; no dollar cost → None.
    assert u["tokens_per_merged_pr"] == (1300 + 700 + 480 + 120) // 2
    assert u["cost_per_merged_pr_usd"] is None


def test_scorecard_cost_per_merged_pr_when_real_cost_recorded(store):
    _land_task_with_usage(
        store, pr_url="https://gh/x/1",
        usage={"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cost_usd": 0.30},
    )
    store.append_trace_event(
        trace_id="c1", goal_id="g", kind="cognition",
        payload={"kind": "cognition", "role": "evaluator",
                 "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.10,
                 "response_text": json.dumps({"verdict": "achieved"})},
    )
    store.upsert_pr_states({"https://gh/x/1": "merged"}, as_of_ms=_now_ms(), truncated=False)
    sc = compute_scorecard(store, window_hours=24)
    u = sc["usage"]
    assert u["total_cost_usd"] == pytest.approx(0.40)
    assert u["cost_per_merged_pr_usd"] == pytest.approx(0.40)


def test_scorecard_usage_zero_prs_reports_null_ratios(store):
    _land_task_with_usage(
        store,  # done but NO pr_url → merged_with_pr stays 0
        usage={"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cost_usd": 0.0},
    )
    sc = compute_scorecard(store, window_hours=24)
    assert sc["usage"]["tokens_per_merged_pr"] is None
    assert sc["usage"]["cost_per_merged_pr_usd"] is None


def test_format_scorecard_renders_usage_and_per_pr_line(store):
    _land_task_with_usage(
        store, pr_url="https://gh/x/1",
        usage={"input_tokens": 100, "output_tokens": 50, "cache_read_tokens": 0, "cost_usd": 0.0},
    )
    text = format_scorecard(compute_scorecard(store, window_hours=24))
    assert "usage:" in text
    assert "per merged PR:" in text


def test_format_scorecard_renders_structural_when_any_reported(store):
    """format_scorecard shows structural block only when the window contained
    at least one graded response — an all-zero row would be noise."""
    _emit_evaluator_with_structural(store, "g", "achieved", "clean")
    text = format_scorecard(compute_scorecard(store, window_hours=24))
    assert "structural (done-gate only):" in text
    assert "clean" in text and "concerns" in text and "poor" in text

    # Empty-store case: no structural block.
    empty_store = StateStore(":memory:")
    try:
        empty_text = format_scorecard(compute_scorecard(empty_store, window_hours=24))
        assert "structural (done-gate only):" not in empty_text
    finally:
        empty_store.close()


# ---- per-goal convergence (spec 018 US1) -----------------------------------


def _with_goal_tables(store: StateStore, tmp_path):
    """Bootstrap the goal tables onto the SAME sqlite file (Tranche 1 shape:
    production GoalStore wraps the shared StateStore) and hand back the
    GoalStore for seeding."""
    from devclaw.goal.store import GoalStore
    return GoalStore(tmp_path / "goals", state=store)


def _seed_convergence(gs, goal_id, *, outcome, rounds, closed_at=None):
    from datetime import datetime, timezone
    gs._goal_state.record_convergence(
        goal_id, outcome=outcome, rounds=rounds, workspace_dir="/w",
        closed_at=closed_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def test_first_pass_weighs_goals_not_verdicts(store, tmp_path):
    """Named regression (spec 018 SC-003, audited 2026-08-25): a goal that
    churned 6 done-gate rounds shifts the rate by ONE goal's weight — the old
    verdict-weighted rate let it dump 5 off_track verdicts into the week."""
    gs = _with_goal_tables(store, tmp_path)
    _seed_convergence(gs, "churny", outcome="achieved", rounds=6)
    _seed_convergence(gs, "clean", outcome="achieved", rounds=1)
    # the churny goal's verdict trail must NOT move the per-goal rate
    for _ in range(5):
        _emit_evaluator_verdict(store, "churny", "off_track")
    _emit_evaluator_verdict(store, "churny", "achieved")

    sc = compute_scorecard(store, window_hours=24)
    c = sc["convergence"]
    assert c["goals_closed"] == 2
    assert c["first_pass"] == 1
    assert c["first_pass_rate"] == pytest.approx(0.5, abs=1e-4)
    assert c["rounds_median"] == pytest.approx(3.5)
    assert c["rounds_max"] == 6


def test_rounds_unknown_bucket_never_counts_as_first_pass(store, tmp_path):
    """A goal closed BEFORE the ledger existed (terminal phase-history entry,
    no convergence row) lands in rounds_unknown — never silently first-pass."""
    from datetime import datetime, timezone
    gs = _with_goal_tables(store, tmp_path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    gs._goal_state.append_phase_history("pre018", "done", now)

    sc = compute_scorecard(store, window_hours=24)
    c = sc["convergence"]
    assert c["rounds_unknown"] == 1
    assert c["goals_closed"] == 0
    assert c["first_pass_rate"] is None


def test_abandoned_goals_counted_but_not_in_convergence_denominator(store, tmp_path):
    gs = _with_goal_tables(store, tmp_path)
    _seed_convergence(gs, "killed", outcome="abandoned", rounds=0)
    _seed_convergence(gs, "done1", outcome="achieved", rounds=1)

    sc = compute_scorecard(store, window_hours=24)
    c = sc["convergence"]
    assert c["abandoned"] == 1
    assert c["goals_closed"] == 1
    assert c["first_pass_rate"] == pytest.approx(1.0)


def test_convergence_window_excludes_old_closes(store, tmp_path):
    gs = _with_goal_tables(store, tmp_path)
    _seed_convergence(gs, "ancient", outcome="achieved", rounds=1,
                      closed_at="2020-01-01T00:00:00+00:00")
    sc = compute_scorecard(store, window_hours=24)
    assert sc["convergence"]["goals_closed"] == 0
    assert sc["convergence"]["first_pass_rate"] is None  # null, never 0% or 100%


def test_zero_denominator_reports_null_not_zero(store, tmp_path):
    _with_goal_tables(store, tmp_path)  # tables exist, nothing closed
    sc = compute_scorecard(store, window_hours=24)
    c = sc["convergence"]
    assert c["first_pass_rate"] is None
    assert c["rounds_median"] is None
    assert c["rounds_max"] is None
    # tables present → the pre-018 degrade note must NOT appear
    assert not any("convergence unknown" in n for n in sc["estimate_notes"])


# ---- ground-truth PR block + bench split (spec 018 US2) --------------------


def _mark_pr(store, url, state):
    store.upsert_pr_states({url: state}, as_of_ms=_now_ms(), truncated=False)


def test_decided_merge_rate_excludes_open_and_unknown(store):
    for i, st in enumerate(["merged", "merged", "rejected", "open", "unknown"]):
        _land_task(store, workspace="/w", status="done", pr_url=f"https://gh/x/{i}")
        if st != "open":
            _mark_pr(store, f"https://gh/x/{i}", st)
    sc = compute_scorecard(store, window_hours=24)
    pr = sc["pr"]
    assert pr["opened"] == 5
    assert (pr["merged"], pr["rejected"], pr["open"], pr["unknown"]) == (2, 1, 1, 1)
    # open + unknown sit in NO rate denominator (2 / (2+1))
    assert pr["decided_merge_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert pr["state_as_of_ms"] is not None
    assert pr["refresh_truncated"] is False


def test_bench_project_moves_only_bench_figures(store, tmp_path):
    """SC-006: bench work changes no ratchet-facing number — PRs land in the
    bench sub-block, bench goals leave convergence untouched."""
    from devclaw.project_registry import ProjectRegistry

    registry = ProjectRegistry(str(tmp_path / "reg.db"))
    registry.create(id="bench-p", name="bench-p", workspace_dir="/bench-ws", bench=True)
    registry.create(id="real-p", name="real-p", workspace_dir="/real-ws")

    _land_task(store, workspace="/bench-ws", status="done", pr_url="https://gh/b/1")
    _mark_pr(store, "https://gh/b/1", "merged")
    _land_task(store, workspace="/real-ws", status="done", pr_url="https://gh/r/1")
    _mark_pr(store, "https://gh/r/1", "rejected")
    gs = _with_goal_tables(store, tmp_path)
    gs._goal_state.record_convergence(
        "bench-goal", outcome="achieved", rounds=1, workspace_dir="/bench-ws",
        closed_at=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc).isoformat(timespec="seconds"),
    )
    _seed_convergence(gs, "real-goal", outcome="achieved", rounds=2)

    sc = compute_scorecard(store, window_hours=24, registry=registry)
    pr = sc["pr"]
    assert pr["opened"] == 1 and pr["rejected"] == 1      # only the real PR
    assert pr["decided_merge_rate"] == 0.0                # 0 merged / 1 decided
    assert pr["bench"]["opened"] == 1 and pr["bench"]["merged"] == 1
    c = sc["convergence"]
    assert c["goals_closed"] == 1 and c["first_pass"] == 0  # bench goal excluded


def test_pr_block_degrades_loudly_without_refresh(store):
    _land_task(store, workspace="/w", status="done", pr_url="https://gh/x/1")
    sc = compute_scorecard(store, window_hours=24)
    assert sc["pr"]["state_as_of_ms"] is None   # never refreshed — stale, named
    assert sc["pr"]["open"] == 1                 # unrefreshed rows read as opened-only


# ---- the finish line, machine-checked (spec 018 US4) -----------------------


def _seed_cycle(store, key, *, clean, idle=0):
    store.record_cycle_report(
        cycle_key=key, window_start_ms=_now_ms() - 3600_000,
        window_end_ms=_now_ms(), clean=clean, idle=idle,
        wedges_json="[]", pauses_json="[]", summary="s", sent_at=None,
    )


def _pass_fixture(store, tmp_path):
    """Metrics just above every threshold: 3/4 first-pass (0.75 ≥ 0.70),
    merged 5 / rejected 1 (0.833 ≥ 0.80), one clean non-idle cycle."""
    gs = _with_goal_tables(store, tmp_path)
    for i, rounds in enumerate([1, 1, 1, 3]):
        _seed_convergence(gs, f"g{i}", outcome="achieved", rounds=rounds)
    for i in range(6):
        _land_task(store, workspace="/w", status="done", pr_url=f"https://gh/x/{i}")
    states = {f"https://gh/x/{i}": "merged" for i in range(5)}
    states["https://gh/x/5"] = "rejected"
    store.upsert_pr_states(states, as_of_ms=_now_ms(), truncated=False)
    _seed_cycle(store, "2026-08-24", clean=1)


def test_ratchet_passes_when_every_check_holds(store, tmp_path):
    _pass_fixture(store, tmp_path)
    sc = compute_scorecard(store, window_hours=24)
    r = sc["ratchet"]
    assert r["checks"]["first_pass_rate"]["pass"] is True
    assert r["checks"]["decided_merge_rate"]["pass"] is True
    assert r["checks"]["wedge_free_window"]["pass"] is True
    assert r["pass"] is True
    assert r["thresholds"]["first_pass_rate"] == pytest.approx(0.70)


def test_ratchet_flips_per_threshold_boundary(store, tmp_path, monkeypatch):
    _pass_fixture(store, tmp_path)
    monkeypatch.setenv("DEVCLAW_RATCHET_FIRST_PASS", "0.76")  # just above 0.75
    sc = compute_scorecard(store, window_hours=24)
    r = sc["ratchet"]
    assert r["checks"]["first_pass_rate"]["pass"] is False
    assert r["pass"] is False
    assert r["thresholds"]["first_pass_rate"] == pytest.approx(0.76)  # echoed


def test_nonclean_cycle_fails_wedge_free_check(store, tmp_path):
    _pass_fixture(store, tmp_path)
    _seed_cycle(store, "2026-08-25", clean=0)
    r = compute_scorecard(store, window_hours=24)["ratchet"]
    assert r["checks"]["wedge_free_window"]["pass"] is False
    assert r["pass"] is False


def test_idle_cycles_do_not_count_toward_wedge_free(store, tmp_path):
    _pass_fixture(store, tmp_path)
    _seed_cycle(store, "2026-08-25", clean=0, idle=1)  # idle: neither clean nor wedged
    r = compute_scorecard(store, window_hours=24)["ratchet"]
    assert r["checks"]["wedge_free_window"]["total_cycles"] == 1
    assert r["pass"] is True


def test_null_metric_never_passes_gate(store, tmp_path):
    _with_goal_tables(store, tmp_path)   # tables exist, nothing closed → nulls
    _seed_cycle(store, "2026-08-24", clean=1)
    r = compute_scorecard(store, window_hours=24)["ratchet"]
    assert r["checks"]["first_pass_rate"]["value"] is None
    assert r["checks"]["first_pass_rate"]["pass"] is False
    assert r["pass"] is False


def test_default_window_is_the_ratchet_window(store, monkeypatch):
    monkeypatch.setenv("DEVCLAW_RATCHET_WINDOW_DAYS", "7")
    sc = compute_scorecard(store)
    assert sc["window_hours"] == 7 * 24


def test_ratchet_is_informational_only():
    """No goal/tick/dispatch mechanism may read the gate verdict — the spec
    007 flip stays a human act. Structural pin: the goal layer never imports
    or references the ratchet result."""
    import pathlib

    goal_dir = pathlib.Path(__file__).resolve().parents[1] / "devclaw" / "goal"
    hits = [
        p.name for p in goal_dir.rglob("*.py")
        if "ratchet" in p.read_text()
    ]
    assert hits == [], f"goal-layer module(s) reference the ratchet: {hits}"
