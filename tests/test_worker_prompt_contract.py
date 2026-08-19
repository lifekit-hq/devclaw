"""Worker-prompt doctrine from #508: judgment-call return contract, precedent
rule, one-shot scope bound (runner/skills/_writes-code/).

Three pull-compatible rules, all worker-side plain markdown (the model-agnostic
worker layer — no host-side scouting/pre-digestion):

  * the commit skill asks for a ``Judgment calls:`` section in the commit body —
    the commit body is what ``delivery._pr_body`` renders as the PR lead, so the
    reviewer sees the decisions (defaults, trade-offs, pattern deviations);
  * the quality-bar skill asks the worker to copy the nearest in-repo pattern
    when adding a mechanism and name the precedent in the commit body;
  * the verify-iterate skill bounds a task to ONE coherent reviewable change
    with split-and-stop — the direct one-shot ``dispatch_task`` path had no
    bound (the long_lived thin-advance brief carries its own milestone bound,
    which must NOT be duplicated: one bound per surface).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from devclaw.goal.store import GoalStore
from devclaw.goal.tick import _advance_brief
from tests.goal_fakes import Clock, seed_goal

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_SRC = _REPO_ROOT / "runner" / "skills"
_RUNNER_PATH = _REPO_ROOT / "runner" / "runner.py"

_COMMIT_SKILL = _SKILLS_SRC / "_writes-code" / "90-commit.md"
_QUALITY_SKILL = _SKILLS_SRC / "_writes-code" / "10-quality-bar.md"
_VERIFY_SKILL = _SKILLS_SRC / "_writes-code" / "40-verify-iterate.md"

# Marker phrases — asserted present in the RAW file first so the absence half of
# each test is never vacuous (per rules/testing.md).
_JUDGMENT_MARKER = "Judgment calls"
_PRECEDENT_MARKER = "copy its shape"
_SCOPE_MARKER = "unreviewable diff"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location(
        "oh_runner_prompt_contract_under_test", _RUNNER_PATH
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def skill_dir(runner, monkeypatch):
    monkeypatch.setattr(runner, "_SKILLS_DIR", str(_SKILLS_SRC))
    return _SKILLS_SRC


# ---- 1. judgment-call return contract ---------------------------------------


def test_judgment_calls_contract_loads_for_code_writing_kinds_only(runner, skill_dir):
    """The reviewer-facing decision log rides the commit body (which delivery
    renders as the PR body lead) — so it belongs to the commit skill and must
    reach every code-writing bundle, and no read-only one."""
    assert _JUDGMENT_MARKER in _COMMIT_SKILL.read_text(encoding="utf-8")  # marker is real
    for kind in ("implement_feature", "fix_bug"):
        assert _JUDGMENT_MARKER in runner._load_skills(kind)
    for kind in ("review_repository", "onboard"):
        assert _JUDGMENT_MARKER not in runner._load_skills(kind)


# ---- 2. precedent rule -------------------------------------------------------


def test_precedent_rule_loads_for_code_writing_kinds_only(runner, skill_dir):
    """Before adding a mechanism, find the nearest in-repo pattern and copy its
    shape, naming the precedent in the commit body."""
    assert _PRECEDENT_MARKER in _QUALITY_SKILL.read_text(encoding="utf-8")  # marker is real
    for kind in ("implement_feature", "fix_bug"):
        assert _PRECEDENT_MARKER in runner._load_skills(kind)
    for kind in ("review_repository", "onboard"):
        assert _PRECEDENT_MARKER not in runner._load_skills(kind)


# ---- 3. one-shot scope bound -------------------------------------------------


def test_scope_bound_present_once_for_code_writing_kinds(runner, skill_dir):
    """The direct one-shot dispatch_task path gets its scope bound from the
    skills bundle (its goal text is the caller's verbatim instruction — nothing
    else bounds it). Exactly once: a rule stated twice is prompt bloat."""
    assert _SCOPE_MARKER in _VERIFY_SKILL.read_text(encoding="utf-8")  # marker is real
    for kind in ("implement_feature", "fix_bug"):
        assert runner._load_skills(kind).count(_SCOPE_MARKER) == 1
    for kind in ("review_repository", "onboard"):
        assert _SCOPE_MARKER not in runner._load_skills(kind)


def test_scope_bound_reaches_the_wrapped_one_shot_prompt(runner, skill_dir):
    """End-to-end through _wrap_goal — the actual prompt a one-shot worker gets
    carries the bound exactly once."""
    wrapped = runner._wrap_goal("implement_feature", "add a widget")
    assert wrapped.count(_SCOPE_MARKER) == 1


def test_thin_advance_brief_is_not_double_scope_bounded(tmp_path):
    """The long_lived thin-advance brief already bounds scope (smallest
    not-yet-done story-slice = one reviewable PR — spec 008 US1). The #508
    generic bound lives in the skills bundle, so the brief must NOT restate it —
    one bound per surface, per the each-rule-once prompt style."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g")
    brief = _advance_brief(store.load_goal("g"), "")
    assert "story-slice" in brief.lower()  # its own bound, now speckit-shaped
    assert "one reviewable pr" in brief.lower()
    assert _SCOPE_MARKER not in brief
