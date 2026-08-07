"""The Ledger hidden-checklist RUNNER mechanics (compounding experiment P1-B).

The concrete probes shell out to dotnet/ng/Playwright and only execute on the
live box; the runner's orchestration is what we lock here, off the box, with a
fake ctx: it produces a full criteria vector, and — the load-bearing property —
it fails a crashing probe CLOSED (never a green, never an escaping exception),
the #186 discipline applied to the experiment's own grader.
"""

from __future__ import annotations

from evals.ledger_checklist.checklist import (
    CHECKS,
    Check,
    CheckCtx,
    CheckResult,
    criteria_vector,
    run_checklist,
)


def _ctx() -> CheckCtx:
    # The mechanics tests use probes that ignore ctx, so sh/api are inert.
    return CheckCtx(repo="/does/not/matter", sh=lambda *a, **k: None, api=lambda *a, **k: None)


def test_run_checklist_builds_full_vector_from_probe_results():
    checks = [
        Check("a", "feat a", lambda ctx: CheckResult(True, "green")),
        Check("b", "feat b", lambda ctx: CheckResult(False, "red")),
    ]
    results = run_checklist(_ctx(), checks)
    assert set(results) == {"a", "b"}
    assert criteria_vector(results) == {"a": True, "b": False}


def test_crashing_probe_fails_closed_and_does_not_escape():
    def _boom(ctx):
        raise RuntimeError("toolchain missing")

    checks = [
        Check("ok", "fine", lambda ctx: CheckResult(True, "green")),
        Check("bad", "crashes", _boom),
    ]
    # Must not raise — a grader crash is not an abort of the whole night.
    results = run_checklist(_ctx(), checks)
    assert results["ok"].passed is True
    assert results["bad"].passed is False  # fail-closed: crash is never a green
    assert "probe crashed" in results["bad"].detail
    assert "toolchain missing" in results["bad"].detail


def test_real_checklist_has_ten_stable_criteria_ids():
    # The vector's shape is a contract across nights — c1..c10, in order, so a
    # night's snapshot is comparable to the prior night's. Guard it.
    assert [c.id for c in CHECKS] == [f"c{i}" for i in range(1, 11)]
    assert all(c.feature and c.probe for c in CHECKS)
    assert len({c.id for c in CHECKS}) == 10  # no dupes → no silently-dropped criterion
