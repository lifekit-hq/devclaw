"""Host-cognition concurrency cap — regression tests (2026-08-13).

Every host-side ``claude --print`` cognition call (review gate, evaluator,
planner, done-gate) funnels through the spawn chokepoint in ``llm_call``, but
that population is invisible to the task cap (``DEVCLAW_MAX_CONCURRENT`` counts
sandboxed tasks, not host processes): up to 4 review gates + goal cognition ran
concurrently on the host and the kernel OOM-killed them (``exited -9``, ×117
lifetime). ``_call_claude_once`` now gates the subprocess behind a lazy
module-level ``asyncio.Semaphore`` sized by ``DEVCLAW_MAX_HOST_COGNITION``
(default 2) — queued callers simply wait, nothing errors.

These pin the cap by patching ``_spawn_claude_once`` (the subprocess boundary
UNDER the semaphore) with a slow stub that tracks observed concurrency, so they
exercise the gate itself without a real subprocess and run instantly.
"""

from __future__ import annotations

import asyncio

import pytest

from devclaw import llm_call


@pytest.fixture(autouse=True)
def _fresh_semaphore(monkeypatch):
    """Reset the lazy holder so each test builds its semaphore from ITS env.
    (The holder is also loop-keyed, but an explicit reset keeps the tests
    independent of pytest-asyncio's loop reuse policy.)"""
    monkeypatch.setattr(llm_call, "_host_cognition_sem", None)
    monkeypatch.setattr(llm_call, "_host_cognition_sem_loop", None)


class _ConcurrencyProbe:
    """A slow fake ``_spawn_claude_once`` that records peak concurrency."""

    def __init__(self) -> None:
        self.running = 0
        self.peak = 0
        self.completed = 0

    async def __call__(self, prompt, model=None, *, role="unknown", timeout_ms=None):
        self.running += 1
        self.peak = max(self.peak, self.running)
        await asyncio.sleep(0.01)  # long enough for all peers to be in flight
        self.running -= 1
        self.completed += 1
        return f"ok:{prompt}"


async def test_host_cognition_calls_capped_by_semaphore(monkeypatch):
    """Fire 6 concurrent calls with the default cap (2): every call completes
    (queued callers wait — no errors), but at most 2 stubs ever overlap."""
    monkeypatch.delenv("DEVCLAW_MAX_HOST_COGNITION", raising=False)
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(llm_call, "_spawn_claude_once", probe)

    results = await asyncio.gather(
        *(llm_call.call_claude(f"p{i}") for i in range(6))
    )

    assert sorted(results) == [f"ok:p{i}" for i in range(6)]
    assert probe.completed == 6
    assert probe.peak == 2


async def test_host_cognition_cap_env_override(monkeypatch):
    """``DEVCLAW_MAX_HOST_COGNITION=3`` widens the cap — read at first use,
    not at import."""
    monkeypatch.setenv("DEVCLAW_MAX_HOST_COGNITION", "3")
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(llm_call, "_spawn_claude_once", probe)

    await asyncio.gather(*(llm_call.call_claude(f"p{i}") for i in range(6)))

    assert probe.completed == 6
    assert probe.peak == 3


async def test_semaphore_is_rebuilt_for_a_new_event_loop(monkeypatch):
    """An ``asyncio.Semaphore`` binds to the loop that first acquires it; the
    lazy holder is loop-keyed so a later loop (each ``asyncio.run`` in tests;
    a service restart's fresh loop) gets a fresh semaphore instead of a
    'bound to a different event loop' RuntimeError."""
    probe = _ConcurrencyProbe()
    monkeypatch.setattr(llm_call, "_spawn_claude_once", probe)

    # Prime the holder on the CURRENT loop, then use it from two brand-new
    # loops in worker threads — each must transparently get its own semaphore.
    await llm_call.call_claude("prime")

    def _run_in_new_loop():
        return asyncio.run(llm_call.call_claude("fresh-loop"))

    out1 = await asyncio.to_thread(_run_in_new_loop)
    out2 = await asyncio.to_thread(_run_in_new_loop)
    assert out1 == out2 == "ok:fresh-loop"
    assert probe.completed == 3


