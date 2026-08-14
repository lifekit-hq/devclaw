"""Review gate — the single adversarial reviewer (``review_diff``) wrapped in
the cognition-timeout **degradation ladder** (``review_gate``).

Two halves, mirroring test_review_gate.py:
  1. the pure module (devclaw/quality): ``review_gate`` delegates to
     ``review_diff`` on the happy path; the ladder engages only on a
     timeout / non-quota unparseable crash and unions per-file verdicts.
  2. the queue integration: ``review_gate`` is the wired default reviewer; an
     exhausted ladder rides the same fail-closed, no-agent-retry crash path.

Driven with a stubbed caller (no docker, no claude) — the gate NEVER calls a
real model here.

(The dead N≥2 diverse-lens review panel + its ``record_vote`` sink were deleted
in #409 — measured a no-op at N=1 for weeks. This file is what survived: the
degrade ladder + single-reviewer behaviour.)
"""

from __future__ import annotations

import pytest

from devclaw import task_queue
from devclaw.engine import EngineRequest
from devclaw.planner import PlannerError
from devclaw import quality
from devclaw.quality import review_diff, review_gate
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue

_DIFF = "diff --git a/f.py b/f.py\n--- a/f.py\n+++ b/f.py\n@@ -0,0 +1 @@\n+code\n"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _approve_json(summary: str = "ok") -> str:
    return '{"verdict": "approve", "summary": "%s", "issues": []}' % summary


def _blocker_json(location: str, problem: str) -> str:
    return (
        '{"verdict": "request_changes", "summary": "found a defect", "issues": ['
        '{"severity": "blocker", "location": "%s", "problem": "%s", "fix": "fix it"}]}'
        % (location, problem)
    )


def _caller_returning(*responses: str):
    """A caller that returns the next canned response per invocation."""
    seq = list(responses)
    idx = {"i": 0}

    async def caller(prompt: str) -> str:
        r = seq[idx["i"] % len(seq)]
        idx["i"] += 1
        return r

    return caller


# ============================ pure module: happy path ================

async def test_review_gate_verdict_identical_to_review_diff():
    """On the happy path ``review_gate`` just returns ``review_diff`` — same
    verdict dict on the same input. The ladder wrapper is invisible unless a
    timeout / unparseable crash engages it."""
    caller = _caller_returning(_blocker_json("f.py", "off-by-one"))
    single = await review_diff(goal="g", kind="implement_feature", diff=_DIFF, claude_caller=caller)
    gated = await review_gate(goal="g", kind="implement_feature", diff=_DIFF, claude_caller=caller)
    assert gated == single
    assert gated["verdict"] == "request_changes"


# ============================ cognition-timeout degradation ladder ====
#
# A large-but-legitimate diff can exhaust the review budget → the caller raises a
# timeout PlannerError → the gate fails CLOSED with no agent retry (#186). The
# ladder (PR4 / systemic fix #5) adds ONE rung *before* that hard fail: on a
# TIMEOUT, split the diff per file, review each independently, and union the
# verdicts. When the rung can't help it re-raises → the SAME fail-closed path.

_DIFF_A = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -0,0 +1 @@\n+aaa\n"
_DIFF_B = "diff --git a/b.py b/b.py\n--- a/b.py\n+++ b/b.py\n@@ -0,0 +1 @@\n+bbb\n"
_MULTI = _DIFF_A + _DIFF_B

_TIMEOUT_MSG = "claude --print timed out after 180000ms"


def _timeout_on_full_diff(per_file):
    """A caller that TIMES OUT on the whole-diff prompt (both files present) and
    otherwise delegates to ``per_file(prompt)`` for a per-file sub-diff prompt.
    Detects the full diff by the presence of BOTH file paths in the prompt."""
    async def caller(prompt: str) -> str:
        if "a/a.py" in prompt and "a/b.py" in prompt:
            raise PlannerError(_TIMEOUT_MSG)
        return await per_file(prompt)
    return caller


