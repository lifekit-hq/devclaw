"""Direction evaluator — the JSON contract + verdict-mapping safety nets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from devclaw.goal.evaluator import GoalEvalError, build_prompt, evaluate, extract_json, validate
from devclaw.goal.models import Goal, GoalStatus


def test_extract_and_bad_verdict():
    assert '"verdict"' in extract_json('{"verdict":"achieved"}')
    with pytest.raises(GoalEvalError):
        validate({"verdict": "vibes"})
    with pytest.raises(GoalEvalError):
        validate("not a dict")


def test_each_valid_verdict():
    for v in ("on_track", "achieved", "stalled"):
        assert validate({"verdict": v, "rationale": "x"}).verdict == v


def test_off_track_requires_corrections_else_softened():
    # off_track WITH corrections stays off_track
    r = validate({"verdict": "off_track", "rationale": "drifting", "corrections": ["redo X"]})
    assert r.verdict == "off_track"
    assert r.corrections == ["redo X"]
    # off_track with NO corrections is softened to on_track (not actionable)
    r2 = validate({"verdict": "off_track", "rationale": "meh", "corrections": []})
    assert r2.verdict == "on_track"


def test_needs_human_backfills_question_from_rationale():
    r = validate({"verdict": "needs_human", "rationale": "which cloud provider?"})
    assert r.verdict == "needs_human"
    assert r.question == "which cloud provider?"


def _goal():
    return Goal(
        id="g", objective="ship a health endpoint", cadence="1d", engine="devclaw",
        workspace_dir="/ws", done_when="/health returns 200 and is tested",
        backlog=["add /health"],
    )


def test_done_gate_prompt_includes_review():
    prompt = build_prompt(
        _goal(), GoalStatus(), "log", "deliveries",
        review_report="the repo has /health and a passing test", at_done_gate=True,
    )
    assert "DONE-GATE" in prompt
    assert "read-only review" in prompt.lower()
    assert "the repo has /health" in prompt


@pytest.mark.asyncio
async def test_evaluate_roundtrip_with_injected_caller():
    calls = {"n": 0}

    async def caller(prompt: str) -> str:
        calls["n"] += 1
        return json.dumps({"verdict": "achieved", "rationale": "done_when met: /health tested"})

    ev = await evaluate(_goal(), GoalStatus(), "log", "deliveries", claude_caller=caller)
    assert ev.verdict == "achieved"
    assert calls["n"] == 1


# ── done-gate de-fat (DONEGATE_LEAN) — omit the re-fed diary at the done-gate ──

_BIG_LOG = "an event happened\n" * 3000        # ~fat re-fed history
_BIG_DELIVERIES = "shipped a PR\n" * 3000        # ~fat delivery record


def test_done_gate_lean_omits_diary_but_keeps_grounding():
    lean = build_prompt(
        _goal(), GoalStatus(), _BIG_LOG, _BIG_DELIVERIES,
        review_report="## Per-clause evidence\n/health exists and is tested",
        repo_context="remote: github.com/o/r\nhead: abc123",
        at_done_gate=True, lean_done_gate=True,
    )
    # the two diary sections are gone
    assert "## Recent event log" not in lean
    assert "## What has actually shipped" not in lean
    assert "an event happened" not in lean and "shipped a PR" not in lean
    # the omission is stated, not silent
    assert "intentionally omitted at this gate" in lean
    # grounding the gate actually needs is still present
    assert "done_when:" in lean
    assert "Repository context" in lean
    assert "Fresh read-only review" in lean and "/health exists" in lean


def test_done_gate_lean_off_keeps_the_diary_unchanged():
    full = build_prompt(
        _goal(), GoalStatus(), _BIG_LOG, _BIG_DELIVERIES,
        at_done_gate=True, lean_done_gate=False,
    )
    assert "## Recent event log" in full
    assert "## What has actually shipped" in full
    assert "an event happened" in full and "shipped a PR" in full


def test_lean_only_applies_at_the_done_gate():
    """The on-demand direction check (at_done_gate=False) still gets the history —
    lean is a done-gate-only de-fat, never a blanket context strip."""
    prompt = build_prompt(
        _goal(), GoalStatus(), _BIG_LOG, _BIG_DELIVERIES,
        at_done_gate=False, lean_done_gate=True,
    )
    assert "## Recent event log" in prompt
    assert "## What has actually shipped" in prompt


def test_lean_done_gate_is_a_large_byte_cut_of_refed_data():
    """The point, measured with the anatomy decoder: leaning drops the log +
    deliveries data sections and shrinks the prompt by the diary's bulk."""
    from devclaw.server.prompt_anatomy import anatomize

    args = (_goal(), GoalStatus(), _BIG_LOG, _BIG_DELIVERIES)
    kw = dict(review_report="## Per-clause evidence\nok",
              repo_context="remote: x", at_done_gate=True)
    full = build_prompt(*args, lean_done_gate=False, **kw)
    lean = build_prompt(*args, lean_done_gate=True, **kw)
    assert len(lean) < len(full) - 40_000        # ~two 24K-capped blocks gone
    lean_kinds = {s.data_kind for s in anatomize(lean, "evaluator").sections if s.category == "data"}
    assert "log" not in lean_kinds and "deliveries" not in lean_kinds
    full_kinds = {s.data_kind for s in anatomize(full, "evaluator").sections if s.category == "data"}
    assert {"log", "deliveries"} <= full_kinds