def test_host_cognition_env_parse_is_failsafe():
    """Same fail-safe contract as the timeout/retry parsers: garbage falls back
    to the default, and 0/negative are NOT honored (a 0-permit semaphore would
    deadlock every cognition call forever)."""
    f = llm_call._max_host_cognition_from_env
    assert f(None) == 2      # unset → default
    assert f("") == 2        # blank → default
    assert f("abc") == 2     # garbage → default
    assert f("1.5") == 2     # non-int → default
    assert f("0") == 2       # would deadlock — not honored
    assert f("-3") == 2      # negative → default
    assert f("1") == 1       # fully serialized host cognition is legal
    assert f("4") == 4


# ---- the cap is a LIVE dial (2026-08-31) ------------------------------------
# Same tripwire class as tests/test_live_concurrency_dial.py, different
# population: that one caps sandboxed tasks, this one caps host subprocesses.
# The invariant with teeth is that changing the cap can never let the host
# admit MORE than the cap while earlier calls are still running — rebuilding a
# fixed-capacity semaphore would do exactly that, and over-admission is what
# OOM-killed 117 cognition calls.

@pytest.fixture(autouse=True)
def _fresh_cap_override(monkeypatch):
    monkeypatch.setattr(llm_call, "_host_cognition_cap_override", None)


def test_live_override_beats_the_env_value(monkeypatch):
    monkeypatch.setattr(llm_call._config, "max_host_cognition_raw", lambda: "4")
    assert llm_call.host_cognition_cap() == 4

    llm_call.set_host_cognition_cap(1)
    assert llm_call.host_cognition_cap() == 1

    llm_call.set_host_cognition_cap(None)
    assert llm_call.host_cognition_cap() == 4, "None must CLEAR, not pin a value"


@pytest.mark.parametrize("bad", [0, -1, True, False, 1.5, "two"])
def test_a_cap_that_would_deadlock_cognition_is_rejected(bad):
    with pytest.raises(ValueError):
        llm_call.set_host_cognition_cap(bad)
    assert llm_call._host_cognition_cap_override is None


def test_lowering_the_cap_never_over_admits_while_calls_are_in_flight():
    """The reason this is a dynamic limiter and not a rebuilt semaphore.

    Rebuilding would hand a fresh full-capacity object to new callers while the
    old holders are still running, so the host would briefly exceed the cap.
    Here: fill to 2, drop the cap to 1, and assert nothing new is admitted
    until in-flight drops below the new cap.
    """
    async def scenario():
        llm_call.set_host_cognition_cap(2)
        limiter = llm_call._DynamicLimiter()
        a = await limiter.__aenter__()
        b = await limiter.__aenter__()
        assert limiter.in_flight == 2

        llm_call.set_host_cognition_cap(1)

        blocked = asyncio.create_task(limiter.__aenter__())
        await asyncio.sleep(0.05)
        assert not blocked.done(), "admitted a 3rd call while 2 were in flight at cap=1"

        await limiter.__aexit__()  # in_flight 2 -> 1, still not under cap=1
        await asyncio.sleep(0.05)
        assert not blocked.done(), "admitted while in_flight was still at the cap"

        await limiter.__aexit__()  # in_flight 1 -> 0, now under cap
        await asyncio.wait_for(blocked, timeout=1)
        assert limiter.in_flight == 1

        await limiter.__aexit__()
        del a, b

    asyncio.run(scenario())


def test_raising_the_cap_admits_more_on_the_next_release():
    async def scenario():
        llm_call.set_host_cognition_cap(1)
        limiter = llm_call._DynamicLimiter()
        await limiter.__aenter__()

        waiting = asyncio.create_task(limiter.__aenter__())
        await asyncio.sleep(0.05)
        assert not waiting.done()

        llm_call.set_host_cognition_cap(3)
        await limiter.__aexit__()  # release wakes the waiter, which re-reads the cap

        await asyncio.wait_for(waiting, timeout=1)
        assert limiter.in_flight == 1
        await limiter.__aexit__()

    asyncio.run(scenario())
