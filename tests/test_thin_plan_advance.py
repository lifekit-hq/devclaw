"""The THIN long_lived executing path (demolition P3, flag-gated by
``DEVCLAW_THIN_PLAN`` → ``TickContext.thin_plan_enabled``).

With the flag ON, a long_lived executing goal runs ZERO per-tick planner
cognition: the tick mechanically dispatches "advance the goal / maintain
PLAN.md" worker sessions and lets the grounded done-gate judge completion. With
the flag OFF (default), the per-tick planner path is byte-unchanged.

The load-bearing assertions (mirroring the planner path's own guardrail):
  * an idle / blocked thin tick spends ZERO tokens (planner AND evaluator at
    calls == 0) — the Pro quota guarantee must survive the planner cut;
  * a due / steered thin tick dispatches an advance session WITHOUT the planner;
  * a successful advance settle PROPOSES done (opens the grounded done-gate)
    without the planner;
  * the flag OFF still runs the planner (additive + gated — no behavior change
    until the operator opts in).
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)

ACT_FEATURE = json.dumps(
    {"decision": "act", "note": "feat",
     "actions": [{"tool": "implement_feature", "goal": "add /health", "open_pr": True}]}
)


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


async def _thin_tick(store, goal_id, planner, evaluator, engine, notifier, *, thin=True, verify_done=True):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        planner_caller=planner, evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        eval_every=99, verify_done=verify_done, thin_plan_enabled=thin,
    )


# ---- the guardrail: thin idle / blocked ticks are zero-token ---------------


@pytest.mark.asyncio
async def test_thin_idle_tick_spends_zero_tokens(tmp_path):
    """A long_lived thin tick with no work and a not-due cadence must plan
    nothing — no planner, no evaluator, no dispatch. This is the quota guarantee
    the planner cut must preserve."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")  # mode defaults to long_lived
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing", last_plan_at=store.now_iso()))
    planner, evaluator, engine, notifier = FakeClaude(ACT_FEATURE), FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    assert out is Outcome.IDLE
    assert planner.calls == 0          # <-- the quota guardrail (no planner in the thin path)
    assert evaluator.calls == 0
    assert engine.dispatched == []


@pytest.mark.asyncio
async def test_thin_blocked_tick_stays_idle_zero_tokens(tmp_path):
    """A blocked thin goal unblocks only on work — never the timer. Zero tokens,
    no dispatch, same as the planner path's blocked steady-state."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(
        phase="blocked", lifecycle="executing", blocked_on="need a decision",
        blocked_kind="needs_answer",
    ))
    planner, evaluator, engine, notifier = FakeClaude(ACT_FEATURE), FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    assert out is Outcome.IDLE
    assert planner.calls == 0
    assert evaluator.calls == 0
    assert engine.dispatched == []


# ---- the advance dispatch (no planner) -------------------------------------


@pytest.mark.asyncio
async def test_thin_cadence_due_dispatches_advance_without_planner(tmp_path):
    """A due thin tick dispatches ONE 'advance the goal / maintain PLAN.md'
    implement_feature session — mechanically, with no planner call."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d", done_when="the /health endpoint returns 200")
    # last_plan_at unset → cadence is due.
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    planner, evaluator, engine, notifier = FakeClaude(ACT_FEATURE), FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    assert planner.calls == 0          # <-- no planner cognition on the thin path
    assert evaluator.calls == 0
    assert len(engine.dispatched) == 1
    action, _goal, _url = engine.dispatched[0]
    assert action.tool == "implement_feature"
    assert "Advance this goal" in action.goal
    assert "PLAN.md" in action.goal
    assert "the /health endpoint returns 200" in action.goal  # done_when rode into the brief