async def test_review_timeout_degrades_per_file_then_unions_verdicts():
    """The documented symptom: the whole diff times out. The ladder re-reviews it
    one file at a time and UNIONS the verdicts (evidence wins) — a single file's
    blocker forces request_changes, so the degraded verdict is >= as strict as a
    whole-diff review would have been, never laxer."""
    async def per_file(prompt: str) -> str:
        if "a/a.py" in prompt:
            return _blocker_json("a.py", "off-by-one")
        return _approve_json()

    result = await review_gate(
        goal="g", kind="implement_feature", diff=_MULTI,
        claude_caller=_timeout_on_full_diff(per_file),
    )
    assert result["verdict"] == "request_changes"
    assert any(i["location"] == "a.py" for i in result["blocking"])
    assert "per-file" in result["summary"]


async def test_review_timeout_degraded_all_approve_ships():
    """When every per-file sub-review approves, the unioned verdict is approve —
    the ladder lets a legitimate large diff earn a real pass instead of the hard
    timeout fail it used to hit."""
    async def per_file(prompt: str) -> str:
        return _approve_json()

    result = await review_gate(
        goal="g", kind="implement_feature", diff=_MULTI,
        claude_caller=_timeout_on_full_diff(per_file),
    )
    assert result["verdict"] == "approve"
    assert result["blocking"] == []


async def test_review_timeout_degrades_then_still_fails_closed_when_exhausted():
    """Ladder EXHAUSTED: a single-file diff can't be split any further, so a
    timeout re-raises the PlannerError → the queue's crash-marker, no-agent-retry
    fail-closed path (#186). Degradation NEVER manufactures an approval."""
    async def always_timeout(prompt: str) -> str:
        raise PlannerError(_TIMEOUT_MSG)

    with pytest.raises(PlannerError, match="timed out"):
        await review_gate(
            goal="g", kind="implement_feature", diff=_DIFF,
            claude_caller=always_timeout,
        )


async def test_review_timeout_ladder_fails_closed_when_a_sub_review_still_times_out():
    """A per-file sub-review that STILL times out (one genuinely huge file) RAISES
    through the ladder → the whole diff fails closed, never approved on the
    silence of the files that did come back."""
    async def always_timeout(prompt: str) -> str:
        raise PlannerError(_TIMEOUT_MSG)

    with pytest.raises(PlannerError, match="timed out"):
        await review_gate(
            goal="g", kind="implement_feature", diff=_MULTI,
            claude_caller=always_timeout,
        )


# ============================ signal-death (SIGKILL) degradation =======
#
# A ``claude --print`` KILLED BY A SIGNAL on an oversized diff surfaces as a
# NEGATIVE exit code ("exited -9"), NOT a timeout and NOT unparseable JSON — so
# before this fix it slipped past the ladder and hard-wedged the goal (the
# 2026-07-30 morning symptom on console-state-honesty). It is the SAME
# oversized-input death family as a timeout, so it now takes the SAME rung.

_SIGKILL_MSG = "claude --print exited -9. stderr:\n\nstdout:\n"


def _sigkill_on_full_diff(per_file):
    """A caller KILLED BY SIGNAL on the whole-diff prompt (both files present),
    otherwise delegating to ``per_file`` for a per-file sub-diff prompt."""
    async def caller(prompt: str) -> str:
        if "a/a.py" in prompt and "a/b.py" in prompt:
            raise PlannerError(_SIGKILL_MSG)
        return await per_file(prompt)
    return caller


async def test_review_signal_death_degrades_per_file_then_unions_verdicts():
    """The whole-diff review is SIGKILLed ("exited -9") on an oversized diff. The
    ladder now treats a signal death like a timeout — re-reviews per file and
    unions the verdicts — instead of wedging the goal on a hard fail-closed."""
    async def per_file(prompt: str) -> str:
        if "a/a.py" in prompt:
            return _blocker_json("a.py", "off-by-one")
        return _approve_json()

    result = await review_gate(
        goal="g", kind="implement_feature", diff=_MULTI,
        claude_caller=_sigkill_on_full_diff(per_file),
    )
    assert result["verdict"] == "request_changes"
    assert any(i["location"] == "a.py" for i in result["blocking"])
    assert "per-file" in result["summary"]