@pytest.mark.asyncio
async def test_evaluate_reads_the_donegate_lean_env_flag_at_call_time(monkeypatch):
    """evaluate() defers to the DONEGATE_LEAN module flag (read at call time, so a
    box env flip takes effect without a re-import) — and only at the done-gate."""
    import devclaw.goal.evaluator as ev_mod

    seen: dict[str, str] = {}

    async def caller(prompt: str) -> str:
        seen["prompt"] = prompt
        return json.dumps({"verdict": "off_track", "rationale": "x",
                           "corrections": ["[clause 1] add the test"]})

    monkeypatch.setattr(ev_mod, "DONEGATE_LEAN", True)
    await evaluate(_goal(), GoalStatus(), _BIG_LOG, _BIG_DELIVERIES,
                   claude_caller=caller, review_report="## Per-clause evidence\nok",
                   at_done_gate=True)
    assert "## Recent event log" not in seen["prompt"]

    monkeypatch.setattr(ev_mod, "DONEGATE_LEAN", False)
    await evaluate(_goal(), GoalStatus(), _BIG_LOG, _BIG_DELIVERIES,
                   claude_caller=caller, review_report="## Per-clause evidence\nok",
                   at_done_gate=True)
    assert "## Recent event log" in seen["prompt"]


def test_review_report_extraction_skips_prompt_template_and_uses_filled_section():
    """The worker's captured stdout starts with a panel echoing the agent's
    brief — which itself contains the literal template ``## Per-clause
    evidence`` followed by ``1. <clause 1 text>`` placeholders. The truncation
    must skip past that template and find the LAST (filled-in) per-clause
    section. Otherwise the evaluator reads the empty template + early
    `status=pending` tool-calls and reports 'review was cut off
    mid-exploration' (closeloop-ui-coverage 2026-06-28 incident)."""
    template = (
        "Message from User panel\n"
        "## Per-clause evidence\n"
        "1. <clause 1 text>\n"
        "   satisfied: yes | no | partial\n"
        "   evidence: <specific files/symbols/tests>\n"
    )
    decoration = "ACP Tool Call\nls -la /workspace\nstatus=pending\n" * 50
    actual_report = (
        "## Per-clause evidence\n"
        "1. /health endpoint exists\n"
        "   satisfied: yes\n"
        "   evidence: app/routes.py:42 health_handler covered by tests/test_health.py:8\n"
        "\n## Summary\nAll clauses satisfied.\n"
    )
    raw = template + decoration + actual_report
    prompt = build_prompt(
        _goal(), GoalStatus(), "log", "deliveries",
        review_report=raw, at_done_gate=True,
    )
    # the actual evidence — specific file/line — reaches the evaluator
    assert "app/routes.py:42 health_handler" in prompt
    assert "Summary" in prompt
    # the early `status=pending` decoration is excluded from the head-truncation
    assert "status=pending" not in prompt


def test_review_report_extraction_falls_back_to_tail_when_no_header():
    """Truly cut-off runs (no ``## Per-clause evidence`` ever emitted) must
    still surface SOME signal — the tail, where the most recent tool-call
    output and any partial work-in-progress lives. Head-truncation always
    showed only banner + prompt echo, never the agent's actual exploration."""
    early_banner = "Message from User\n" + ("x" * 5000)
    actual_work_at_end = (
        "I started exploring but ran into a permissions error reading "
        "/workspace/.env — DETAILS HERE for the evaluator to act on."
    )
    raw = early_banner + actual_work_at_end
    prompt = build_prompt(
        _goal(), GoalStatus(), "log", "deliveries",
        review_report=raw, at_done_gate=True,
    )
    assert "DETAILS HERE for the evaluator to act on" in prompt


def test_review_report_extraction_handles_empty_input():
    """Defensive: an empty / None review_report path must not crash and must
    not inject an empty section header into the prompt."""
    from devclaw.goal.evaluator import _extract_review_report
    assert _extract_review_report("") == ""


def test_done_gate_prompt_includes_spec_when_present():
    prompt = build_prompt(
        _goal(), GoalStatus(), "log", "deliveries",
        review_report="repo has /health", at_done_gate=True,
        spec="MUST expose /health AND /ready; auth required.",
    )
    assert "Agreed spec" in prompt and "/ready" in prompt


def test_evaluator_prompt_carries_anti_inference_clause():
    """The grounding rule (review-gate.md's shape, #227) lives in the BASE
    prompt — present on EVERY path, not just the done-gate: mid-flight and
    on-demand evals write "corrections" into steering, and a wrong-stack
    inference there becomes wasted tasks or a false stalled/needs_human block
    (triage F3). The rendered `## Repository context` section carries the
    first-hand facts; absent/empty context omits the section, never fakes it."""
    plain = build_prompt(_goal(), GoalStatus(), "log", "deliveries")
    gate = build_prompt(
        _goal(), GoalStatus(), "log", "deliveries",
        review_report="repo has /health", at_done_gate=True,
        repo_context="workspace_dir: /ws\nglobal.json: file",
    )
    for prompt in (plain, gate):
        assert "Do NOT infer repository facts" in prompt
        assert "UNKNOWN" in prompt
    assert "## Repository context" in gate
    assert "global.json: file" in gate
    assert "## Repository context" not in plain   # no snapshot → section omitted


# ---- per-clause evidence contract (the 2026-06-25 trash-PR safety net) ----


