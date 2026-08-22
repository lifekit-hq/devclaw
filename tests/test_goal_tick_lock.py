"""Tranche 1 / PR8 — the per-goal ``asyncio.Lock`` around ``tick_goal``.

CAS (``GoalStore.transition``'s optimistic-concurrency check, Tranche 1/PR4)
already guarantees CORRECTNESS when two ticks race the SAME goal — the ONLY
same-goal concurrency left standing after PR4 is an MCP-driven ``tick_one``
(manual poke, ops-agent) overlapping the heartbeat's ``tick_all`` sweep for
that goal. Pre-PR8, that race meant BOTH ticks ran a full round (workspace
prep + dispatch can take minutes) and the LOSER abandoned its entire round to
a ``TransitionConflict`` — correct, but wasteful and a confusing trace. PR8's
lock adds EFFICIENCY + LEGIBILITY on top of CAS: the second tick simply waits
for the first to finish, then reads FRESH state.

The mid-tick await these tests park on is ``prepare_ws`` — the thin advance
path's last await before the dispatch transaction (the planner await that
used to play this role is gone, demolition P3b).

Named regression tests, each with a one-line comment naming the property it
proves. See ``devclaw/goal/tick.py``'s ``_TICK_LOCKS`` / ``_tick_lock`` /
``tick_goal`` (the lock's own comment has the full rationale, including WHY
``steer_goal`` / ``cancel_goal`` / ``evaluate_goal`` stay lock-free).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from devclaw.goal import store as store_mod
from devclaw.goal.models import GoalStatus, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from devclaw.goal.transitions import Event, State
from tests.goal_fakes import Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


async def _tick(store, goal_id, evaluator, engine, notifier, *, prepare=fake_prepare):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=prepare,
    )


class _ParkingPrepare:
    """A prepare_ws hook that counts calls, then PARKS on a shared
    ``asyncio.Event`` before returning. Once ``release`` is set it stays set
    (``asyncio.Event`` semantics), so any call that starts AFTER the release
    is a no-op wait — this is what lets one instance model both "the first
    call parks" and "a later call returns immediately"."""

    def __init__(self, release: asyncio.Event) -> None:
        self.release = release
        self.calls = 0

    async def __call__(self, workspace_dir, repo_url=None, branch=None):
        self.calls += 1
        await self.release.wait()
        return branch or "main"


async def _let_tasks_run(n: int = 25) -> None:
    """Pump the event loop so every currently-schedulable task advances to
    its next await point, without actually resolving anything ourselves.
    Safe to over-call: a task parked on an unset Event or blocked acquiring
    a held Lock simply stays parked/blocked — extra iterations can't cause a
    false pass."""
    for _ in range(n):
        await asyncio.sleep(0)


# ---- 1. same-goal ticks serialize (THE headline test) ----------------------


@pytest.mark.asyncio
async def test_same_goal_ticks_serialize(tmp_path):
    """Two concurrent tick_goal calls for the SAME goal — modeling an
    MCP-driven tick_one racing the heartbeat's tick_all, the one same-goal
    race PR4's CAS leaves standing. Pre-PR8 both would reach the advance
    dispatch in parallel and the second writer would lose its whole round to
    Outcome.CONFLICT (see devclaw/goal/tick.py's _tick_lock comment — this
    is deliberately demonstrated only in prose here, not as a second code
    path, since removing the lock would just be reverting this PR). With the
    lock: tick2 cannot even acquire it — let alone start its dispatch —
    until tick1's ENTIRE tick has finished; tick2 then reads FRESH state
    (the in-flight ref tick1 just created) and polls it instead of
    double-dispatching a second advance session off a stale idle snapshot."""
    store = _store(tmp_path, Clock())
    # cadence="0s": a stale idle read would be "due" again immediately, so a
    # lockless tick2 WOULD dispatch a second advance — which is exactly what
    # the dispatched-count assertion below rules out.
    seed_goal(tmp_path, "g", cadence="0s")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))

    release = asyncio.Event()
    prepare = _ParkingPrepare(release)
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(terminal=False, status="running", detail=""))
    notifier = RecordingNotifier()

    task1 = asyncio.create_task(_tick(store, "g", evaluator, engine, notifier, prepare=prepare))
    await _let_tasks_run()
    assert prepare.calls == 1  # tick1 reached the advance dispatch and is parked on `release`

    task2 = asyncio.create_task(_tick(store, "g", evaluator, engine, notifier, prepare=prepare))
    await _let_tasks_run()
    # Without the lock, tick2 would have read (stale idle) status and reached
    # prepare_ws too — a second, concurrent dispatch round — by now. With it,
    # tick2 is blocked acquiring _tick_lock("g") — held by tick1 — and never
    # even starts its body.
    assert prepare.calls == 1

    release.set()
    out1, out2 = await asyncio.gather(task1, task2)

    assert out1 is Outcome.DISPATCHED         # tick1 dispatched the advance session
    assert out2 is Outcome.IN_FLIGHT          # tick2 read FRESH state and polled the ref
    assert len(engine.dispatched) == 1        # never a second, stale-snapshot dispatch
    assert engine.polls == 1                  # tick2 did real work, not a skipped round
    assert Outcome.CONFLICT not in (out1, out2)


