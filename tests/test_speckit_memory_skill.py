"""The speckit-memory worker skill (runner/skills/_writes-code/05-speckit-memory.md).

Spec 008: speckit is the universal execution substrate — the worker's durable
cross-session memory is the repo's ``specs/NNN-*/`` artifacts (spec.md,
plan.md, tasks.md), and a root ``PLAN.md`` is retired as a planning spine.
This skill is the worker's half of that. Its predecessor (05-plan-md.md,
demolition P2) taught the worker to CREATE a root PLAN.md — live-found still
shipping in the #538 shakedown (the delivered PR carried a fresh PLAN.md
despite the host brief ordering the speckit flow), because the host-side P1
rewire cannot reach a skill baked into the sandbox image.

What these tests pin:

  * it loads for code-writing kinds (the _writes-code doctrine tier) and NOT
    for the read-only kinds;
  * it teaches the speckit artifacts as the handoff (read the current
    feature's tasks.md first; checkbox flips are the progress signal; one
    story-slice per PR; the code is the source of truth);
  * it FORBIDS creating a root PLAN.md, and no _writes-code skill teaches it
    anymore (presence AND absence, per rules/testing.md).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILLS_SRC = _REPO_ROOT / "runner" / "skills"
_SKILL = _SKILLS_SRC / "_writes-code" / "05-speckit-memory.md"
_RUNNER_PATH = _REPO_ROOT / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location(
        "devclaw_runner_speckit_skill_under_test", _RUNNER_PATH
    )
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
    assert _SKILL.is_file(), f"missing speckit-memory worker skill at {_SKILL}"


def test_skill_loads_for_code_writing_kinds_only(runner, skill_dir):
    marker = "Durable memory — the speckit artifacts"
    assert marker in _text()  # marker is real (assert presence AND absence)
    for kind in ("implement_feature", "fix_bug"):
        assert marker in runner._load_skills(kind), (
            f"speckit-memory skill must load for the code-writing kind {kind!r}"
        )
    for kind in ("review_repository", "onboard"):
        assert marker not in runner._load_skills(kind), (
            f"speckit-memory skill must NOT load for the read-only kind {kind!r}"
        )


def test_skill_teaches_speckit_artifacts_as_the_handoff():
    text = _text()
    assert "specs/NNN-*/" in text, "skill must name the speckit feature dirs as the handoff"
    assert "tasks.md" in text and "spec.md" in text
    assert "create-new-feature.sh" in text, "skill must teach creating a feature via the repo's own script"


def test_skill_teaches_checkable_tasks_as_the_progress_signal():
    text = _text()
    assert "- [ ]" in text and "- [x]" in text, "skill must teach the checkbox convention"
    assert "without re-deriving" in text.lower(), "skill must frame the checklist as the progress signal"


def test_skill_teaches_one_story_slice_per_pr():
    lowered = _text().lower()
    assert "story-slice" in lowered
    assert "build ahead" in lowered, "skill must forbid building ahead into later stories"


def test_skill_teaches_code_is_source_of_truth():
    assert "source of truth" in _text().lower()


def test_skill_forbids_creating_a_root_plan_md():
    # The named regression for the #538 live find: the worker wrote a fresh
    # PLAN.md because the old skill taught it to. The replacement must forbid
    # exactly that.
    text = _text()
    assert "Never create or update a root `PLAN.md`" in text


def test_no_writes_code_skill_teaches_plan_md_anymore(runner, skill_dir):
    # Absence across the whole assembled brief: PLAN.md may appear ONLY in the
    # prohibition ("Never create or update…"), never as the old instruction.
    old_marker = "PLAN.md — your durable working memory"
    for kind in ("implement_feature", "fix_bug"):
        brief = runner._load_skills(kind)
        assert old_marker not in brief, (
            f"the retired PLAN.md skill is back in the {kind!r} brief"
        )
        assert "maintain PLAN.md" not in brief.lower()


def test_skill_does_not_reintroduce_the_dropped_gh_issue_machinery():
    # The 2026-08-05 pivot dropped the wayfinder GitHub-issue map; the speckit
    # swap must not resurrect it either.
    lowered = _text().lower()
    assert "gh issue" not in lowered
    assert "wayfinder:map" not in lowered


# ─── spec 021 US3: the read-side diet ────────────────────────────────────────


def test_skill_teaches_per_slice_read_budget():
    """US3 (spec 021): exploration cost is paid once at planning — each slice
    gets a declared surface line in plan.md — and build sessions pull from it
    before raw exploration. Presence AND absence per rules/testing.md: the
    skill must demand the declared-surface read order and must never license
    silent repo-wide fallback."""
    text = _text()
    assert "the next session's read budget" in text
    assert "explore raw files only within the slice's declared surface" in text
    assert "never silently" in text  # stale entry → fix it loud, no fallback
    # the diet must not contradict the pull doctrine (no pushed dossiers):
    assert "dossier" not in text.lower()


def test_skill_states_the_harness_enforced_slice_stop_once():
    """US1's contract line survives US3's edits: one slice per session, and
    the stop is stated as the HARNESS's act exactly once (prompt-style rule:
    each rule stated once)."""
    text = _text()
    assert text.count("the harness ends the session") == 1