@pytest.mark.asyncio
async def test_thin_steering_dispatches_advance_and_rides_into_brief(tmp_path):
    """Steering makes work present even when the cadence isn't due; the thin path
    dispatches an advance session and the steering text rides into the brief for
    the worker to read (there is no planner to 'apply' it)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing", last_plan_at=store.now_iso()))
    store.append_steering("g", ["pause features, fix the failing CI first"])
    planner, evaluator, engine, notifier = FakeClaude(ACT_FEATURE), FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    assert planner.calls == 0
    assert len(engine.dispatched) == 1
    action, _goal, _url = engine.dispatched[0]
    assert "failing CI" in action.goal   # steering rode into the advance brief
    # steering was consumed (won't re-fire next tick)
    assert store.unread_steering_rows("g") == []


# ---- the done-trigger: a successful advance settle proposes done -----------


@pytest.mark.asyncio
async def test_thin_successful_settle_proposes_done_via_gate(tmp_path):
    """When an advance session settles successfully (devclaw's own
    ``status=done`` header, gate not FAILED), the thin path PROPOSES done — it
    opens the grounded done-gate (a review_repository dispatch) — with no planner
    call. The done-gate, not the worker's claim, is the authority (#358)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", done_when="the repo builds green")
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    planner = FakeClaude(ACT_FEATURE)
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="Agent: shipped the endpoint",
        pr_url="https://github.com/o/r/pull/7", gate_passed=True,
    ))
    notifier = RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    assert out is Outcome.VERIFYING          # the done-gate was opened
    assert planner.calls == 0                # <-- proposed done with no planner
    # the done-gate opens a read-only review of the repo
    assert any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)


@pytest.mark.asyncio
async def test_thin_failed_settle_does_not_propose_done(tmp_path):
    """A FAILED/gate-failed settle must NOT propose done — the thin path only
    triggers the (expensive) done-gate on a clean session-success header."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    planner = FakeClaude(ACT_FEATURE)
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed", detail="tests failed",
    ))
    notifier = RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    # No done-gate proposed off a failure; and still no planner cognition.
    assert planner.calls == 0
    assert evaluator.calls == 0
    assert not any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)


@pytest.mark.asyncio
async def test_thin_trigger_ignores_worker_free_text_not_the_header(tmp_path):
    """#358 trust boundary (invariant-guard, 2026-08-05): the done-trigger reads
    ONLY devclaw's controlled settle header (first line), never the worker's
    free-text narration. A FAILED settle whose worker detail merely CONTAINS the
    phrase 'status=done' must NOT propose done — the worker cannot flip the
    control-plane's decision by writing a magic string into its own summary."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    planner, evaluator = FakeClaude(ACT_FEATURE), FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed",
        detail="I could not finish, but I set status=done on ticket #3 and gate=passed locally",
    ))
    notifier = RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    # The failed header wins over the worker's free-text 'status=done'.
    assert out is not Outcome.VERIFYING
    assert not any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)


@pytest.mark.asyncio
async def test_thin_trigger_not_suppressed_by_worker_free_text_gate_failed(tmp_path):
    """The inverse crack: a genuinely SUCCESSFUL settle (header status=done, gate
    passed) whose worker detail happens to contain 'gate=FAILED' must STILL
    propose done — the free-text must not suppress a legitimate proposal."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", done_when="the repo builds green")
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    planner, evaluator = FakeClaude(ACT_FEATURE), FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", gate_passed=True,
        pr_url="https://github.com/o/r/pull/8",
        detail="fixed the flow that previously logged gate=FAILED in CI",
    ))
    notifier = RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier)

    assert out is Outcome.VERIFYING
    assert planner.calls == 0
    assert any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)


# ---- additive + gated: flag OFF is the unchanged planner path ---------------


@pytest.mark.asyncio
async def test_flag_off_still_runs_the_planner(tmp_path):
    """With the thin flag OFF (the default), a due long_lived tick runs the
    per-tick planner exactly as before — the change is additive and gated."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    planner, evaluator, engine, notifier = FakeClaude(ACT_FEATURE), FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", planner, evaluator, engine, notifier, thin=False)

    assert out is Outcome.DISPATCHED
    assert planner.calls == 1            # <-- planner still drives when the flag is off
