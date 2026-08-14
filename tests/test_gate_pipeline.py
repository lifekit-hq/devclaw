"""Regression tests for the settle gate pipeline (#407 PRs 2-4).

The post-verify quality cascade in ``TaskQueue._run_and_settle`` used to be an
inline short-circuit ladder (compute the diff once → test-integrity → review →
browser). The GatePipeline extraction lifts it into an ordered tuple of pure
verdict-producer gates run through :func:`run_pipeline`. These tests pin the
behaviours the inline ladder carried implicitly and that the extraction MUST
preserve exactly:

  1. strict ordering via short-circuit — a failing gate stops the chain, so a
     later gate never runs (integrity failing ⇒ review never called);
  2. the diff is computed AT MOST ONCE and shared across every gate;
  3. the verify gate short-circuits BEFORE any diff is computed (a verify
     failure must not trigger a ``git diff``);
  4. the ADR-0007 dial-able flag is carried by review/browser findings and NOT
     by the always-hard verify/test_integrity gates.

Pure unit tests — no docker, no claude, no git.
"""

from __future__ import annotations

from devclaw import task_queue as tq
from devclaw.quality.gate_pipeline import GateInput, GateVerdict, run_pipeline


class _RecordingGate:
    """A fake gate that records whether it ran and touches the shared diff (like
    the real read-the-change gates), so tests can assert short-circuit + one-diff
    semantics without standing up the real cognition gates."""

    def __init__(self, gate_id: str, *, fail: bool = False, dialable: bool = False):
        self.gate_id = gate_id
        self._fail = fail
        self._dialable = dialable
        self.checked = False

    def applies(self, gi: GateInput) -> bool:
        return True

    async def check(self, gi: GateInput) -> GateVerdict:
        self.checked = True
        await gi.diff()  # the read-the-change gates all consume the shared diff
        if self._fail:
            return GateVerdict.failed(
                self.gate_id, f"{self.gate_id} failed", dialable=self._dialable
            )
        return GateVerdict.passed(self.gate_id)


def _gate_input(diff_calls: list, *, verify=None) -> GateInput:
    async def diff_fn() -> str:
        diff_calls.append(1)
        return "diff-body"

    return GateInput(
        kind="implement_feature",
        goal="g",
        workspace_dir="/ws",
        verify=verify if verify is not None else {"ran": True, "passed": True},
        scaffold=False,
        browser_mode="flexible",
        diff_fn=diff_fn,
    )


class _FakeQueue:
    """Minimal stand-in exposing only the two coroutine methods the review /
    browser gates call on their queue."""

    def __init__(self, review_fail=None, reachability_clears=False):
        self._review_fail = review_fail
        self._reachability_clears = reachability_clears

    async def _review_failure(self, kind, goal, diff, ws, *, scaffold=False, project_id=None):
        return self._review_fail

    async def _browser_reachability_clears(self, verify, diff, ws):
        return self._reachability_clears


async def test_gate_pipeline_short_circuits_integrity_before_review():
    """A failing test-integrity gate stops the chain — the review gate is never
    called. This is the strict ordering the cascade always had (review runs only
    if integrity passed), now enforced by the pipeline short-circuit."""
    calls: list = []
    gi = _gate_input(calls)
    integrity = _RecordingGate("test_integrity", fail=True)
    review = _RecordingGate("review", dialable=True)

    verdict = await run_pipeline(gi, (integrity, review))

    assert verdict is not None
    assert verdict.gate_id == "test_integrity"
    assert verdict.dialable is False
    assert integrity.checked is True
    assert review.checked is False  # short-circuited — review never ran


async def test_gate_pipeline_shares_one_diff_across_gates():
    """Every read-the-change gate consumes the SAME memoised diff — it is
    computed once, not once per gate (the inline ladder's single-computed diff)."""
    calls: list = []
    gi = _gate_input(calls)
    gates = (
        _RecordingGate("test_integrity"),
        _RecordingGate("review"),
        _RecordingGate("browser"),
    )

    verdict = await run_pipeline(gi, gates)

    assert verdict is None  # all passed
    assert all(g.checked for g in gates)
    assert len(calls) == 1  # diff computed exactly once and shared


async def test_verify_gate_short_circuits_before_any_diff_is_computed():
    """A verify failure must short-circuit the pipeline WITHOUT computing the
    diff — the real _VerifyGate never asks for it, so no git diff runs on the
    verify-fail path (the inline ladder's 'compute the diff only after verify
    passed' behaviour)."""
    calls: list = []
    gi = _gate_input(
        calls,
        verify={"ran": True, "passed": False, "cmd": "npm test", "exit_code": 1},
    )
    downstream = _RecordingGate("test_integrity")

    verdict = await run_pipeline(gi, (tq._VerifyGate(), downstream))

    assert verdict is not None
    assert verdict.gate_id == "verify"
    assert verdict.dialable is False  # always-hard, never dial-able
    assert downstream.checked is False
    assert calls == []  # no diff computed on a verify failure


async def test_gate_input_diff_is_memoised():
    """GateInput.diff() computes its diff at most once even across many awaits —
    an empty diff is a valid value, so the guard is a flag, not truthiness."""
    calls: list = []

    async def diff_fn() -> str:
        calls.append(1)
        return ""  # empty but valid

    gi = GateInput(
        kind="implement_feature",
        goal="g",
        workspace_dir="/ws",
        verify={"ran": True, "passed": True},
        scaffold=False,
        browser_mode="flexible",
        diff_fn=diff_fn,
    )

    assert await gi.diff() == ""
    assert await gi.diff() == ""
    assert len(calls) == 1  # not recomputed despite the falsy value


async def test_review_gate_finding_is_dialable():
    """A real _ReviewGate finding is dial-able (ADR 0007): it can advise-and-ship
    under `trust` at retry exhaustion. The always-hard gates never set this."""
    calls: list = []
    gi = _gate_input(calls)
    q = _FakeQueue(review_fail="review found a real defect")

    verdict = await tq._ReviewGate(q).check(gi)

    assert verdict.ok is False
    assert verdict.gate_id == "review"
    assert verdict.dialable is True
    assert verdict.reason == "review found a real defect"


async def test_review_gate_pass_is_ok_and_not_dialable():
    """No review finding ⇒ a passing verdict that lets the cascade continue."""
    calls: list = []
    gi = _gate_input(calls)
    q = _FakeQueue(review_fail=None)

    verdict = await tq._ReviewGate(q).check(gi)

    assert verdict.ok is True
    assert verdict.gate_id == "review"
