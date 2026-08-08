"""The compounding scorecard (experiment P1-C): store + analysis + one graded
night. Locks the trajectory reading the whole experiment turns on — monotonic
progress vs churn (green→red) vs plateau — off the box, with scripted vectors.
"""

from __future__ import annotations

from evals.compounding import (
    NightRecord,
    analyze,
    append_night,
    load_history,
    plan_stats,
    render_report,
    run_night,
)
from evals.ledger_checklist.checklist import Check, CheckCtx, CheckResult


def _vec(green: set[str], total: int = 3) -> dict[str, bool]:
    return {f"c{i}": (f"c{i}" in green) for i in range(1, total + 1)}


def _night(n: int, green: set[str], total: int = 3, tokens: int | None = None) -> NightRecord:
    return NightRecord(night=n, ts_ms=1000 + n, repo_ref=f"sha{n}", vector=_vec(green, total), tokens=tokens)


def _ctx() -> CheckCtx:
    return CheckCtx(repo="/x", sh=lambda *a, **k: None, api=lambda *a, **k: None)


# --- store ------------------------------------------------------------------

def test_store_round_trips_in_night_order(tmp_path):
    p = tmp_path / "run.jsonl"
    append_night(p, _night(2, {"c1"}))
    append_night(p, _night(1, {"c1"}))  # appended out of order
    hist = load_history(p)
    assert [r.night for r in hist] == [1, 2]  # sorted by night
    assert hist[0].vector == {"c1": True, "c2": False, "c3": False}


def test_load_history_missing_file_is_empty(tmp_path):
    assert load_history(tmp_path / "nope.jsonl") == []


# --- analysis ---------------------------------------------------------------

def test_empty_history_is_no_data():
    r = analyze([])
    assert r.verdict == "no-data"
    assert r.nights == 0


def test_single_night_is_started_with_its_greens_as_new():
    r = analyze([_night(1, {"c1", "c2"})])
    assert r.verdict == "started"
    assert r.new_green == ["c1", "c2"]
    assert r.churned == []
    assert r.monotonic is True
    assert r.latest_green == 2 and r.total_criteria == 3


def test_added_green_reads_as_compounding():
    hist = [_night(1, {"c1"}), _night(2, {"c1", "c2"})]
    r = analyze(hist)
    assert r.verdict == "compounding"
    assert r.new_green == ["c2"]
    assert r.churned == []
    assert r.monotonic is True


def test_green_going_red_is_churn_and_breaks_monotonic():
    hist = [_night(1, {"c1", "c2"}), _night(2, {"c1"})]
    r = analyze(hist)
    assert r.verdict == "churn"
    assert r.churned == ["c2"]
    assert r.monotonic is False


def test_flat_green_for_k_working_nights_is_plateau():
    hist = [_night(1, {"c1"}), _night(2, {"c1"}), _night(3, {"c1"})]
    r = analyze(hist)
    assert r.plateau_nights == 2  # nights 2 and 3 added nothing
    assert r.verdict == "plateau"
    assert r.monotonic is True  # flat is non-decreasing — plateau, not churn


def test_all_green_is_closed():
    hist = [_night(1, {"c1", "c2"}), _night(2, {"c1", "c2", "c3"})]
    r = analyze(hist)
    assert r.reached_all_green is True
    assert r.verdict == "closed"


def test_idle_night_does_not_count_toward_plateau():
    # Night 2 did no work (tokens=0) — it must not be read as a plateau night.
    hist = [_night(1, {"c1"}), _night(2, {"c1"}, tokens=0), _night(3, {"c1", "c2"})]
    r = analyze(hist)
    assert r.verdict == "compounding"  # night 3 added c2; the idle night is skipped
    assert r.plateau_nights == 0


# --- one graded night -------------------------------------------------------