async def test_review_signal_death_still_fails_closed_when_subreviews_also_die():
    """A signal death that PERSISTS per-file RAISES through the ladder → the whole
    diff still fails closed (never approved on the silence of a killed reviewer).
    Degrading a signal death never manufactures an approval — #186 holds."""
    async def always_sigkill(prompt: str) -> str:
        raise PlannerError(_SIGKILL_MSG)

    with pytest.raises(PlannerError, match="exited -9"):
        await review_gate(
            goal="g", kind="implement_feature", diff=_MULTI,
            claude_caller=always_sigkill,
        )


async def test_review_ladder_disabled_reraises_timeout_without_fanning_out(monkeypatch):
    """Disabling the ladder (_DEGRADE_ENABLED=False) restores the pre-ladder gate
    exactly: a timeout re-raises immediately and NO per-file fan-out happens (the
    caller is invoked once, for the whole diff)."""
    monkeypatch.setattr(quality, "_DEGRADE_ENABLED", False)
    calls = {"n": 0}

    async def timing_out(prompt: str) -> str:
        calls["n"] += 1
        raise PlannerError(_TIMEOUT_MSG)

    with pytest.raises(PlannerError, match="timed out"):
        await review_gate(
            goal="g", kind="implement_feature", diff=_MULTI,
            claude_caller=timing_out,
        )
    assert calls["n"] == 1  # the ladder never engaged — one whole-diff call only


async def test_review_ladder_over_file_cap_fails_closed(monkeypatch):
    """A diff with more files than the per-file fan-out cap (_DEGRADE_MAX_FILES_DEFAULT)
    is NOT degraded (the fan-out would be too large a burst); it fails closed and a
    human splits it. Cap=1 with a 2-file diff → the original timeout re-raises."""
    monkeypatch.setattr(quality, "_DEGRADE_MAX_FILES_DEFAULT", 1)

    async def timing_out(prompt: str) -> str:
        raise PlannerError(_TIMEOUT_MSG)

    with pytest.raises(PlannerError, match="timed out"):
        await review_gate(
            goal="g", kind="implement_feature", diff=_MULTI,
            claude_caller=timing_out,
        )


def _nonjson_on_full_diff(per_file):
    """A caller that returns NON-JSON prose on the whole-diff prompt (both files)
    and delegates to ``per_file`` for a per-file sub-diff. Mirrors the #381
    symptom: an oversized diff makes the model ramble without ever emitting a
    verdict object — the same 'input too big' failure a timeout is."""
    async def caller(prompt: str) -> str:
        if "a/a.py" in prompt and "a/b.py" in prompt:
            return "a long prose review of the whole change with no json verdict object"
        return await per_file(prompt)
    return caller


async def test_unparseable_verdict_on_oversized_diff_degrades_per_file_and_earns_verdict():
    """#381: a non-JSON crash on the WHOLE diff now degrades like a timeout — the
    model most likely rambled because the diff was too big. The ladder splits per
    file; a per-file blocker still forces request_changes (evidence wins)."""
    async def per_file(prompt: str) -> str:
        if "a/a.py" in prompt:
            return _blocker_json("a.py", "off-by-one")
        return _approve_json()

    result = await review_gate(
        goal="g", kind="implement_feature", diff=_MULTI,
        claude_caller=_nonjson_on_full_diff(per_file),
    )
    assert result["verdict"] == "request_changes"
    assert any(i["location"] == "a.py" for i in result["blocking"])
    assert "per-file" in result["summary"]


async def test_unparseable_verdict_still_fails_closed_when_subreviews_also_unparseable():
    """If the per-file sub-reviews ALSO can't parse (the non-JSON was deterministic,
    not size-driven), they RAISE → the whole diff still fails closed. #186 holds —
    degradation never manufactures an approval from non-JSON."""
    async def not_json(prompt: str) -> str:
        return "prose, never a verdict object"

    with pytest.raises(PlannerError):
        await review_gate(
            goal="g", kind="implement_feature", diff=_MULTI,
            claude_caller=not_json,
        )


