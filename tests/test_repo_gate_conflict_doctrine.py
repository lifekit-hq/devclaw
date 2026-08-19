"""Regression test for the repo-gate-conflict WORKER doctrine (#354).

When a repo-local mechanism (pre-commit hook, lint autofix, version-bump gate,
CI check) mechanically forces a change the ticket forbids, the worker must
treat fixing/relaxing the mechanism as in-scope and document it — NOT appease
the gate with the forbidden change. Live-observed on finance-sentry-ui-library
(#301/#302): a pre-commit version-gate forced a `package.json` bump the ticket
forbade, and every attempt appeased it and failed review for scope creep.

This is the generalizable, class-level fix (worker doctrine at the edge), not a
finance-sentry patch. These tests pin that the doctrine ships in the always-on
brief for the code-writing kinds and says the load-bearing things.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_SKILLS = _REPO / "runner" / "skills"
_DOCTRINE = _SKILLS / "_writes-code" / "50-repo-gate-conflict.md"
_RUNNER_PATH = _REPO / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("oh_runner_gate_conflict_under_test", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_doctrine_file_exists_and_is_well_formed():
    text = _DOCTRINE.read_text(encoding="utf-8")
    assert text.lstrip().startswith("# "), "doctrine must open with an H1 title"
    # names the mechanisms it generalizes over — not just one repo's hook
    lowered = text.lower()
    assert "pre-commit" in lowered
    assert "lint" in lowered
    # the two losing moves are both named and forbidden
    assert "appease" in lowered
    # the resolution: fix the mechanism, document why
    assert "in scope" in lowered
    assert "agents.md" in lowered
    # never fabricate / silently appease when it can't be fixed
    assert "fabricate" in lowered or "blocker" in lowered


@pytest.mark.parametrize("kind", ["implement_feature", "fix_bug"])
def test_doctrine_ships_in_the_writes_code_brief(runner, monkeypatch, kind):
    monkeypatch.setattr(runner, "_SKILLS_DIR", str(_SKILLS))
    bundle = runner._load_skills(kind)
    # the worker sees the "fix the mechanism, don't obey it" doctrine
    assert "fix the mechanism" in bundle.lower()
    assert "forbids" in bundle.lower()


@pytest.mark.parametrize("kind", ["review_repository", "onboard"])
def test_doctrine_absent_from_non_code_writing_kinds(runner, monkeypatch, kind):
    """review/onboard don't commit code, so the writes-code tier — and this
    doctrine with it — must not load for them."""
    monkeypatch.setattr(runner, "_SKILLS_DIR", str(_SKILLS))
    bundle = runner._load_skills(kind)
    assert "fix the mechanism, don't obey it" not in bundle.lower()
