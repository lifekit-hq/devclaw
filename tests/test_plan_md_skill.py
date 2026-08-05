"""The PLAN.md worker skill (openhands-runner/skills/_writes-code/05-plan-md.md).

Demolition P2 (docs/proposals/cognition-demolition.md): durable plan-state moves
from a control-plane LLM (the planner) into a plain worker-owned ``PLAN.md`` file
on the target repo. This skill is the worker's half of that — it teaches the
sandbox worker to read/maintain the plan.

Unlike the mooted wayfinder-issue skill, NOTHING in the control plane parses
PLAN.md (it is the worker's own working memory, not a machine-read frontier), so
there is no parser to pin against. What these tests pin instead:

  * it loads for code-writing kinds (it lives in the _writes-code doctrine tier),
    and NOT for the read-only kinds;
  * it teaches the plan shape + the trust-input/verify-output principle (the code
    is the source of truth), and does NOT reintroduce the gh-issue machinery the
    2026-08-05 pivot dropped.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_SRC = _REPO_ROOT / "openhands-runner" / "skills"
_SKILL = _SKILLS_SRC / "_writes-code" / "05-plan-md.md"
_RUNNER_PATH = _REPO_ROOT / "openhands-runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("oh_runner_plan_skill_under_test", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def skill_dir(runner, monkeypatch):
    monkeypatch.setattr(runner, "_SKILLS_DIR", str(_SKILLS_SRC))
    return _SKILLS_SRC


def _text() -> str:
    return _SKILL.read_text(encoding="utf-8")


def test_skill_file_exists():
    assert _SKILL.is_file(), f"missing PLAN.md worker skill at {_SKILL}"


def test_skill_loads_for_code_writing_kinds_only(runner, skill_dir):
    marker = "PLAN.md — your durable working memory"
    assert marker in _text()  # marker is real (per rules/testing.md: assert presence AND absence)
    for kind in ("implement_feature", "fix_bug"):
        assert marker in runner._load_skills(kind), (
            f"PLAN.md skill must load for the code-writing kind {kind!r}"
        )
    for kind in ("review_repository", "onboard"):
        assert marker not in runner._load_skills(kind), (
            f"PLAN.md skill must NOT load for the read-only kind {kind!r}"
        )


def test_skill_teaches_the_plan_shape():
    # spec->plan->tasks shape (2026-08-05 planning convergence): Destination
    # (specify) + Decisions + Milestones (the coarse plan) + a checkable Tasks
    # list (the box-checking progress signal) + Out of scope.
    text = _text()
    for header in ("## Destination", "## Decisions so far", "## Milestones", "## Out of scope"):
        assert header in text, f"skill omits the {header!r} section of the plan shape"
    assert "## Tasks" in text, "skill omits the per-milestone Tasks section"
    # The old freeform 'Next / open questions' section was replaced by the
    # structured Milestones + checkable Tasks shape — assert the swap actually
    # happened (it's genuinely absent from the template, not a canned prior).
    assert "## Next / open questions" not in text


def test_skill_teaches_checkable_tasks_as_the_progress_signal():
    # The 'tasks' layer is load-bearing: progress becomes reading a box, not an
    # LLM re-plan. The skill must teach the checkbox convention explicitly.
    text = _text()
    assert "- [ ]" in text and "- [x]" in text, "skill must teach the checkable-task convention"
    assert "without re-deriving" in text.lower(), "skill must frame the checklist as the progress signal"


def test_skill_teaches_rolling_wave_and_adaptive_depth():
    # Coarse-first / deep-just-in-time, de-risk-by-sequencing, and sizing the
    # ceremony to the goal (the anti-'plan-everything-up-front' guidance).
    lowered = _text().lower()
    assert "rolling wave" in lowered, "skill must teach coarse-first / deep-just-in-time planning"
    assert "de-risk" in lowered, "skill must teach ordering milestones to de-risk early"
    assert "size the plan to the goal" in lowered, "skill must teach adaptive depth (size the ceremony to the goal)"


def test_skill_teaches_done_is_the_whole_goal_not_one_increment():
    # A big goal must not be declared done after a single increment; the worker
    # owns honesty about what remains (the grounded done-gate is the authority).
    lowered = _text().lower()
    assert "not when your one increment is done" in lowered


def test_skill_teaches_code_is_source_of_truth():
    # The trust-input/verify-output principle (§3a): the worker owns PLAN.md but
    # the repo is authoritative — where they disagree, the code wins.
    assert "source of truth" in _text().lower()


def test_skill_does_not_reintroduce_the_dropped_gh_issue_machinery():
    # The 2026-08-05 pivot dropped the wayfinder GitHub-issue map for a plain
    # file. A skill that told the worker to use `gh issue` / `wayfinder:map`
    # would resurrect the mooted direction.
    lowered = _text().lower()
    assert "gh issue" not in lowered, "PLAN.md skill must not use the dropped gh-issue machinery"
    assert "wayfinder:map" not in lowered, "PLAN.md skill must not use the dropped wayfinder map label"
