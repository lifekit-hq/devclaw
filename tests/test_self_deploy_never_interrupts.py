"""Tripwire: a self-deploy never recreates the instance while a task is running.

Recreating ``devclaw-mcp`` SIGKILLs every in-flight sandbox. That was tolerable
while the only thing arming a self-deploy was devclaw closing its own PR — rare,
and the goal that triggered it had just finished. Since
``specs/tiny/merge-to-main-redeploys.md`` EVERY push to main arms one, so the
quiescence gate went from a nicety to the thing standing between a routine merge
and a killed sandbox mid-run.

Denys, 2026-09-09: *"I want deploy to be part of the CI/CD pipeline. When we
merge to main it redeploys. The only thing — I don't want it to interrupt an
active run."* This file is that sentence, executable.
"""
from __future__ import annotations

import asyncio

from devclaw.goal import self_deploy


class _State:
    """The four methods ``maybe_trigger`` touches."""

    def __init__(self, *, pending, running: int) -> None:
        self._pending = pending
        self._running = running
        self.recorded: list[dict] = []
        self.cleared = False

    def deploy_pending(self):
        return self._pending

    def count_running(self) -> int:
        return self._running

    def record_deploy_last(self, **kw) -> None:
        self.recorded.append(kw)

    def clear_deploy_pending(self) -> None:
        self.cleared = True


def _run(state, *, now_ms: int = 1_000_000):
    return asyncio.run(self_deploy.maybe_trigger(state, now_ms=now_ms))


def _armed(since_ms: int = 999_000):
    return ("abc123", "ci", since_ms)


def test_a_running_task_holds_the_deploy(monkeypatch) -> None:
    """THE guarantee. A merge armed a deploy and a task is mid-run: the deploy
    waits. If this ever fires, a merge kills whatever the worker was doing and
    the goal loses the session."""
    fired: list[str] = []
    monkeypatch.setattr(
        self_deploy, "_trigger",
        lambda slug: fired.append(slug) or (True, ""),  # type: ignore[func-returns-value]
    )
    monkeypatch.setenv("DEVCLAW_SELF_REPO", "lifekit-hq/devclaw")

    state = _State(pending=_armed(), running=1)
    assert _run(state) is None, "a deploy fired while a task was running"
    assert fired == []
    assert not state.cleared, "the pending deploy must survive to fire later"


def test_quiescence_releases_the_held_deploy(monkeypatch) -> None:
    """The other half: holding forever would mean merges never ship. Once the
    last task settles, the same armed deploy fires on the next heartbeat."""
    fired: list[str] = []

    async def _fake(slug):
        fired.append(slug)
        return True, ""

    monkeypatch.setattr(self_deploy, "_trigger", _fake)
    monkeypatch.setenv("DEVCLAW_SELF_REPO", "lifekit-hq/devclaw")

    state = _State(pending=_armed(), running=0)
    assert _run(state) == "triggered"
    assert fired == ["lifekit-hq/devclaw"]
    assert state.cleared, "a fired deploy is consumed, never re-fired every tick"


def test_nothing_armed_costs_nothing(monkeypatch) -> None:
    """The idle path stays a single meta read — no subprocess, no network, no
    cognition. The heartbeat runs this every sweep forever."""
    def _boom(_slug):  # pragma: no cover — must never be reached
        raise AssertionError("triggered with nothing armed")

    monkeypatch.setattr(self_deploy, "_trigger", _boom)

    state = _State(pending=None, running=0)
    assert _run(state) is None
    assert state.recorded == []
    assert not state.cleared


class _ReconcileState(_State):
    def __init__(self, *, last=None, running: int = 0) -> None:
        super().__init__(pending=None, running=running)
        self._last = last
        self.armed: list[dict] = []

    def deploy_last(self):
        return self._last

    def set_deploy_pending(self, **kw) -> None:
        self.armed.append(kw)
        self._pending = (kw["sha"], kw["goal_id"], kw["since_ms"])


def _reconcile(monkeypatch, state, *, head: str, running_sha: str = "old1") -> list[str]:
    fired: list[str] = []

    async def _fake(slug):
        fired.append(slug)
        return True, ""

    async def _head(_slug):
        return head

    monkeypatch.setattr(self_deploy, "_trigger", _fake)
    monkeypatch.setattr(self_deploy, "_main_head", _head)
    monkeypatch.setenv("DEVCLAW_SELF_REPO", "lifekit-hq/devclaw")
    monkeypatch.setenv("DEVCLAW_GIT_SHA", running_sha)
    _run(state, now_ms=10_000_000)
    return fired


def test_a_hand_merge_is_deployed_once_quiescent(monkeypatch) -> None:
    """Main moved with no arm (a merge done by hand): the heartbeat notices the
    running build trails it, arms, and fires on quiescence — and still holds
    for a running task."""
    held = _ReconcileState(running=1)
    assert _reconcile(monkeypatch, held, head="new2") == []
    assert held.armed and not held.cleared

    state = _ReconcileState()
    assert _reconcile(monkeypatch, state, head="new2") == ["lifekit-hq/devclaw"]
    assert state.armed[0]["sha"] == "new2"


def test_reconcile_never_loops_or_double_fires(monkeypatch) -> None:
    """Level-triggered, so it must not re-arm: in sync, an already-deployed
    sha (a rollback leaves the box behind), or a deploy still landing."""
    for state, head in (
        (_ReconcileState(), "old1"),
        (_ReconcileState(last={"sha": "new2", "at_ms": 1, "outcome": "triggered"}), "new2"),
        (_ReconcileState(last={"sha": "mid", "at_ms": 9_999_000}), "new2"),
    ):
        assert _reconcile(monkeypatch, state, head=head) == []
        assert state.armed == []


def test_an_unfired_arm_is_retried_after_the_settle_window(monkeypatch) -> None:
    """An expired or failed arm never deployed anything: the box still trails
    main, so the next heartbeat past the settle window arms the same sha again."""
    for outcome in ("expired", "trigger_failed"):
        state = _ReconcileState(last={"sha": "new2", "at_ms": 1, "outcome": outcome})
        assert _reconcile(monkeypatch, state, head="new2") == ["lifekit-hq/devclaw"]
        assert state.armed[0]["sha"] == "new2"
