"""Spec 015 US1 — acceptance scenarios are executable ground truth, enforced
at the three existing seams: intake grading, the spec template, and the
done-gate's structural axis; the browser gate's executed-run requirement
(FR-002) is pinned to this spec."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from devclaw import intake_readiness
from devclaw.goal.evaluator import build_prompt as evaluator_prompt
from devclaw.goal.models import Goal, GoalStatus
from devclaw.quality.browser_gate import browser_run_verdict

_REPO = Path(__file__).resolve().parent.parent


# ---- FR-001: intake grading requires executable-test-expressible intent ------

def test_intake_prompt_requires_executable_test_at_outermost_surface():
    prompt = intake_readiness.build_prompt(
        what="add a coupon field to checkout",
        done_when="coupons apply",
        context=None,
        repo_context="repo facts here",
    )
    assert "executable test" in prompt
    assert "outermost surface" in prompt
    # the three surface archetypes are named so the model grounds the judgment
    assert "end-to-end test" in prompt
    assert "running service" in prompt
    assert "running scheduler" in prompt
    # and the missing-vocabulary example the verdict can name
    assert "acceptance criteria not expressible as an executable test" in prompt


def test_not_executable_criteria_grade_needs_refinement(monkeypatch):
    """SC-005: a verdict carrying the executability reason flows through
    validate() into a not-ready outcome with the reason preserved — the label
    machinery (ready ⇒ devclaw-ready, else needs-refinement) is untouched."""

    async def caller(prompt):
        return json.dumps({
            "ready": False,
            "missing": ["acceptance criteria not expressible as an executable test"],
            "rationale": "the outcome ('feels faster') has no executable check",
            "increments": {"assessed": None, "agrees": None, "basis": ""},
        })

    verdict = asyncio.run(intake_readiness.evaluate(
        what="make the dashboard feel faster",
        done_when="it feels snappier",
        context=None,
        repo_context="repo facts here",
        claude_caller=caller,
    ))
    assert verdict.ready is False
    assert "acceptance criteria not expressible as an executable test" in verdict.missing


# ---- FR-001 upstream: the spec template carries the requirement --------------

def test_spec_template_requires_executable_acceptance_scenarios():
    template = (_REPO / ".specify/templates/spec-template.md").read_text(encoding="utf-8")
    assert "executable acceptance test" in template
    assert "outermost surface" in template


# ---- FR-003: the done-gate names uncovered acceptance scenarios --------------

def test_done_gate_prompt_names_uncovered_acceptance_scenarios():
    goal = Goal(
        id="g", objective="ship checkout", cadence="1d", engine="devclaw",
        workspace_dir="/ws", done_when="checkout works and is tested",
    )
    prompt = evaluator_prompt(
        goal, GoalStatus(), "log", "deliveries",
        review_report="review here", at_done_gate=True,
    )
    assert "spec acceptance scenarios with no covering executable test" in prompt
    # it rides the structural axis, which never sets the verdict
    assert "never sets the verdict" in prompt


def test_uncovered_scenario_marker_absent_from_raw_template():
    """The enum lives in the evaluator's built done-gate block, not the raw
    template — prove the raw template does NOT carry it, so the presence
    assertion above is grounded in build_prompt, never vacuous."""
    raw = (_REPO / "devclaw/prompts/goal-evaluator.md").read_text(encoding="utf-8")
    assert "no covering executable test" not in raw


# ---- FR-002 pin: executed-run proof is the existing browser-gate law ---------

def test_browser_gate_requires_executed_run_for_ui_facing_change():
    """FR-002 = existing behavior, pinned to spec 015: a UI-facing diff with
    no executed browser run is `never_ran` and blocks under BOTH dial modes —
    intent (config present, suite named) is not execution."""
    diff = "diff --git a/web/src/app/checkout.component.ts b/web/src/app/checkout.component.ts\n"
    res = browser_run_verdict(
        {"ran": True, "passed": True}, diff, config_present=True,
    )
    assert res.state == "never_ran"
    assert res.blocks_delivery("flexible") and res.blocks_delivery("strict")

    # an executed, clean run satisfies it
    ok = browser_run_verdict(
        {"ran": True, "passed": True,
         "browser_report": {"expected": 3, "unexpected": 0,
                            "flaky": 0, "skipped": 0}},
        diff, config_present=True,
    )
    assert ok.state == "ran_passed"
    assert not ok.blocks_delivery("strict")