def test_done_gate_prompt_carries_structural_health_axis():
    """The done-gate evaluator must consider TWO axes: functional clauses AND
    structural health. Without the second axis, a goal can verdict ``achieved``
    while leaving the codebase worse than before (closeloop App.tsx grew to
    1827 LOC through 4 such PRs in late June 2026). The prompt now tells the
    evaluator: both axes must pass before returning achieved."""
    prompt = build_prompt(
        _goal(), GoalStatus(), "log", "deliveries", at_done_gate=True,
        review_report=(
            "## Per-clause evidence\n1. health\n   satisfied: yes\n   evidence: app/main.py\n"
            "## Structural health\nverdict: clean\nNo concerns.\n"
        ),
    )
    text = prompt.lower()
    assert "structural" in text
    assert "## structural health" in prompt.lower() or "structural health" in text
    assert "both axes" in text  # the load-bearing rule
    # The exemplar that motivates the second axis must be named so the model
    # remembers WHY it's grading structure, not just THAT it should.
    assert "1827" in prompt or "monolith" in text or "closeloop" in text


def test_goal_evaluator_prompt_carries_clause_decomposition_directive():
    """The prompt MUST tell the model to decompose done_when into atomic
    clauses and demand specific repo evidence per clause — this is the
    behaviour that prevents the 'all stubs counted as done' failure mode."""
    prompt = build_prompt(_goal(), GoalStatus(), "log", "deliveries", at_done_gate=True)
    assert "DECOMPOSE" in prompt or "decompose" in prompt.lower()
    assert "atomic clauses" in prompt.lower() or "atomic clause" in prompt.lower()
    assert "evidence" in prompt.lower()
    # the strict rule that prevents the trash-PR class
    assert "achieved" in prompt.lower()


def test_done_gate_achieved_without_clauses_is_downgraded():
    # Belt-and-suspenders: even with a strict prompt the model can claim
    # 'achieved' without producing per-clause evidence. The validator
    # downgrades to off_track with a forcing correction.
    r = validate(
        {"verdict": "achieved", "rationale": "looks good"},
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert r.corrections, "expected a forcing correction asking for clauses"
    assert "clause" in r.corrections[0].lower()


def test_done_gate_achieved_with_unsatisfied_clause_is_downgraded():
    # 'achieved' with at least one unsatisfied clause must downgrade and
    # surface a per-clause correction.
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "shipped",
            "clauses": [
                {
                    "clause": "/health returns 200",
                    "satisfied": True,
                    "evidence": "src/Health.cs:12 returns OK; HealthTests.cs:8 asserts 200",
                },
                {
                    "clause": "/health is tested",
                    "satisfied": False,
                    "evidence": "missing — should live in tests/HealthTests.cs",
                },
            ],
        },
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    # the unsatisfied clause must surface as a correction
    assert any("/health is tested" in c for c in r.corrections)
    # clauses are preserved on the result for downstream visibility
    assert len(r.clauses) == 2
    assert r.clauses[1].satisfied is False


def test_done_gate_achieved_with_partial_evidence_is_downgraded():
    # 'partial' (string) coerces to satisfied=False — partial doesn't count.
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "mostly",
            "clauses": [
                {"clause": "feature A", "satisfied": "yes", "evidence": "src/A.cs"},
                {"clause": "feature B", "satisfied": "partial", "evidence": "src/B.cs (incomplete)"},
            ],
        },
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert any("feature B" in c for c in r.corrections)


def test_done_gate_achieved_clause_with_no_evidence_is_downgraded():
    # satisfied=True but empty evidence → still downgraded (evidence contract).
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "shipped",
            "clauses": [
                {"clause": "feature A", "satisfied": True, "evidence": ""},
            ],
        },
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert any("feature A" in c for c in r.corrections)


def test_done_gate_achieved_with_all_clauses_satisfied_stays_achieved():
    # The HAPPY path: every clause satisfied with real evidence → achieved.
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "all clauses met",
            "clauses": [
                {
                    "clause": "/health returns 200",
                    "satisfied": True,
                    "evidence": "src/Health.cs:12; HealthTests.cs:8",
                },
                {
                    "clause": "/health is tested",
                    "satisfied": True,
                    "evidence": "HealthTests.cs:8 Health_Returns200",
                },
            ],
        },
        at_done_gate=True,
    )
    assert r.verdict == "achieved"
    assert len(r.clauses) == 2
    assert all(c.satisfied for c in r.clauses)


def test_pre_done_gate_achieved_is_not_strict():
    # Outside the done-gate, achieved doesn't require clauses (mid-goal
    # evaluator never returns achieved in practice, but the validator must
    # not reject it). Behaviour stays as the existing soft contract.
    r = validate({"verdict": "achieved", "rationale": "wip"})
    assert r.verdict == "achieved"


