"""The THIN long_lived executing path (demolition P3b — the per-tick planner
is CUT; this is now the ONLY long_lived executing path).

A long_lived executing goal runs ZERO per-tick planner cognition: the tick
mechanically dispatches "advance the goal via speckit" worker sessions
and lets the grounded done-gate judge completion.

The load-bearing assertions (inheriting the old planner path's guardrail):
  * an idle / blocked tick spends ZERO tokens (the evaluator at calls == 0,
    nothing dispatched) — the Pro quota guarantee must survive the planner cut;
  * a due / steered tick dispatches an advance session mechanically;
  * a successful advance settle PROPOSES done (opens the grounded done-gate)
    with no cognition of its own.
"""

from __future__ import annotations

import pytest

from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


async def _thin_tick(store, goal_id, evaluator, engine, notifier, *, verify_done=True):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=verify_done, )


# ---- the guardrail: thin idle / blocked ticks are zero-token ---------------


@pytest.mark.asyncio
async def test_thin_idle_tick_spends_zero_tokens(tmp_path):
    """A long_lived thin tick with no work and a not-due cadence must plan
    nothing — no cognition, no dispatch. This is the quota guarantee the
    planner cut must preserve."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")  # mode defaults to long_lived
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing", last_plan_at=store.now_iso()))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.IDLE
    assert evaluator.calls == 0        # <-- the quota guardrail
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
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.IDLE
    assert evaluator.calls == 0
    assert engine.dispatched == []


# ---- the advance dispatch (no planner) -------------------------------------


@pytest.mark.asyncio
async def test_thin_cadence_due_dispatches_advance_without_planner(tmp_path):
    """A due thin tick dispatches ONE 'advance the goal via speckit'
    implement_feature session — mechanically, with no cognition call."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d", done_when="the /health endpoint returns 200")
    # last_plan_at unset → cadence is due.
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    assert evaluator.calls == 0        # <-- no cognition on the thin path
    assert len(engine.dispatched) == 1
    action, _goal, _url = engine.dispatched[0]
    assert action.tool == "implement_feature"
    assert "Advance this goal" in action.goal
    assert "tasks.md" in action.goal and "PLAN.md" not in action.goal  # spec 008 US1
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
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    assert evaluator.calls == 0
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
    opens the grounded done-gate (a review_repository dispatch) — with no
    cognition call of its own. The done-gate, not the worker's claim, is the
    authority (#358)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", done_when="the repo builds green")
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="Agent: shipped the endpoint",
        pr_url="https://github.com/o/r/pull/7", gate_passed=True,
    ))
    notifier = RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.VERIFYING          # the done-gate was opened
    assert evaluator.calls == 0              # <-- proposed done with no cognition
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
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed", detail="tests failed",
    ))
    notifier = RecordingNotifier()

    await _thin_tick(store, "g", evaluator, engine, notifier)

    # No done-gate proposed off a failure; and still zero cognition.
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
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed",
        detail="I could not finish, but I set status=done on ticket #3 and gate=passed locally",
    ))
    notifier = RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

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
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", gate_passed=True,
        pr_url="https://github.com/o/r/pull/8",
        detail="fixed the flow that previously logged gate=FAILED in CI",
    ))
    notifier = RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.VERIFYING
    assert evaluator.calls == 0
    assert any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)


# ─── spec 021: chunk-plan integrity + context-budget brief framing ───────────


@pytest.mark.asyncio
async def test_corrupt_chunk_plan_blocks_loud_with_zero_llm_calls(tmp_path):
    """FR-004: a continuation whose current feature's tasks.md cannot be read
    blocks mechanical:corrupt_doc with zero cognition and zero dispatch —
    never a session that silently re-plans over prior work."""
    ws = tmp_path / "ws-corrupt"
    (ws / "specs" / "001-f").mkdir(parents=True)
    (ws / "specs" / "001-f" / "tasks.md").write_bytes(b"\xff\xfe\x00 not utf-8")
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d", workspace_dir=str(ws))
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    store.append_delivery("g", "increment 1", "PR: https://x/1\n", ref_id="r1")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    assert evaluator.calls == 0          # zero-token guard holds on the block
    assert engine.dispatched == []
    status = store.load_status("g")
    assert status.phase == "blocked"
    assert status.blocked_kind == "mechanical:corrupt_doc"
    assert "tasks.md" in status.blocked_on


@pytest.mark.asyncio
async def test_first_increment_never_runs_the_chunk_plan_check(tmp_path):
    """The corrupt-artifact gate is continuation-only: a first dispatch with
    no settled increments must not consult the workspace at all (FR-004 scope;
    a fresh goal legitimately has no speckit tree yet)."""
    ws = tmp_path / "ws-fresh"
    (ws / "specs" / "001-f").mkdir(parents=True)
    (ws / "specs" / "001-f" / "tasks.md").write_bytes(b"\xff\xfe\x00 not utf-8")
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d", workspace_dir=str(ws))
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _thin_tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED     # no increments yet → gate not armed
    assert len(engine.dispatched) == 1


def test_oversized_slice_failure_context_demands_reslice(tmp_path):
    """FR-008: a runner-named oversized slice makes the next brief demand a
    re-slice of THAT slice — an identical re-attempt is refused by the brief."""
    from devclaw.goal.tick import _advance_brief

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    goal = store.load_goal("g")
    fc = (
        "Conversation run failed for id=x: Internal error: Prompt is too long "
        "[active_slice: 001-f US2] — the worker conversation overflowed"
    )
    brief = _advance_brief(goal, "", failure_context=fc)
    assert "re-slice that slice" in brief
    assert "Do not re-attempt the oversized slice unchanged" in brief
    assert "[active_slice: 001-f US2]" in brief


def test_continuation_brief_is_bounded_regardless_of_prior_chunk_count(tmp_path):
    """FR-003: the continuation brief's prior-increments section is tail-kept
    under a fixed cap, so worker input stays flat as the arc grows."""
    from devclaw.goal import prior_increments as pi
    from devclaw.goal.prompt_budget import PRIOR_INCREMENTS_KEEP
    from devclaw.goal.tick import _advance_brief

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    goal = store.load_goal("g")

    def rendered(n):
        recs = [
            pi.parse_record(
                f"increment {i}: do a thing in module {i}",
                f"PR: https://example.com/pr/{i}\nVerify gate `pytest -q`: PASSED\n",
                "done",
            )
            for i in range(n)
        ]
        return pi.render(recs)

    assert len(rendered(300)) <= PRIOR_INCREMENTS_KEEP + 1000
    brief_small = _advance_brief(goal, "", prior_increments=rendered(3))
    brief_large = _advance_brief(goal, "", prior_increments=rendered(300))
    assert len(brief_large) <= len(brief_small) + PRIOR_INCREMENTS_KEEP