# ---- 2. different goals do not serialize -----------------------------------


@pytest.mark.asyncio
async def test_different_goals_do_not_serialize(tmp_path):
    """The lock is per-goal, not global — two DIFFERENT goals ticking
    concurrently must both reach their advance dispatch in parallel; one
    goal's tick must never wait behind another goal's in-flight dispatch."""
    store = _store(tmp_path, Clock())
    # Distinct workspaces: this test isolates the per-goal TICK LOCK, and two
    # goals on ONE project are now serialized by the single-writer project hold
    # (spec 010 P1) — a different mechanism, which would mask what this asserts.
    seed_goal(tmp_path, "g1", workspace_dir="/repos/g1")
    seed_goal(tmp_path, "g2", workspace_dir="/repos/g2")
    store.save_status("g1", GoalStatus(phase="idle", lifecycle="executing"))
    store.save_status("g2", GoalStatus(phase="idle", lifecycle="executing"))

    release = asyncio.Event()
    prepare1 = _ParkingPrepare(release)
    prepare2 = _ParkingPrepare(release)
    evaluator = FakeClaude()
    engine = FakeEngine()
    notifier = RecordingNotifier()

    task1 = asyncio.create_task(_tick(store, "g1", evaluator, engine, notifier, prepare=prepare1))
    task2 = asyncio.create_task(_tick(store, "g2", evaluator, engine, notifier, prepare=prepare2))
    await _let_tasks_run()

    # BOTH dispatch rounds started before either finished — a different-goal
    # Lock object guards g2, so g1's held lock never blocks it.
    assert prepare1.calls == 1
    assert prepare2.calls == 1

    release.set()
    out1, out2 = await asyncio.gather(task1, task2)

    assert out1 is Outcome.DISPATCHED
    assert out2 is Outcome.DISPATCHED
    assert len(engine.dispatched) == 2


# ---- 3. the lock does not deadlock with the choke-point catch --------------


@pytest.mark.asyncio
async def test_illegal_transition_releases_the_lock(tmp_path, monkeypatch):
    """PR8 wraps tick_goal's ENTIRE body — including the existing
    IllegalTransition/TransitionConflict choke-point catch — in the per-goal
    lock. This proves that catch does not leave the lock held: force an
    IllegalTransition the same way test_goal_transitions.py's regression
    does (yank the real (EXECUTING_IDLE, DISPATCH_ACTION) edge the thin
    advance dispatch needs out of LEGAL, modeling 'the table is missing a
    real code path'); tick1 force-blocks internally and returns
    Outcome.BLOCKED normally (not an unhandled raise). A SECOND, independent
    tick_goal call for the SAME goal — awaited right after, bounded by
    asyncio.wait_for so a real regression fails loud instead of hanging the
    suite — must then dispatch normally and complete; a leaked lock would
    hang it forever."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))

    real_legal = dict(store_mod.LEGAL)
    patched = dict(store_mod.LEGAL)
    del patched[(State.EXECUTING_IDLE, Event.DISPATCH_ACTION)]
    monkeypatch.setattr(store_mod, "LEGAL", patched)

    evaluator = FakeClaude()
    engine = FakeEngine()
    notifier = RecordingNotifier()

    out1 = await asyncio.wait_for(
        _tick(store, "g", evaluator, engine, notifier), timeout=5,
    )
    assert out1 is Outcome.BLOCKED             # the internal catch fired, not an unhandled raise

    monkeypatch.setattr(store_mod, "LEGAL", real_legal)  # restore — the modeled bug is one-shot
    s = store.load_status("g")
    store.transition("g", Event.UNBLOCK, replace(s, phase="idle", actions_dispatched=0), expect=s)

    dispatched_before = len(engine.dispatched)
    out2 = await asyncio.wait_for(
        _tick(store, "g", evaluator, engine, notifier), timeout=5,
    )

    assert out2 is Outcome.DISPATCHED
    assert len(engine.dispatched) == dispatched_before + 1  # a second, independent tick really ran