def test_off_track_at_done_gate_preserves_clauses():
    # When the model itself returns off_track (with corrections), the clauses
    # it produced are still preserved for visibility.
    r = validate(
        {
            "verdict": "off_track",
            "rationale": "one clause missing",
            "corrections": ["[clause 2] add the missing endpoint"],
            "clauses": [
                {"clause": "feature A", "satisfied": True, "evidence": "src/A.cs"},
                {"clause": "feature B", "satisfied": False, "evidence": "missing"},
            ],
        },
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert len(r.clauses) == 2
    assert r.corrections == ["[clause 2] add the missing endpoint"]


def test_eval_prompt_omits_spec_section_when_absent():
    prompt = build_prompt(_goal(), GoalStatus(), "log", "deliveries")
    assert "Agreed spec" not in prompt


def test_eval_prompt_carries_decisions_block_only_when_given():
    """Spec 031 US4 (#234 shape): the `## Decisions` grounding block is present
    iff a rendered section is passed. The raw template must not contain the
    literal header, or the absence half is vacuous — the prose refers to the
    block as *Decisions* without the `##`."""
    import pathlib
    template = pathlib.Path(__file__).resolve().parents[1] / "devclaw/prompts/goal-evaluator.md"
    assert "## Decisions" not in template.read_text(encoding="utf-8")
    without = build_prompt(_goal(), GoalStatus(), "log", "deliveries")
    assert "## Decisions" not in without
    section = "Decisions on this goal — settled by the owner (1 current):\n- [clause: \"c1\"] → decide: accept the gap and close (owner, 2026-09-02)"
    with_ = build_prompt(_goal(), GoalStatus(), "log", "deliveries", decisions=section)
    assert "## Decisions" in with_ and "accept the gap and close" in with_


def test_clause_resolved_by_decision_counts_as_satisfied():
    """Spec 031 FR-011: a clause the gate grades with a `resolved_by` id counts
    as satisfied and carries the id; a malformed value is ignored (#233)."""
    from devclaw.goal.evaluator import _parse_clauses
    clauses = _parse_clauses([
        {"clause": "c1", "satisfied": False, "evidence": "", "resolved_by": "dec_abc"},
        {"clause": "c2", "satisfied": False, "evidence": "missing — x", "resolved_by": 42},
    ])
    assert clauses[0].satisfied is True and clauses[0].resolved_by == "dec_abc"
    assert "dec_abc" in clauses[0].evidence
    assert clauses[1].satisfied is False and clauses[1].resolved_by == ""


# ---- stub-policy enforcement (the 2026-06-26 v5 safety net) ---------------
#
# Backstory: finance-sentry-mcp-v5 shipped 4 `not_yet_available` stubs for
# capabilities the repo didn't have (cashflow, crypto-pnl, tax-lots, net-worth-
# history). Every per-item gate passed (narrow verify_cmd) and the done-gate
# evaluator stamped them satisfied because the prompt previously endorsed
# "legitimate stubs". The fix: stubs are only acceptable when the goal's
# `stub_acceptable` list NAMES the tool. The validator enforces this
# mechanically as a belt-and-suspenders backup to the prompt rule.


def _goal_with_stub_acceptable(allowed: list[str]) -> Goal:
    return Goal(
        id="g", objective="ship a finance MCP", cadence="1d", engine="devclaw",
        workspace_dir="/ws",
        done_when=(
            "expose get_account_summary, get_cashflow_report, and get_tax_lots "
            "as MCP tools backed by authoritative reads"
        ),
        backlog=["scaffold mcp", "wire tools"],
        stub_acceptable=allowed,
    )


def test_eval_prompt_renders_stub_acceptable_block_when_populated():
    prompt = build_prompt(
        _goal_with_stub_acceptable(["get_cashflow_report", "get_tax_lots"]),
        GoalStatus(), "log", "deliveries", at_done_gate=True,
    )
    assert "stub_acceptable" in prompt
    assert "get_cashflow_report" in prompt
    assert "get_tax_lots" in prompt


def test_eval_prompt_warns_when_stub_acceptable_empty():
    # The empty case must be LOUD — the prompt actively warns the model so it
    # doesn't fall back to "stubs are basically fine" priors.
    prompt = build_prompt(_goal(), GoalStatus(), "log", "deliveries", at_done_gate=True)
    assert "stub_acceptable" in prompt
    assert "empty" in prompt.lower()
    assert "not authorized" in prompt.lower()


def test_unauthorized_stub_clause_is_downgraded_at_done_gate():
    # The exact v5 failure pattern: model returns satisfied=True for a clause
    # whose ONLY evidence is a not_yet_available stub. No stub_acceptable on
    # the goal → validator must flip it and surface the policy violation in
    # the correction.
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "all 3 tools implemented",
            "clauses": [
                {
                    "clause": "get_account_summary returns authoritative data",
                    "satisfied": True,
                    "evidence": "Tools/GetAccountSummaryTool.cs:14 dispatches IBankingAccountsReader",
                },
                {
                    "clause": "get_cashflow_report returns a cashflow report",
                    "satisfied": True,
                    "evidence": "Tools/Stubs/CashflowReportStub.cs:14 returns NotYetAvailablePayload(\"not_yet_available\", \"...\")",
                },
            ],
        },
        at_done_gate=True,
        stub_acceptable=[],
    )
    assert r.verdict == "off_track"
    # the stub clause is flipped to unsatisfied with the policy reason
    cashflow = next(c for c in r.clauses if "cashflow" in c.clause)
    assert cashflow.satisfied is False
    assert "unauthorized stub" in cashflow.evidence.lower()
    # the real clause is preserved untouched
    summary = next(c for c in r.clauses if "summary" in c.clause)
    assert summary.satisfied is True
    # the correction names the unsatisfied clause so the planner can act
    assert any("cashflow" in c.lower() for c in r.corrections)


def test_authorized_stub_clause_stays_satisfied_at_done_gate():
    # Same shape as the previous test but stub_acceptable explicitly names
    # the cashflow tool → owner opted in → clause stays satisfied → verdict
    # remains achieved.
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "1 real tool, 1 authorized stub",
            "clauses": [
                {
                    "clause": "get_account_summary returns authoritative data",
                    "satisfied": True,
                    "evidence": "Tools/GetAccountSummaryTool.cs:14 dispatches IBankingAccountsReader",
                },
                {
                    "clause": "get_cashflow_report returns a cashflow report",
                    "satisfied": True,
                    "evidence": "Tools/Stubs/CashflowReportStub.cs:14 returns NotYetAvailablePayload(\"not_yet_available\", \"...\")",
                },
            ],
        },
        at_done_gate=True,
        stub_acceptable=["get_cashflow_report"],
    )
    assert r.verdict == "achieved"
    assert all(c.satisfied for c in r.clauses)


