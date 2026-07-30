"""Bounded transient-retry around the cognition primitive — regression tests.

The clean-run fix (2026-07-30): the #1 wedge of an unattended run was the
host-side ``claude --print`` call failing TRANSIENTLY — OOM-killed mid-call
(``exited -9`` under host memory pressure), timed out, or an ``overloaded`` /
network blip. Each used to raise straight through and dead-stop the goal +
ping the owner. ``call_claude`` now retries the TRANSIENT class a bounded number
of times with a short backoff, while QUOTA/RATE/AUTH still pause on the FIRST
failure (retrying a live cap only burns it) and a REAL bug still fails fast.

These pin the wrapper's decision logic directly by patching
``_call_claude_once`` (the single-attempt boundary), so they exercise the retry
policy independent of the subprocess/envelope plumbing (covered elsewhere) and
run instantly. The real backoff sleep is neutralized so a transient never costs
wall-clock in the suite.
"""

from __future__ import annotations

import pytest

from devclaw import llm_call
from devclaw.llm_call import PlannerError, call_claude


@pytest.fixture(autouse=True)
def _no_backoff_sleep(monkeypatch):
    """Never actually sleep the backoff in tests (would be 5s/20s of dead time)."""
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(llm_call, "_RETRY_SLEEP", _instant)


def _sequence_caller(monkeypatch, outcomes):
    """Patch ``_call_claude_once`` to yield ``outcomes`` in order: a ``str`` is
    returned, an ``Exception`` is raised. Returns a dict whose ``n`` counts how
    many attempts the wrapper actually made."""
    calls = {"n": 0}
    it = iter(outcomes)

    async def fake_once(prompt, model=None, *, role="unknown", timeout_ms=None):
        calls["n"] += 1
        outcome = next(it)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(llm_call, "_call_claude_once", fake_once)
    return calls


# ---- the headline behavior: a transient OOM recovers instead of dead-stopping --


async def test_signal_death_oom_retries_instead_of_dead_stopping(monkeypatch):
    """A ``claude --print`` killed by OOM (exit -9) is a transient infra hiccup
    (#448 classifies it TRANSIENT) — the primitive retries and the run carries
    on, rather than the goal dead-stopping and pinging the owner."""
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 2)
    calls = _sequence_caller(monkeypatch, [
        PlannerError("claude --print exited -9. stderr:  stdout: "),
        "recovered",
    ])
    assert await call_claude("plan it", role="goal_planner") == "recovered"
    assert calls["n"] == 2  # one failure + one successful retry


async def test_timeout_retries_until_success(monkeypatch):
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 2)
    calls = _sequence_caller(monkeypatch, [
        PlannerError("claude --print timed out after 180000ms"),
        PlannerError("claude --print timed out after 180000ms"),
        "ok",
    ])
    assert await call_claude("p") == "ok"
    assert calls["n"] == 3  # two transient failures, third attempt succeeds


async def test_retries_are_bounded_then_raise_last_error(monkeypatch):
    """A PERSISTENT transient failure must still fail — bounded, never an
    infinite burn — and raise the LAST error verbatim (so the review gate still
    fails closed and the planner still surfaces it)."""
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 2)
    err = PlannerError("claude --print exited -9. stderr:")

    async def always_fail(*a, **k):
        always_fail.n += 1
        raise err

    always_fail.n = 0
    monkeypatch.setattr(llm_call, "_call_claude_once", always_fail)
    with pytest.raises(PlannerError) as ei:
        await call_claude("p")
    assert ei.value is err
    assert always_fail.n == 3  # 1 initial + 2 retries, then give up


# ---- the pausing/real kinds must NOT be retried ------------------------------


async def test_quota_is_not_retried_so_pause_machinery_fires(monkeypatch):
    """A usage cap must raise on the FIRST failure — retrying would spray doomed
    calls into a live quota. Classified QUOTA (not TRANSIENT) → no retry."""
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 5)
    calls = _sequence_caller(monkeypatch, [
        PlannerError("claude --print exited 1. stdout:\nYou've reached your usage limit"),
        "should-not-be-reached",
    ])
    with pytest.raises(PlannerError):
        await call_claude("p")
    assert calls["n"] == 1


async def test_auth_is_not_retried(monkeypatch):
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 5)
    calls = _sequence_caller(monkeypatch, [
        PlannerError("failed to authenticate: OAuth session expired and could not be refreshed"),
        "x",
    ])
    with pytest.raises(PlannerError):
        await call_claude("p")
    assert calls["n"] == 1


async def test_real_bug_is_not_retried(monkeypatch):
    """A clean nonzero exit carrying a real traceback is a bug (REAL), not a
    transient — fail fast, don't burn retries on a deterministic failure."""
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 5)
    calls = _sequence_caller(monkeypatch, [
        PlannerError("claude --print exited 1. stderr: Traceback (most recent call last): ValueError"),
        "x",
    ])
    with pytest.raises(PlannerError):
        await call_claude("p")
    assert calls["n"] == 1


async def test_zero_retries_env_disables_retry(monkeypatch):
    """``DEVCLAW_COGNITION_RETRIES=0`` → exactly one attempt (the pre-fix
    behavior), even for a transient."""
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 0)
    calls = _sequence_caller(monkeypatch, [
        PlannerError("claude --print exited -9. stderr:"),
        "unreached",
    ])
    with pytest.raises(PlannerError):
        await call_claude("p")
    assert calls["n"] == 1


async def test_success_first_try_makes_no_extra_calls(monkeypatch):
    """The happy path is untouched — one attempt, no retry, no sleep."""
    monkeypatch.setattr(llm_call, "COGNITION_MAX_RETRIES", 2)
    calls = _sequence_caller(monkeypatch, ["done"])
    assert await call_claude("p") == "done"
    assert calls["n"] == 1


# ---- the pure helpers --------------------------------------------------------


def test_retries_env_parse_is_failsafe():
    f = llm_call._cognition_retries_from_env
    assert f(None) == 2      # unset → default
    assert f("") == 2        # blank → default
    assert f("abc") == 2     # garbage → default
    assert f("1.5") == 2     # non-int → default
    assert f("-3") == 2      # negative → default
    assert f("0") == 0       # explicit disable is honored
    assert f("4") == 4


def test_backoff_is_geometric_capped_and_honors_stated_hint():
    b = llm_call._retry_backoff_s
    assert b(0, None) == 5.0       # base
    assert b(1, None) == 20.0      # base * 4
    assert b(9, None) == 60.0      # capped
    assert b(0, 30) == 30.0        # a provider-stated hint wins
    assert b(0, 999) == 60.0       # a stated hint is still capped
