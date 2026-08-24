"""Spec 015 US2 — the runner's agent-less validate_product branch: boot →
suites → report, per-scenario failing-title extraction, loud degradations."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_RUNNER_PATH = _REPO_ROOT / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_validate", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def emitted(runner, monkeypatch):
    out: list[dict] = []
    monkeypatch.setattr(runner, "_emit_result", out.append)
    # validation runs provision no toolchain in tests
    monkeypatch.setattr(runner, "_provision_toolchain", lambda ws: None)
    monkeypatch.setattr(runner, "_resync_mise_env", lambda ws: None)
    return out


def _report_of(emitted) -> dict:
    (payload,) = emitted
    assert payload["status"] == "ok"
    return payload["validation_report"]


def _pw_report(failing_titles=(), expected=3):
    return {
        "stats": {"expected": expected, "unexpected": len(failing_titles),
                  "flaky": 0, "skipped": 0},
        "suites": [{
            "title": "checkout.spec.ts",
            "suites": [{
                "title": "checkout",
                "specs": [
                    {"title": t, "tests": [{"status": "unexpected"}]}
                    for t in failing_titles
                ] + [{"title": "ok case", "tests": [{"status": "expected"}]}],
            }],
        }],
    }


def test_happy_path_boot_then_suites_green(runner, emitted, tmp_path):
    ws = str(tmp_path)
    runner._run_validate_product(
        {"validation": {"boot": "true", "suites": "true"}}, ws
    )
    vr = _report_of(emitted)
    assert vr["contract_ran"] is True
    assert vr["boot"]["passed"] and vr["suites"]["passed"]
    assert vr["failing_tests"] == []
    # no per-scenario report existed — said out loud, never silent
    assert "no per-scenario report" in vr["note"]


def test_failing_titles_extracted_from_playwright_report(runner, emitted, tmp_path):
    ws = tmp_path
    report = _pw_report(failing_titles=["coupon applies", "sentinel registers"])
    (ws / ".devclaw").mkdir()
    # suites cmd writes the report itself (simulating a playwright run) then fails
    suites = (
        f"mkdir -p .devclaw && cat > .devclaw/playwright-report.json <<'EOF'\n"
        f"{json.dumps(report)}\nEOF\nexit 1"
    )
    runner._run_validate_product(
        {"validation": {"boot": "true", "suites": suites}}, str(ws)
    )
    vr = _report_of(emitted)
    assert vr["suites"]["passed"] is False
    assert vr["failing_tests"] == [
        "checkout.spec.ts > checkout > coupon applies",
        "checkout.spec.ts > checkout > sentinel registers",
    ]
    assert vr["browser_report"]["unexpected"] == 2


def test_boot_failure_short_circuits_before_suites(runner, emitted, tmp_path):
    marker = tmp_path / "suites-ran"
    runner._run_validate_product(
        {"validation": {"boot": "exit 7", "suites": f"touch {marker}"}},
        str(tmp_path),
    )
    vr = _report_of(emitted)
    assert vr["boot"]["passed"] is False and vr["boot"]["exit_code"] == 7
    assert vr["suites"] is None
    assert "boot failed" in vr["note"]
    assert not marker.exists()


def test_missing_contract_is_stated_not_silent(runner, emitted, tmp_path):
    runner._run_validate_product({}, str(tmp_path))
    vr = _report_of(emitted)
    assert vr["contract_ran"] is False
    assert vr["note"].startswith("missing contract")


def test_green_by_vacuity_is_named(runner, emitted, tmp_path):
    ws = tmp_path
    report = {"stats": {"expected": 0, "unexpected": 0, "flaky": 0, "skipped": 0},
              "suites": []}
    suites = (
        f"mkdir -p .devclaw && cat > .devclaw/playwright-report.json <<'EOF'\n"
        f"{json.dumps(report)}\nEOF\nexit 0"
    )
    runner._run_validate_product(
        {"validation": {"boot": "true", "suites": suites}}, str(ws)
    )
    vr = _report_of(emitted)
    assert vr["note"].startswith("green-by-vacuity")


def test_stale_report_from_prior_run_is_cleared(runner, emitted, tmp_path):
    ws = tmp_path
    stale = ws / ".devclaw" / "playwright-report.json"
    stale.parent.mkdir()
    stale.write_text(json.dumps(_pw_report(failing_titles=["old ghost"])))
    runner._run_validate_product(
        {"validation": {"boot": "true", "suites": "true"}}, str(ws)
    )
    vr = _report_of(emitted)
    # the stale artifact must not leak a prior run's failures into this one
    assert vr["failing_tests"] == []
    assert vr["browser_report"] is None


def test_no_agent_is_spawned_for_validate_product(runner, emitted, tmp_path, monkeypatch):
    """FR-005: the branch is agent-less by construction — the ACP drive path
    must never be touched (fake-agent regression style)."""
    def _boom(*a, **k):
        raise AssertionError("validate_product must never drive the ACP agent")
    for name in ("_wrap_goal", "_resolve_acp_command"):
        if hasattr(runner, name):
            monkeypatch.setattr(runner, name, _boom)
    runner._run_validate_product(
        {"validation": {"boot": "true", "suites": "true"}}, str(tmp_path)
    )
    assert _report_of(emitted)["contract_ran"] is True