def test_authorized_stub_matched_by_substring_not_just_exact():
    # Tool-slug authorization is substring (case-insensitive) — the clause
    # text says "get_tax_lots tool" not "get_tax_lots" verbatim; the
    # evidence is a *Stub class name. Both forms should be enough to match
    # the stub_acceptable entry.
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "authorized stub",
            "clauses": [
                {
                    "clause": "the get_tax_lots tool is exposed",
                    "satisfied": True,
                    "evidence": "Stubs/TaxLotsStub.cs:9 returns not_yet_available",
                },
            ],
        },
        at_done_gate=True,
        stub_acceptable=["GET_TAX_LOTS"],  # case-insensitive
    )
    assert r.verdict == "achieved"


def test_stub_policy_no_op_when_no_stub_markers_in_evidence():
    # A clause whose evidence is real symbols (no stub markers) is
    # unaffected by the stub policy even if stub_acceptable is empty.
    r = validate(
        {
            "verdict": "achieved",
            "rationale": "real wiring",
            "clauses": [
                {
                    "clause": "get_account_summary returns data",
                    "satisfied": True,
                    "evidence": "Tools/GetAccountSummaryTool.cs:14 dispatches IBankingAccountsReader",
                },
            ],
        },
        at_done_gate=True,
        stub_acceptable=[],
    )
    assert r.verdict == "achieved"


def test_stub_policy_only_applies_at_done_gate():
    # Outside the done-gate the policy is dormant — pre-done-gate ticks
    # shouldn't downgrade evidence the planner is mid-shipping.
    r = validate(
        {
            "verdict": "achieved",  # nonsense pre-done-gate but accepted as-is
            "rationale": "wip",
            "clauses": [
                {
                    "clause": "get_cashflow_report",
                    "satisfied": True,
                    "evidence": "CashflowReportStub.cs returns not_yet_available",
                },
            ],
        },
        at_done_gate=False,
        stub_acceptable=[],
    )
    assert r.verdict == "achieved"