async def test_quota_shaped_unparseable_is_not_degraded_and_reraises_for_pause():
    """The #381 guard: a non-JSON crash whose RAW output is usage-limit prose must
    NOT fan out per-file — that would spray `claude` calls into a live cap. It
    re-raises unchanged (one whole-diff call only) so the queue's quota classifier
    PAUSES instead."""
    calls = {"n": 0}

    async def quota_prose(prompt: str) -> str:
        calls["n"] += 1
        return "Internal error: You're out of extra usage · resets 9pm (UTC)"

    with pytest.raises(PlannerError):
        await review_gate(
            goal="g", kind="implement_feature", diff=_MULTI,
            claude_caller=quota_prose,
        )
    assert calls["n"] == 1  # ladder did NOT engage — quota-shaped re-raises to pause


async def test_ladder_preserves_raw_response_for_quota_classification():
    """Fail-closed with fidelity: if a per-file sub-review comes back as usage-
    limit PROSE (no JSON), the PlannerError that propagates out of the ladder must
    still carry that raw text on ``.raw`` — the queue's quota guard classifies the
    STRING, and without the prose a session limit reads as a permanent defect
    (the 2026-07-14 shape). The ladder must not swallow it."""
    async def per_file(prompt: str) -> str:
        return "You've hit your session limit · resets 5:20pm (Europe/Dublin)"

    with pytest.raises(PlannerError) as ei:
        await review_gate(
            goal="g", kind="implement_feature", diff=_MULTI,
            claude_caller=_timeout_on_full_diff(per_file),
        )
    assert "session limit" in (getattr(ei.value, "raw", "") or "")


# ============================ queue integration ======================

@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


@pytest.fixture(autouse=True)
def _enable_gate_and_fake_diff(monkeypatch):
    monkeypatch.setattr(task_queue, "REVIEW_GATE_ENABLED", True)

    async def fake_diff(_host_dir, _base=""):
        return _DIFF

    monkeypatch.setattr(task_queue, "_git_diff", fake_diff)


def _ok_gate_runner(calls: list):
    async def runner(req: EngineRequest):
        calls.append(req.goal)
        gate = {"ran": True, "cmd": "pytest", "passed": True, "exit_code": 0,
                "timed_out": False, "output": ""}
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": gate}
    return runner


def test_queue_default_reviewer_is_review_gate(store):
    """The queue's default reviewer is ``review_gate`` — the single adversarial
    reviewer wrapped in the degradation ladder."""
    q = TaskQueue(store)
    assert q._reviewer is review_gate


async def test_review_gate_through_queue_ships_like_the_single_reviewer(store, monkeypatch):
    """Through the queue: a clean approve ships the task done on the first try, no
    needless retry."""
    import functools
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)

    async def one_approve(prompt: str) -> str:
        return _approve_json()

    reviewer = functools.partial(review_gate, claude_caller=one_approve)
    calls: list = []
    q = TaskQueue(store, runner=_ok_gate_runner(calls), reviewer=reviewer)
    # strict so the review gate is actually consulted (spec 001: trust skips it).
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g",
                   verify_cmd="pytest", strictness="strict")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert len(calls) == 1


async def test_queue_review_timeout_exhausted_fails_closed_without_agent_retry(
    store, monkeypatch
):
    """End-to-end through the queue: an unsplittable review timeout rides the
    SAME crash-marker, no-agent-retry path as any review crash (#186) — the task
    fails closed with the actionable 'split / review by hand' reason and the agent
    is NOT re-run. (The autouse fixture's _git_diff returns a single-file diff, so
    the ladder can't split → re-raises the timeout.)"""
    import functools
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 3)  # generous — must NOT be used
    calls: list = []

    async def timing_out(prompt: str) -> str:
        raise PlannerError(_TIMEOUT_MSG)

    reviewer = functools.partial(review_gate, claude_caller=timing_out)
    q = TaskQueue(store, runner=_ok_gate_runner(calls), reviewer=reviewer)
    # strict: the review gate is only consulted under strict now (spec 001), and
    # a consulted-but-crashed gate still fails closed (#186).
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g",
                   verify_cmd="pytest", strictness="strict")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"
    assert "review gate crashed" in (t.error or "")   # _REVIEW_CRASH_MARKER
    assert "Not auto-retried" in (t.error or "")       # actionable fail-fast
    assert len(calls) == 1                              # NO agent retry on an exhausted ladder