def test_run_night_wraps_checklist_into_a_record():
    checks = [
        Check("c1", "a", lambda ctx: CheckResult(True, "green")),
        Check("c2", "b", lambda ctx: CheckResult(False, "red")),
    ]
    rec = run_night(_ctx(), night=4, repo_ref="deadbeef", checks=checks, tokens=1234, ts_ms=999)
    assert rec.night == 4 and rec.repo_ref == "deadbeef" and rec.ts_ms == 999
    assert rec.tokens == 1234
    assert rec.vector == {"c1": True, "c2": False}
    assert rec.green_count() == 1


def test_run_night_fails_closed_on_a_crashing_probe():
    def _boom(ctx):
        raise RuntimeError("no toolchain")

    checks = [Check("c1", "x", _boom)]
    rec = run_night(_ctx(), night=1, repo_ref="h", checks=checks, ts_ms=1)
    assert rec.vector == {"c1": False}  # crash → red, never a phantom green


# --- report -----------------------------------------------------------------

def test_render_report_smoke():
    hist = [_night(1, {"c1"}), _night(2, {"c1", "c2"})]
    out = render_report(analyze(hist), hist)
    assert "COMPOUNDING" in out
    assert "| night |" in out
    assert "c2" in out


# --- PLAN.md size tripwire (the huge-plan fear, made a watchable number) -----

def test_plan_stats_counts_only_milestone_section_checkboxes():
    plan = (
        "## Destination\ndone\n\n"
        "## Milestones\n- [x] scaffold\n- [ ] auth\n- [ ] ui\n\n"
        "## Tasks — auth\n- [ ] login endpoint\n- [x] jwt helper\n"
    )
    s = plan_stats(plan)
    assert s["plan_milestones"] == 3  # the Tasks-section checkboxes are NOT milestones
    assert s["plan_milestones_done"] == 1  # only the one [x] milestone
    assert s["plan_lines"] == len(plan.splitlines())
    assert s["plan_bytes"] == len(plan.encode("utf-8"))


def test_plan_stats_empty_and_no_milestones_section():
    assert plan_stats("")["plan_milestones"] == 0
    assert plan_stats("## Notes\njust prose, no milestones\n")["plan_milestones"] == 0


def test_run_night_records_plan_size_from_repo(tmp_path):
    (tmp_path / "PLAN.md").write_text("## Milestones\n- [x] a\n- [ ] b\n", encoding="utf-8")
    ctx = CheckCtx(repo=str(tmp_path), sh=lambda *a, **k: None, api=lambda *a, **k: None)
    checks = [Check("c1", "a", lambda c: CheckResult(True, "green"))]
    rec = run_night(ctx, night=1, repo_ref="h", checks=checks, ts_ms=1)
    assert rec.plan_milestones == 2 and rec.plan_milestones_done == 1
    assert rec.plan_lines == 3 and rec.plan_bytes is not None


def test_run_night_plan_fields_none_when_no_plan_md():
    # _ctx() repo="/x" has no PLAN.md — the tripwire is best-effort, never fails a night
    checks = [Check("c1", "a", lambda c: CheckResult(True, "green"))]
    rec = run_night(_ctx(), night=1, repo_ref="h", checks=checks, ts_ms=1)
    assert rec.plan_lines is None and rec.plan_milestones is None


def test_old_record_without_plan_fields_still_loads(tmp_path):
    # a pre-tripwire JSONL line (no plan_* keys) must still load — defaults fill in
    p = tmp_path / "run.jsonl"
    p.write_text(
        '{"night": 1, "ts_ms": 1, "repo_ref": "h", "vector": {"c1": true}, "tokens": null}\n',
        encoding="utf-8",
    )
    hist = load_history(p)
    assert len(hist) == 1 and hist[0].plan_lines is None


def test_render_report_surfaces_the_plan_line():
    rec = NightRecord(
        night=1, ts_ms=1, repo_ref="h", vector={"c1": True},
        plan_lines=42, plan_milestones=5, plan_milestones_done=2,
    )
    out = render_report(analyze([rec]), [rec])
    assert "PLAN.md" in out and "42 lines" in out and "2/5 milestones" in out