@pytest.mark.asyncio
async def test_evaluate_threads_stub_acceptable_through_to_validate():
    # End-to-end at the function level: evaluate() must pull stub_acceptable
    # off the goal and pass it to validate() — otherwise the policy is
    # unenforced in production despite the unit tests passing.
    goal = _goal_with_stub_acceptable([])  # no stubs allowed

    async def caller(_prompt: str) -> str:
        return json.dumps({
            "verdict": "achieved",
            "rationale": "shipped",
            "clauses": [
                {
                    "clause": "get_cashflow_report",
                    "satisfied": True,
                    "evidence": "CashflowReportStub.cs returns not_yet_available",
                },
            ],
        })

    r = await evaluate(
        goal, GoalStatus(), "log", "deliveries",
        claude_caller=caller, at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert "cashflow" in r.corrections[0].lower()


# ---- execution-evidence enforcement for test clauses (2026-07-06 benchmark) -
#
# Backstory: closeloop-bench-2026-07-05's verify.sh asserted the Playwright
# spec files EXISTED (grep-shaped check()) but never executed them, and the
# done-gate stamped the test clause green. Presence is not coverage: a
# test-shaped clause needs run evidence (output, counts, gate log) to satisfy.


def _achieved_with(clauses: list[dict]) -> dict:
    return {"verdict": "achieved", "rationale": "all met", "clauses": clauses}


def test_test_clause_with_existence_only_evidence_is_downgraded():
    r = validate(
        _achieved_with([
            {
                "clause": "the walking skeleton is covered by a Playwright E2E test",
                "satisfied": True,
                "evidence": "tests/e2e/walking-skeleton.spec.ts exists; verify.sh asserts the file is present",
            },
        ]),
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert not r.clauses[0].satisfied
    assert "existence-only" in r.clauses[0].evidence
    assert len(r.corrections) == 1


def test_test_clause_with_run_evidence_stays_achieved():
    r = validate(
        _achieved_with([
            {
                "clause": "/health is tested",
                "satisfied": True,
                "evidence": "HealthTests.cs:8 Health_Returns200 — suite passes in the verify gate (14 tests)",
            },
        ]),
        at_done_gate=True,
    )
    assert r.verdict == "achieved"
    assert r.clauses[0].satisfied


def test_non_test_clause_with_exists_evidence_is_untouched():
    # "exists" is fine evidence for a non-test clause (docs, infra files).
    r = validate(
        _achieved_with([
            {
                "clause": "a multi-stage production Dockerfile is present at the repo root",
                "satisfied": True,
                "evidence": "Dockerfile exists at repo root with two FROM stages and a production CMD",
            },
        ]),
        at_done_gate=True,
    )
    assert r.verdict == "achieved"
    assert r.clauses[0].satisfied


def test_existence_only_net_applies_only_at_done_gate():
    r = validate(
        {"verdict": "on_track", "rationale": "progress", "clauses": [
            {
                "clause": "the flow is tested end to end",
                "satisfied": True,
                "evidence": "spec files exist under tests/e2e/",
            },
        ]},
    )
    # pre-done-gate: recorded as-is; the net only guards the CLOSE.
    assert r.verdict == "on_track"
    assert r.clauses[0].satisfied


def test_evaluator_prompt_names_the_existence_is_not_execution_rule():
    prompt = build_prompt(_goal(), GoalStatus(), "log", "deliveries", at_done_gate=True)
    assert "EXECUTED" in prompt
    assert "presence, not coverage" in prompt


# ---- mechanical run evidence outranks wording (2026-08-25 no-op round) ------
#
# devclaw-auth-ping-path-2026-08-25 round 1: the evaluator returned achieved,
# the increment's verify gate had run the full suite green in-sandbox, and the
# existence-only regex still flipped the test clause on the words "test exists"
# — burning a full dispatch round whose only output was reworded evidence. The
# host-written `Verify gate …: PASSED` marker in the deliveries tail is the
# mechanical fact; when it's present and green, wording must not flip a clause.

_EXISTENCE_WORDED_TEST_CLAUSE = {
    "clause": "the behavior is pinned by the named regression test",
    "satisfied": True,
    "evidence": "tests/test_x.py:12 — test exists, seeds the state and asserts all four properties",
}


def test_existence_only_flip_yields_to_mechanical_verify_evidence():
    r = validate(
        _achieved_with([dict(_EXISTENCE_WORDED_TEST_CLAUSE)]),
        at_done_gate=True,
        verified_execution=True,
    )
    assert r.verdict == "achieved"
    assert r.clauses[0].satisfied


def test_existence_only_flip_still_applies_without_run_evidence():
    # default (no mechanical evidence) — behavior byte-identical to before
    r = validate(
        _achieved_with([dict(_EXISTENCE_WORDED_TEST_CLAUSE)]),
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert not r.clauses[0].satisfied
    assert "existence-only" in r.clauses[0].evidence


def test_verified_execution_derived_from_last_gate_marker():
    from devclaw.goal.evaluator import _deliveries_verified_execution

    passed = "PR: x\n\nVerify gate `pytest -q`: PASSED\n\nGate output (tail):\nok"
    failed_after_pass = passed + "\n\nVerify gate `pytest -q`: FAILED\n"
    assert _deliveries_verified_execution(passed) is True
    assert _deliveries_verified_execution(failed_after_pass) is False
    assert _deliveries_verified_execution("no markers here") is False
    assert _deliveries_verified_execution("") is False
    # the worker's own prose never matches: the marker is anchored at column 0
    inline = "Agent summary:\n  they said Verify gate `x`: PASSED somewhere"
    assert _deliveries_verified_execution(inline) is False


def test_mechanical_downgrade_rationale_states_the_flip():
    # An achieved-arguing model rationale must never stand alone next to the
    # downgraded off_track verdict (the round-1 log contradiction).
    model_rationale = "All clauses have specific, repo-confirmed evidence"
    r = validate(
        {
            "verdict": "achieved",
            "rationale": model_rationale,
            "clauses": [dict(_EXISTENCE_WORDED_TEST_CLAUSE)],
        },
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert r.rationale != model_rationale
    assert "downgraded from 'achieved'" in r.rationale
    # the no-clauses downgrade branch states the flip too
    r2 = validate(
        {"verdict": "achieved", "rationale": model_rationale, "clauses": []},
        at_done_gate=True,
    )
    assert r2.verdict == "off_track"
    assert "downgraded from 'achieved'" in r2.rationale


# ---- standing-goal contract (the 2026-07-06 benchmark safety net) ----------
#
# Backstory: closeloop-bench-2026-07-05's done_when read "Not applicable as a
# bounded criterion — this is a standing goal ... Fail any → off_track" and the
# done-gate still terminally closed it `achieved`. A standing goal is closed by
# the OWNER (cancel_goal / re-aim), never by the gate: an all-axes-pass verdict
# must become needs_human, which blocks + notifies instead of closing.


def _standing_goal() -> Goal:
    return Goal(
        id="g", objective="closeloop mirrors best-in-class CRMs", cadence="6h",
        engine="devclaw", workspace_dir="/ws",
        done_when=(
            "Not applicable as a bounded criterion — this is a standing goal. "
            "Judge each delivery against the four axes; fail any → off_track."
        ),
        backlog=["notifications engine"],
    )


_ALL_PASS_ACHIEVED = {
    "verdict": "achieved",
    "rationale": "all axes pass",
    "clauses": [
        {"clause": "research is real", "satisfied": True, "evidence": "docs/research/crm.md"},
        {"clause": "synthesis argued", "satisfied": True, "evidence": "docs/features/x.md Borrowed/Rejected"},
    ],
    "structural_health": "clean",
}


def test_is_standing_matches_contract_phrasings():
    from devclaw.goal.models import is_standing

    assert is_standing("this is a standing goal")
    assert is_standing("Not applicable as a bounded criterion — judge deliveries")
    assert is_standing("NOT A BOUNDED CRITERION")
    assert is_standing("there is no terminal state for this goal")
    # bounded contracts stay bounded
    assert not is_standing("/health returns 200 and is tested")
    assert not is_standing("all backlog items merged")
    assert not is_standing("")


def test_standing_done_gate_achieved_becomes_needs_human():
    r = validate(_ALL_PASS_ACHIEVED, at_done_gate=True, standing=True)
    assert r.verdict == "needs_human"
    assert "standing" in r.question.lower()
    # the grading survives the conversion — the owner sees WHAT passed
    assert len(r.clauses) == 2 and all(c.satisfied for c in r.clauses)
    assert r.structural_health == "clean"


def test_standing_does_not_soften_off_track():
    # standing only intercepts the CLOSE; a failing axis still steers as usual.
    r = validate(
        {
            "verdict": "off_track", "rationale": "axis 3 failed",
            "corrections": ["[clause 1] fix the JWT fallback"],
        },
        at_done_gate=True, standing=True,
    )
    assert r.verdict == "off_track"
    assert r.corrections == ["[clause 1] fix the JWT fallback"]


def test_non_standing_achieved_is_unaffected():
    r = validate(_ALL_PASS_ACHIEVED, at_done_gate=True, standing=False)
    assert r.verdict == "achieved"


def test_standing_prompt_carries_the_contract_note():
    prompt = build_prompt(_standing_goal(), GoalStatus(), "log", "deliveries", at_done_gate=True)
    assert "STANDING-GOAL CONTRACT" in prompt
    # bounded goals don't get the note
    bounded = build_prompt(_goal(), GoalStatus(), "log", "deliveries", at_done_gate=True)
    assert "STANDING-GOAL CONTRACT" not in bounded


@pytest.mark.asyncio
async def test_evaluate_threads_standing_through_to_validate():
    async def caller(prompt: str) -> str:
        return json.dumps(_ALL_PASS_ACHIEVED)

    r = await evaluate(
        _standing_goal(), GoalStatus(), "log", "deliveries",
        claude_caller=caller, at_done_gate=True,
    )
    assert r.verdict == "needs_human"
    assert "standing" in r.question.lower()


def test_done_gate_brief_requires_browser_run_evidence_for_ui_clauses():
    """A UI clause is satisfied only by a real-browser run — a component that
    unit-tests green, builds, or renders in a Storybook story is not proof it
    works in the running app (the finance-sentry cmn-select gap)."""
    from types import SimpleNamespace

    from devclaw.goal.tick_donegate import _done_gate_review_brief

    goal = SimpleNamespace(
        objective="ship a themed Angular UI library",
        done_when="each component works in the running app",
    )
    brief = _done_gate_review_brief(goal)
    lowered = brief.lower()
    # a web-UI clause must cite a passing browser run…
    assert "browser" in lowered and "playwright" in lowered
    # …and the brief must call out that unit tests / stories are NOT sufficient.
    assert "storybook" in lowered
    assert "not proof" in lowered


# ---- 2026-08-19 done-gate treadmill fixes: the strictness dial on the
# structural axis + contract-bound corrections (verdict owned by done_when) ----


def _met_clauses():
    return [
        {"clause": "/health returns 200", "satisfied": True,
         "evidence": "src/Health.cs:12"},
        {"clause": "/health is tested", "satisfied": True,
         "evidence": "HealthTests.cs:8 Health_Returns200"},
    ]


def test_done_gate_structural_concerns_advise_and_ship_under_trust():
    """Named regression (2026-08-19 fs-book-figures night): four consecutive
    done proposals were each held open by fresh advisory nits on a met
    contract; the 05:00 run window was the only brake. Under the trust dial
    the structural axis advises-and-ships (ADR 0007): a met contract closes,
    the concerns ride the close as follow-ups."""
    r = validate(
        {
            "verdict": "achieved", "rationale": "all clauses met",
            "clauses": _met_clauses(),
            "structural_health": "concerns",
            "structural_concerns": [
                "RiskModule.cs:51 — DI registration belongs in a cross-cutting point"
            ],
        },
        at_done_gate=True, strictness="trust",
    )
    assert r.verdict == "achieved"
    assert r.structural_concerns  # preserved for the close-path surfacing


def test_done_gate_structural_concerns_still_block_under_strict():
    r = validate(
        {
            "verdict": "achieved", "rationale": "all clauses met",
            "clauses": _met_clauses(),
            "structural_health": "concerns",
            "structural_concerns": ["RiskModule.cs:51 — move the DI registration"],
        },
        at_done_gate=True, strictness="strict",
    )
    assert r.verdict == "off_track"
    assert any("[structural" in c for c in r.corrections)


def test_done_gate_taste_corrections_cannot_hold_a_met_contract_open():
    """off_track whose corrections carry no [clause N] tag while every clause
    is satisfied with evidence is an achieved-grade evidence set typed
    off_track — the untagged items demote to the structural axis and the dial
    decides the close, exactly as if the model had typed achieved."""
    payload = {
        "verdict": "off_track", "rationale": "minor improvements remain",
        "clauses": _met_clauses(),
        "corrections": [
            "Move the DI registration to Program.cs",
            "Make BookSnapshot.InvestedUsd a required parameter",
        ],
    }
    r = validate(payload, at_done_gate=True, strictness="trust")
    assert r.verdict == "achieved"
    assert any("InvestedUsd" in c for c in r.structural_concerns)
    r = validate(payload, at_done_gate=True, strictness="strict")
    assert r.verdict == "off_track"


def test_done_gate_unmet_clause_still_fails_closed_under_trust():
    """The dial recalibrates the structural axis ONLY — an unmet done_when
    clause holds the goal open in BOTH modes (fail-closed, the #186 class),
    and when the model names no fix the steering derives from the clause so
    the next advance brief is never byte-identical."""
    r = validate(
        {
            "verdict": "off_track", "rationale": "parity untested",
            "clauses": [
                {"clause": "parity test passes", "satisfied": False, "evidence": ""}
            ],
        },
        at_done_gate=True, strictness="trust",
    )
    assert r.verdict == "off_track"
    assert any(c.lower().startswith("[clause") for c in r.corrections)


def test_off_track_with_only_structural_concerns_carries_derived_steering():
    """Blind-loop regression: off_track with structural_concerns but empty
    corrections used to pass validate() with no steering at all — the next
    advance brief was byte-identical and the goal spun. Steering must never
    land empty on an actionable verdict."""
    r = validate(
        {
            "verdict": "off_track", "rationale": "shape needs work",
            "structural_health": "concerns",
            "structural_concerns": ["App.tsx — split the monolith"],
        }
    )
    assert r.verdict == "off_track"
    assert r.corrections
    assert r.corrections[0].startswith("[structural]")


def test_goal_evaluator_prompt_binds_verdict_to_clause_coverage_not_structure():
    """Presence AND absence, proven on the RAW template (per testing rules):
    the template must say the structural axis never sets the verdict, and the
    old self-policing rule (concerns => off_track corrections) must be gone —
    it taught the model the treadmill the host now prevents."""
    raw = (
        Path(__file__).resolve().parents[1]
        / "devclaw" / "prompts" / "goal-evaluator.md"
    ).read_text()
    assert "never sets the verdict" in raw
    assert "ONLY clause-tagged fixes" in raw
    assert "each concern surfaced as a correction" not in raw
    assert "planner" not in raw.lower()


# ---- done_when is behaviour, never delivery ceremony -----------------------
#
# Backstory (three consecutive nights, 2026-08-18→21): fs-book-figures'
# done_when ended "delivered as PRs (~2) ... merge stays human" and
# lkd-feed-honesty's demanded "MERGED evidence ... close PR #64 ... issues
# closed". The gate confirmed every substantive clause and refused to close
# anyway, because the ceremony text decomposed into clauses of its own that no
# sandbox run can satisfy — the goal-branch strategy never merges
# (tick_settle: "cumulative PR stays open for the done-gate") and the sandbox
# carries no GitHub credential. Both goals were closed by hand. The fix is at
# decomposition: ceremony never becomes a clause, so it can never hold a goal
# open. Nothing about verification is relaxed — a behaviour clause without
# repo evidence still fails closed.


def test_evaluator_prompt_excludes_delivery_mechanics_from_clauses():
    """Presence AND absence, proven on the RAW template (per testing rules).

    The template must tell the model that delivery mechanics are not
    completion criteria and must be dropped at decomposition — and must NOT
    tell it to merge, close issues, or otherwise act on the ceremony it drops.
    """
    raw = (
        Path(__file__).resolve().parents[1]
        / "devclaw" / "prompts" / "goal-evaluator.md"
    ).read_text()
    # the rule is stated, and stated at decomposition (step 1a, before clauses exist)
    assert "A clause must assert repository behaviour" in raw
    assert "becomes a numbered clause" in raw
    assert "never appears in `clauses`" in raw
    assert raw.index("A clause must assert repository behaviour") < raw.index(
        "**2. For EACH clause, find SPECIFIC evidence.**"
    )
    # the dropped text is surfaced, not silently swallowed (loud over silent)
    # — since spec 035 it is RECORDED with the pin (`dropped_ceremony`), not
    # only named in prose
    assert "Record each dropped span verbatim in `dropped_ceremony`" in raw
    assert "name\nthe drop in `rationale`" in raw
    # the named ceremony forms from the three incidents
    for ceremony in ("how many PRs", "whether it is merged", "who merges it"):
        assert ceremony in raw
    # and the gate is NOT told to perform delivery itself
    assert "you must merge" not in raw.lower()


def test_behaviour_clause_without_evidence_still_fails_closed():
    """The exclusion rule must not become a loophole: dropping ceremony at
    decomposition leaves every behaviour clause judged exactly as before."""
    r = validate(
        {
            "verdict": "off_track",
            "rationale": "dropped 'merge stays human' as delivery mechanics; clause 1 unmet",
            "clauses": [
                {"clause": "service owns the computation", "satisfied": False, "evidence": ""},
            ],
            "structural_health": "clean",
            "corrections": ["[clause 1] extract the canonical service"],
        },
        at_done_gate=True,
    )
    assert r.verdict == "off_track"
    assert r.corrections


# ---- "present" describing DATA is not existence evidence -------------------
#
# Live-found 2026-08-21 on goal lkd-honest-widgets-2026-08-21. The evaluator
# returned achieved with all 15 clauses satisfied and repo-confirmed, and the
# host flipped one of them to unsatisfied — holding a fully-met contract open.
# The flipped clause's evidence was:
#   "Home.test.tsx:213-233 asserts queryByText(/running|total/i) absent when
#    _errors present with zero counts; fails if the Home.tsx:91 guard is removed."
# `_EXISTENCE_EVIDENCE_RE` matched the bare word "present" — which here describes
# the _errors PAYLOAD in the test's input, not a file on disk. Mutation-sensitivity
# evidence ("fails if the guard is removed") is the strongest coverage evidence
# there is; reading it as "the file exists" is backwards. Error-state testing says
# "<field> present" constantly, so the collision is systematic.


def test_payload_present_wording_is_not_existence_evidence():
    """The live false positive: "_errors present" describes data, not a file."""
    r = validate(
        _achieved_with([
            {
                "clause": "A test asserts zeros never render without an accompanying error",
                "satisfied": True,
                "evidence": (
                    "Home.test.tsx:213-233 asserts queryByText(/running|total/i) absent "
                    "when _errors present with zero counts; fails if the Home.tsx:91 "
                    "guard is removed."
                ),
            },
        ]),
        at_done_gate=True,
        strictness="trust",
    )
    assert r.verdict == "achieved"
    assert r.clauses[0].satisfied


def test_file_present_wording_is_still_existence_evidence():
    """The narrowing must not blunt the net: presence phrasings that really do
    describe a FILE still fail closed."""
    for evidence in (
        "the test file is present under tests/",
        "parser specs present in the repo",
        "tests/e2e/walking-skeleton.spec.ts exists",
    ):
        r = validate(
            _achieved_with([
                {"clause": "the parser is covered by tests", "satisfied": True,
                 "evidence": evidence},
            ]),
            at_done_gate=True,
            strictness="trust",
        )
        assert r.verdict == "off_track", f"should stay closed for: {evidence}"
        assert "existence-only" in r.clauses[0].evidence
