"""Regression tests for the browser-gate WORKER doctrine (unit 4): the baked
skills mandate the machine-readable Playwright JSON-reporter contract the host
gate keys off. The companion advisory — a UI-source change shipping with a
verify_cmd that runs no browser E2E — now reads the materialized span host-side
(spec 013); see ``test_change_advisories.py``.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SKILLS = _REPO / "runner" / "skills"


# ---- skill doctrine content ---------------------------------------------------

def test_playwright_skill_mandates_the_json_reporter_contract():
    text = (_SKILLS / "craft" / "playwright.md").read_text(encoding="utf-8")
    assert "--reporter=json" in text
    assert "PLAYWRIGHT_JSON_OUTPUT_NAME" in text
    assert "webServer" in text
    # the gate keys off execution, not intent
    assert "0 executed" in text or "never ran" in text.lower()


def test_verify_gate_coverage_skill_points_at_the_browser_gate():
    text = (_SKILLS / "_writes-code" / "20-verify-gate-coverage.md").read_text(encoding="utf-8")
    assert "browser gate" in text.lower()
    assert "--reporter=json" in text


# The UI-source-changed-without-browser-run advisory moved host-side with spec
# 013 (it read `git diff <pre_head>` inside the sandbox and was therefore blind
# to unrecorded files, like every other consumer of that view). Its regression
# tests live in ``test_change_advisories.py``.
