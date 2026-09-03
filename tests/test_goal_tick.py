"""The goal heartbeat — the load-bearing zero-token guardrail + the evaluation
state machine (in_flight → verifying → done). Folded from goalclaw, extended for
the direction evaluator + done-gate.

The single most important assertions in this layer: an idle tick and an
in-flight-still-running tick must leave the one cognition caller (the
evaluator) at calls == 0 and dispatch nothing. If that ever goes non-zero,
the Pro quota dies under N idle ticks/day.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from devclaw.goal.engine import GoalEngineError
from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_all, tick_goal
from devclaw.goal.tick_donegate import DONEGATE_ROUND_CAP
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal, seed_marker_repo,
)

ACT_FEATURE = json.dumps(
    {"decision": "act", "note": "feat", "actions": [{"tool": "implement_feature", "goal": "add /health", "open_pr": True}]}
)


@pytest.fixture(autouse=True)
def _no_real_deploys(monkeypatch):
    """Deploys are out of scope for tick tests, but every done-gate close runs
    the best-effort ``_auto_deploy`` (conditional by default since #554 —
    an app-surface workspace still deploys) — and it
    swallows ``Exception``, so on a docker-enabled host the achieved-verdict
    tests here silently launched a REAL ``devclaw-deploy-g`` container that
    outlived pytest (the 2026-07-14 leak; conftest's ``_block_real_docker``
    now fails such an escape loudly). Stub the seam module-wide with the same
    raiser shape individual tests already used; a test that wants to assert
    deploy behavior patches ``deploy_project`` itself."""
    from devclaw.delivery import deploy as deploy_mod

    async def _no_deploy(workspace_dir, slug):
        raise RuntimeError("no deploys under test")

    monkeypatch.setattr(deploy_mod, "deploy_project", _no_deploy)


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


async def _tick(store, goal_id, evaluator, engine, notifier, *, verify_done=True, summary_caller=None, remote_checker=None, mergeability_probe=None):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=verify_done, summary_caller=summary_caller,
        remote_checker=remote_checker,
        mergeability_probe=mergeability_probe,
    )


class RecordingProbe:
    """A fake mergeability probe (#394): records the PR urls it was asked
    about and returns a fixed verdict (True=CONFLICTING, False=mergeable,
    None=could-not-tell)."""

    def __init__(self, verdict=None):
        self.asked: list[str] = []
        self._verdict = verdict

    async def __call__(self, pr_url: str):
        self.asked.append(pr_url)
        return self._verdict


class RecordingSummarizer:
    """A fake plain-language summarizer caller: records prompts and returns a
    fixed plain rewrite, so tests can assert WHICH notifications get summarized."""

    def __init__(self, rewrite="PLAIN: here is what is happening"):
        self.prompts: list[str] = []
        self._rewrite = rewrite

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._rewrite


# ---- the guardrail ---------------------------------------------------------


@pytest.mark.asyncio
async def test_idle_tick_spends_zero_tokens(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.IDLE
    assert evaluator.calls == 0        # <-- evaluator must not fire on idle either
    assert engine.dispatched == []
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_in_flight_running_spends_zero_tokens(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(phase="in_flight", in_flight=InFlight("devclaw", "implement_feature", "t1", "task")),
    )
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(terminal=False, status="running"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.IN_FLIGHT
    assert evaluator.calls == 0
    assert engine.polls == 1


# ---- the working path ------------------------------------------------------


@pytest.mark.asyncio
async def test_first_tick_dispatches_advance_session(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")  # no STATUS yet → cadence due → dispatch an advance
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    assert evaluator.calls == 0        # mechanical dispatch — zero cognition
    assert len(engine.dispatched) == 1
    action, goal, notify_url = engine.dispatched[0]
    assert action.tool == "implement_feature"
    # spec 008 US1: the thin advance brief runs the speckit flow, not PLAN.md.
    assert "Advance this goal" in action.goal and "tasks.md" in action.goal
    assert "PLAN.md" not in action.goal
    assert notify_url == "http://relay"
    saved = store.load_status("g")
    assert saved.phase == "in_flight" and saved.in_flight is not None


@pytest.mark.asyncio
async def test_one_shot_mode_rides_the_advance_path(tmp_path):
    """mode="one_shot" converged onto the SAME execution path as long_lived
    (spec 008 shrink): its tick dispatches the thin ADVANCE brief — no
    checklist/decompose machinery is consulted (there is none left)."""
    from devclaw.advance_brief import ADVANCE_BRIEF_MARKER

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", mode="one_shot")  # no STATUS yet → cadence due
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    assert evaluator.calls == 0
    assert len(engine.dispatched) == 1
    action, _, _ = engine.dispatched[0]
    assert action.goal.strip().startswith(ADVANCE_BRIEF_MARKER)  # the advance brief, one path


@pytest.mark.asyncio
async def test_workspace_prepped_before_dispatch(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()
    calls: list = []

    async def rec_prepare(ws, repo_url=None, branch=None):
        calls.append((ws, repo_url, branch))
        return branch or "main"

    out = await tick_goal(
        "g", store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="", prepare_ws=rec_prepare,
    )
    assert out is Outcome.DISPATCHED
    # seed_goal now sets a fake repo_url so the investigating phase takes the
    # repo-research path (vs world-research, which fires for from-scratch only).
    # every goal is goal-branch (#616 retired the per-action selection rule)
    assert calls == [("/repos/demo", "https://example.com/demo.git", "goal/g")]
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_finished_action_records_delivery_and_proposes_done(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(phase="in_flight", in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health")),
    )
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="Agent summary: added /health",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    # A successful settle proposes done: the done-gate review is dispatched.
    assert out is Outcome.VERIFYING
    assert any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)
    # grounded delivery captured + PR logged
    assert "added /health" in store.recent_deliveries("g")
    assert "PR https://github.com/o/r/pull/9" in store.recent_log("g")
    # Honest-wording contract (closeloop-bench 2026-07-05): the gate is named
    # as the SANDBOX gate (not CI) in the durable log.
    assert "sandbox gate=passed" in store.recent_log("g")


@pytest.mark.asyncio
async def test_steering_triggers_advance_even_when_cadence_not_due(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    # Steering enters through the verb, never by hand-editing inbox.md (#617).
    store.append_steering("g", ["pause features, fix the failing CI first"], source="denys")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert "failing CI" in action.goal     # steering rode into the advance brief
    assert store.unread_steering_rows("g") == []   # consumed with the dispatch


# ---- blocked ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_blocked_goal_stays_idle_without_steering(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1h")
    store.save_status("g", GoalStatus(phase="blocked", blocked_on="which DB?", last_plan_at="2026-06-01T00:00:00+00:00"))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.IDLE
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_donegate_churn_park_survives_auto_eval_steering(tmp_path):
    """Named regression (2026-08-25, devclaw-pr-authorship-2026-08-24 night):
    the churn brake parks a goal AND appends its corrections as steering
    (source auto-eval) — and the blocked gate counted ANY unread steering as
    unblock-work, so the brake un-parked itself within one tick, three times
    in one night, while the owner slept. Machine steering must never unblock
    a human-gated park: the goal stays blocked, zero cognition, rows unread."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="blocked", blocked_kind="donegate_churn",
        blocked_on="done-gate churn brake: parked",
        donegate_rounds=DONEGATE_ROUND_CAP,
    ))
    store.append_steering("g", ["[clause 1] fix the skip mechanism"], source="auto-eval")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.IDLE
    s = store.load_status("g")
    assert s.phase == "blocked"
    assert s.blocked_kind == "donegate_churn"
    assert evaluator.calls == 0
    assert engine.dispatched == []
    # the corrections stay parked with the goal, unconsumed
    assert "[clause 1] fix the skip mechanism" in store.unread_steering("g")


@pytest.mark.asyncio
async def test_blocked_goal_unblocks_on_human_steering_and_consumes_parked_corrections(tmp_path):
    """The human half of the contract: owner steering unblocks the parked
    goal, and the machine corrections parked with it ride the SAME dispatch —
    both rows consumed, both in the brief, so nothing the gate recorded for
    the owner's decision is lost."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="blocked", blocked_kind="donegate_churn",
        blocked_on="done-gate churn brake: parked",
        donegate_rounds=DONEGATE_ROUND_CAP,
    ))
    store.append_steering("g", ["[clause 1] fix the skip mechanism"], source="auto-eval")
    store.append_steering("g", ["reviewed the PR — take the conftest approach"], source="denys")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert "fix the skip mechanism" in action.goal
    assert "take the conftest approach" in action.goal
    assert store.unread_steering_rows("g") == []


@pytest.mark.asyncio
async def test_idle_goal_still_advances_on_auto_eval_corrections(tmp_path):
    """The ralph-loop pin: below the churn cap the done-gate returns the goal
    to IDLE with its corrections as auto-eval steering, and the next tick must
    advance on them — the human-gating above applies ONLY to blocked goals."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    store.append_steering("g", ["[clause 2] add the parity test"], source="auto-eval")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert "add the parity test" in action.goal
    assert store.unread_steering_rows("g") == []


@pytest.mark.asyncio
async def test_dispatch_cap_blocks_runaway(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")  # backlog 2 → cap = 4
    store.save_status("g", GoalStatus(phase="idle", actions_dispatched=4))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    assert engine.dispatched == []
    assert any("cap" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_verified_delivery_refunds_dispatch_cap(tmp_path):
    """A dispatch that settles done + gate-passed hands its cap budget back —
    the cap measures outstanding unproductive dispatches, not lifetime
    throughput, so an auto-merging mission goal never blocks on healthy work
    (live-found 2026-07-07: closeloop-mission-v2 blocked at cap 6 while
    shipping real merged PRs)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=4,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="added /health",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))

    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert store.load_status("g").actions_dispatched == 3


@pytest.mark.asyncio
async def test_gateless_successful_settle_refunds_dispatch_cap(tmp_path):
    """A gateless settle that succeeds (review, program) also refunds — a
    mission goal that verifies every delivery with a read-only review was
    structurally re-tripping the cap on healthy on_track work (live-found
    2026-07-09, closeloop-mission-v2). Only failures and gate-FAILED work
    accumulate."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=4,
            in_flight=InFlight("devclaw", "review_repository", "t1", "task", "verify the delivery"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="repo analysis",
    ))

    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert store.load_status("g").actions_dispatched == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "poll",
    [
        # failed run — no refund
        PollResult(terminal=True, status="failed", detail="agent died"),
        # done but gate FAILED — unverified, no refund
        PollResult(terminal=True, status="done", detail="broke tests",
                   pr_url="https://github.com/o/r/pull/9", gate_passed=False),
        # a worker block that landed NOTHING (empty or undeterminable span, so
        # engine._landed_partial reads False) — there is nothing for the next
        # session to continue from, so it still burns its dispatch and the cap
        # still catches it. The refund narrows the brake; it never weakens it.
        PollResult(terminal=True, status="failed",
                   detail="worker reported BLOCKED: missing credential",
                   landed_partial=False),
    ],
)
async def test_unproductive_settle_keeps_dispatch_count(tmp_path, poll):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=4,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
        ),
    )
    engine = FakeEngine(poll_result=poll)

    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert store.load_status("g").actions_dispatched == 4


@pytest.mark.asyncio
async def test_landed_partial_refunds_the_cap_but_leaves_the_watchdog_armed(tmp_path):
    """The refund must not become a licence to loop.

    A landed partial publishes no PR and may have committed only re-planning,
    so it is NOT a delivered increment: ``last_progress_at`` is left untouched
    and the no-progress watchdog stays armed. That is what makes refunding the
    dispatch cap safe without minting a second counter — a goal that lands
    forever without ever shipping still trips the existing brake."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")  # backlog 2 → cap 4
    stale = "2026-01-01T00:00:00+00:00"
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=4, last_progress_at=stale,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "spec 030"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed",
        detail="worker reported BLOCKED: context budget exhausted",
        landed_partial=True,
    ))

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    # AT the cap the refund is what keeps the goal moving: it settles, hands
    # the budget back, and dispatches the continuation on this same tick
    # instead of parking at mechanical:dispatch_cap. Without the refund this
    # is Outcome.BLOCKED — the live devclaw-030 failure.
    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1
    after = store.load_status("g")
    assert after.blocked_kind != "mechanical:dispatch_cap"
    # ...but it did not DELIVER: the watchdog is left armed, so a goal that
    # lands forever without ever shipping still trips the existing brake.
    assert after.last_progress_at == stale


@pytest.mark.asyncio
async def test_partial_feeds_forward_as_continue_from_the_branch(tmp_path):
    """The sentence the next worker reasons from.

    Under goal-branch delivery a partial's commits are in the next worker's
    working tree, so the failed-entry wording ("its work is not in the tree")
    must never render for one — that lie is what made worker N+1 re-plan,
    re-tripwire, and burn the cap without writing code."""
    from devclaw.advance_brief import PRIOR_INCREMENTS_MARKER

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.record_settlement("g", ref_id="t1", ref_kind="task", status="partial")
    store.append_delivery(
        "g", "implement spec 030 US1",
        "Error:\nworker reported BLOCKED: context budget exhausted\n",
        ref_id="t1",
    )
    store.save_status("g", GoalStatus(phase="idle"))
    store.append_steering("g", ["keep going"], source="owner")
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert PRIOR_INCREMENTS_MARKER in action.goal
    assert "status=partial" in action.goal
    assert "CONTINUE from them" in action.goal
    # the load-bearing absence: the failed-entry wording must not apply here
    assert "status=partial DID land" in action.goal


@pytest.mark.asyncio
async def test_refund_never_goes_negative(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=0,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="added /health",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))

    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert store.load_status("g").actions_dispatched == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "detail",
    [
        "toolchain_provision_failed: mise install failed (exit 1)",
        "clone failed: fatal: repository not found",
        "could not prepare target_branch 'feat/x': git clean -fdx failed: permission denied",
    ],
)
async def test_in_task_setup_failure_blocks_mechanical_prep_instead_of_redispatching(tmp_path, detail):
    """#379: a mechanical SETUP failure inside a dispatched task (toolchain not
    provisioned, git clone/clean failure, target-branch prep failure) is something
    the worker CANNOT fix by re-running the same instruction. It must block the
    goal on the DAMPED ``mechanical:prep`` breaker (auto-heal budget + backoff),
    NOT re-dispatch a fresh sandbox with the same doomed instruction — the
    finance-sentry-ui goal re-hit the identical failure 119× before this fix. Zero
    cognition: the goal blocks on the failed settle before any re-dispatch."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=1,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
        ),
    )
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="failed", detail=detail))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    saved = store.load_status("g")
    assert saved.phase == "blocked"
    assert saved.blocked_kind == "mechanical:prep"   # the damped, auto-healing breaker
    assert engine.dispatched == []                    # NO amnesiac re-dispatch storm
    assert notifier.sent                               # owner pinged once


def test_steer_goal_resets_dispatch_counter_on_blocked(tmp_path):
    """steer_goal must zero actions_dispatched when unblocking so the dispatch
    cap doesn't re-fire on the very next tick after the human resolves the block."""
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    seed_goal(goals_dir, "g")

    db = StateStore(str(tmp_path / "state.db"))
    try:
        cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
        svc = GoalService(TaskQueue(db), db, config=cfg)
        # Seed + read back through the service's OWN store: since Tranche 1/PR3
        # status lives in the shared StateStore (not STATUS.md), so a separate
        # GoalStore over the same goals_dir would read its own private DB, not
        # the state the service mutated. (Matches test_cancel_goal.py.)
        svc._goal_store.save_status(
            "g", GoalStatus(phase="blocked", blocked_on="cap hit", actions_dispatched=5)
        )

        svc.steer_goal("g", "resume with new approach")

        saved = svc._goal_store.load_status("g")
        assert saved.phase == "idle"
        assert saved.actions_dispatched == 0
    finally:
        db.close()


def test_steer_goal_clears_blocked_on(tmp_path):
    """steer_goal must clear blocked_on when unblocking — a HUMAN answering the
    question resolves it, so get_goal/list_goals/the console must stop showing
    the stale question as live (it fooled two readers on 2026-07-29). resume_goal
    already clears it; steer_goal used to leak it because the store only
    normalizes blocked_kind on a non-blocked write, not blocked_on."""
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    seed_goal(goals_dir, "g")

    db = StateStore(str(tmp_path / "state.db"))
    try:
        cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
        svc = GoalService(TaskQueue(db), db, config=cfg)
        svc._goal_store.save_status(
            "g",
            GoalStatus(
                phase="blocked",
                blocked_on="PR #9 open and unmerged — please merge or close",
                blocked_kind="needs_answer",
                actions_dispatched=5,
            ),
        )

        svc.steer_goal("g", "merged it — carry on")

        saved = svc._goal_store.load_status("g")
        assert saved.phase == "idle"
        assert not saved.blocked_on   # stale question no longer shown as live
        assert not saved.blocked_kind  # store normalizes this on non-blocked write
    finally:
        db.close()


# ---- resume_goal (the recovery verb — F7) -----------------------------------


def _resume_service(tmp_path):
    """A GoalService over the shared StateStore for resume_goal tests — mirrors
    test_steer_goal_resets_dispatch_counter_on_blocked's construction (status
    lives in the shared DB since Tranche 1, so tests must read back through the
    service's OWN store)."""
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    db = StateStore(str(tmp_path / "state.db"))
    cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
    return GoalService(TaskQueue(db), db, config=cfg), db, goals_dir


@pytest.mark.asyncio
async def test_resume_goal_unblocks_without_steering_and_replans_next_tick(tmp_path):
    """resume_goal is the recovery verb: it must fire UNBLOCK without appending
    a goal_steering row (a pure "blocker cleared" must never become a
    direction override — that was the F7 gap with steer_goal-as-only-unstick)
    AND guarantee an advance on the very next tick even with a fresh
    last_plan_at + a long cadence — a bare UNBLOCK would park the goal until
    cadence (should_plan = work OR cadence_due, and resume adds no work)."""
    svc, db, goals_dir = _resume_service(tmp_path)
    try:
        seed_goal(goals_dir, "g", cadence="30d")
        store = svc._goal_store
        store.save_status("g", GoalStatus(
            phase="blocked", blocked_on="sandbox image missing",
            actions_dispatched=5, last_plan_at=store.now_iso(),
        ))

        out = svc.resume_goal("g")

        assert out["resumed"] is True
        assert out["was_blocked_on"] == "sandbox image missing"
        saved = store.load_status("g")
        assert saved.phase == "idle"
        assert not saved.blocked_on                    # stale block reason cleared
        assert saved.actions_dispatched == 0           # cap won't re-fire on the first re-plan
        assert saved.last_plan_at is None              # cadence_due → True on the next tick
        assert store.unread_steering_rows("g") == []   # NO steering row appended

        evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()
        tick_out = await _tick(store, "g", evaluator, engine, notifier)

        assert tick_out is Outcome.DISPATCHED          # advanced despite fresh last_plan_at + 30d cadence
        action, _, _ = engine.dispatched[0]
        assert "Steering from the owner" not in action.goal   # no steering row → clean advance brief
    finally:
        db.close()


def test_resume_goal_noops_legibly_when_not_blocked(tmp_path):
    """No UNBLOCK edge exists from non-blocked states — resume_goal on an idle
    goal must no-op with a legible message, never raise IllegalTransition, and
    leave the status untouched (idempotency: a second resume is a no-op)."""
    svc, db, goals_dir = _resume_service(tmp_path)
    try:
        seed_goal(goals_dir, "g")
        svc._goal_store.save_status("g", GoalStatus(phase="idle", actions_dispatched=2))

        out = svc.resume_goal("g")

        assert out["resumed"] is False
        assert "not blocked" in out["message"]
        saved = svc._goal_store.load_status("g")
        assert saved.phase == "idle"
        assert saved.actions_dispatched == 2           # untouched — a true no-op
        assert svc._goal_store.unread_steering_rows("g") == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_blocked_goal_costs_zero_cognition_until_resume_goal(tmp_path):
    """The zero-token guard holds around the new verb: a blocked goal costs 0
    cognition calls and 0 dispatches tick after tick, and only resume_goal
    reopens the advance path."""
    svc, db, goals_dir = _resume_service(tmp_path)
    try:
        seed_goal(goals_dir, "g", cadence="30d")
        store = svc._goal_store
        store.save_status("g", GoalStatus(
            phase="blocked", blocked_on="cap hit", last_plan_at=store.now_iso(),
        ))
        evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

        for _ in range(3):
            out = await _tick(store, "g", evaluator, engine, notifier)
            assert out is Outcome.IDLE
        assert evaluator.calls == 0 and engine.dispatched == []   # blocked = 0 tokens

        svc.resume_goal("g")
        out = await _tick(store, "g", evaluator, engine, notifier)

        assert out is Outcome.DISPATCHED
        assert len(engine.dispatched) == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_done_goal_is_skipped(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="done"))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.SKIP_DONE
    assert evaluator.calls == 0


# ---- the done-gate ("done" is only a proposal) -----------------------------


@pytest.mark.asyncio
async def test_done_gate_review_achieved_closes_goal(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "achieved",
        "rationale": "/health exists and is tested",
        "clauses": [
            {
                "clause": "/health returns 200",
                "satisfied": True,
                "evidence": "src/Health.cs:12 returns OK; HealthTests.cs:8 asserts 200",
            },
            {
                "clause": "/health is tested",
                "satisfied": True,
                "evidence": "HealthTests.cs:8 Health_Returns200",
            },
        ],
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="repo has /health + test"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DONE
    assert evaluator.calls == 1
    assert "repo has /health" in evaluator.last_prompt   # review report fed in
    assert store.load_status("g").phase == "done"
    assert any("complete (verified)" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_done_gate_review_off_track_steers_and_continues(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "off_track", "rationale": "/health is not tested",
        "corrections": ["add a test for /health"],
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="no test found"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.SLEPT             # not done
    s = store.load_status("g")
    assert s.phase == "idle"
    # the correction was steered back in for the next plan
    assert "add a test for /health" in store.unread_steering("g")
    # companion to #430: with no unmerged-PR marker, keep-going is UNCHANGED —
    # the block below must never false-fire on an ordinary off_track.


@pytest.mark.asyncio
async def test_interrupted_done_gate_close_reopens_cadence_next_tick(tmp_path):
    """#784 (pause-and-resume brake): a close resolution that dies between the
    done-check verdict and the evaluator's verdict must not strand the goal for
    a full re-plan cadence. DONE_GATE_SETTLED clears ``last_plan_at`` before
    the evaluator runs, so whatever kills the evaluator mid-flight (quota
    pause, OOM, restart), the next non-paused tick acts immediately."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(
        phase="verifying",
        last_plan_at=store.now_iso(),  # cadence freshly stamped by the propose
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))

    class AbortingClaude(FakeClaude):
        async def __call__(self, prompt: str) -> str:
            await super().__call__(prompt)
            raise RuntimeError("Internal error: You're out of extra usage (rate_limit)")

    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="looks done"))
    notifier = RecordingNotifier()

    try:
        await _tick(store, "g", AbortingClaude(), engine, notifier)
    except Exception:
        pass  # a raw quota abort propagates to the sweep wrapper in prod

    s = store.load_status("g")
    assert s.phase == "idle" and s.in_flight is None
    # THE invariant: the settle reopened the cadence before the evaluator ran,
    # so the aborted resolution cannot strand the goal until cadence elapses.
    assert s.last_plan_at is None

    # Next tick with cognition healthy: the goal ACTS (re-derives the close)
    # instead of sleeping out the 1d cadence.
    healthy = FakeClaude(json.dumps({"verdict": "off_track", "rationale": "r", "corrections": ["c"]}))
    out = await _tick(store, "g", healthy, engine, notifier)
    assert out is not Outcome.IDLE


@pytest.mark.asyncio
async def test_done_gate_disabled_uses_artifact_eval(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # A settled advance session is what proposes done on the thin path.
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "achieved",
        "rationale": "deliveries show done_when met",
        "clauses": [
            {
                "clause": "/health returns 200",
                "satisfied": True,
                "evidence": "PR #1 added src/Health.cs:12 + HealthTests.cs:8",
            },
            {
                "clause": "/health is tested",
                "satisfied": True,
                "evidence": "HealthTests.cs:8 Health_Returns200 passing",
            },
        ],
    }))
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="shipped", gate_passed=True,
    ))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, verify_done=False)

    assert out is Outcome.DONE
    assert evaluator.calls == 1
    assert engine.dispatched == []          # no review run when verification disabled
    assert store.load_status("g").phase == "done"


@pytest.mark.asyncio
async def test_done_gate_prompt_carries_repo_context_on_artifact_only_path(tmp_path, monkeypatch):
    """verify_done=False runs the SAME terminal close with review_report="" —
    the evaluator prompt used to contain ZERO first-hand repo facts, and the
    owner was told "(verified)" anyway. Now the prompt carries a grounded
    snapshot of the ACTUAL workspace and the notification says what really
    happened (triage F3, the evaluator sibling of #227)."""
    from devclaw.goal import tick as tick_mod

    async def _no_deploy(workspace_dir, slug):
        raise RuntimeError("no deploys under test")

    monkeypatch.setattr(tick_mod._deploy, "deploy_project", _no_deploy)
    repo = seed_marker_repo(tmp_path)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", workspace_dir=str(repo))
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "achieved",
        "rationale": "deliveries show done_when met",
        "clauses": [
            {"clause": "/health returns 200", "satisfied": True,
             "evidence": "PR #1 added src/Health.cs:12 + HealthTests.cs:8"},
            {"clause": "/health is tested", "satisfied": True,
             "evidence": "HealthTests.cs:8 Health_Returns200 passing"},
        ],
    }))
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="shipped", gate_passed=True,
    ))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, verify_done=False)

    assert out is Outcome.DONE
    prompt = evaluator.last_prompt
    assert "## Repository context" in prompt
    assert "global.json: file" in prompt          # a real probe line from the ACTUAL workspace
    assert "pyproject.toml: missing" in prompt    # ...which is not the control-plane repo
    done_msgs = [m for m in notifier.sent if "goal complete" in m]
    assert done_msgs, notifier.sent
    assert all("artifact-only close" in m for m in done_msgs)
    assert not any("(verified)" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_done_gate_verified_wording_kept_when_review_grounded(tmp_path, monkeypatch):
    """The DEFAULT done-gate is untouched: when a real repo review grounded the
    close, the owner notification still says "(verified)" — the honest-labeling
    fix only relabels the artifact-only fallthrough."""
    from devclaw.goal import tick as tick_mod

    async def _no_deploy(workspace_dir, slug):
        raise RuntimeError("no deploys under test")

    monkeypatch.setattr(tick_mod._deploy, "deploy_project", _no_deploy)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "achieved",
        "rationale": "/health exists and is tested",
        "clauses": [
            {"clause": "/health returns 200", "satisfied": True,
             "evidence": "src/Health.cs:12 returns OK; HealthTests.cs:8 asserts 200"},
            {"clause": "/health is tested", "satisfied": True,
             "evidence": "HealthTests.cs:8 Health_Returns200"},
        ],
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="repo has /health + test"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DONE
    assert any("goal complete (verified)" in m for m in notifier.sent)
    assert not any("artifact-only" in m for m in notifier.sent)


# ---- periodic direction evaluation: CUT (demolition P1) --------------------
# docs/proposals/cognition-demolition.md — the per-tick mid-flight direction
# evaluator (`_run_mid_flight_eval`) is removed. Direction is no longer re-judged
# by an LLM on a delivery cadence; the mechanical brakes stand (no-progress
# watchdog, done-gate, per-item circuit breaker). The old fires-and-steers /
# carries-repo-context / stalled-blocks tests are replaced by the contract below.


@pytest.mark.asyncio
async def test_midflight_eval_cut_no_evaluator_call_and_never_blocks(tmp_path):
    """Demolition P1 regression: a long_lived executing goal that reaches the old
    eval cadence makes ZERO mid-flight evaluator calls and is NEVER blocked by a
    direction verdict — even when an evaluator wired to return the strongest old
    trigger (`stalled`) is present. The goal keeps its momentum (the settled
    session proposes done, opening the done-gate review); the only surviving
    direction cognition is the done-gate itself, and it runs only when that
    review comes back.
    """
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # Under the old code a goal at the eval cadence fired the mid-flight
    # eval; now it must not.
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
    ))
    # A `stalled` verdict WOULD have blocked the goal under the old mid-flight eval.
    evaluator = FakeClaude(json.dumps({"verdict": "stalled", "rationale": "no real progress"}))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="shipped"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert evaluator.calls == 0                       # the mid-flight cognition boundary is gone
    assert out is not Outcome.BLOCKED                 # a direction verdict can no longer block mid-flight
    assert store.load_status("g").phase != "blocked"
    # momentum: the settled session proposed done — the done-gate review is out
    assert any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)


# ---- plain-language summarizer (owner messages rewritten; best-effort) ------


@pytest.mark.asyncio
async def test_owner_notification_is_plain_summarized(tmp_path, monkeypatch):
    """An OWNER-level message (a blocker — here a workspace-prep failure) is
    rewritten by the summarizer before it reaches the notifier; the owner sees
    the plain text, not the raw line."""
    monkeypatch.delenv("DEVCLAW_NOTIFY_ALTITUDE", raising=False)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()
    summarizer = RecordingSummarizer("🟡 The repo could not be cloned; I paused the goal for you.")

    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=_failing_prepare,
        summary_caller=summarizer,
    )

    assert out is Outcome.BLOCKED
    assert len(summarizer.prompts) == 1                       # summarizer ran once
    assert "Repository not found" in summarizer.prompts[0]    # raw line fed in
    assert notifier.sent == ["🟡 The repo could not be cloned; I paused the goal for you."]  # plain text sent


@pytest.mark.asyncio
async def test_summarizer_not_invoked_for_suppressed_task_dispatch(tmp_path, monkeypatch):
    """A per-task dispatch is suppressed at the default floor — the summarizer
    must not be called for it (no wasted tokens on a message nobody sees)."""
    monkeypatch.delenv("DEVCLAW_NOTIFY_ALTITUDE", raising=False)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()
    summarizer = RecordingSummarizer()

    out = await _tick(store, "g", evaluator, engine, notifier, summary_caller=summarizer)

    assert out is Outcome.DISPATCHED
    assert summarizer.prompts == []          # never summarized a suppressed message
    assert notifier.sent == []


@pytest.mark.asyncio
async def test_idle_tick_never_invokes_summarizer(tmp_path, monkeypatch):
    """The zero-token guardrail extends to the summarizer: an idle tick must not
    call it."""
    monkeypatch.delenv("DEVCLAW_NOTIFY_ALTITUDE", raising=False)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()
    summarizer = RecordingSummarizer()

    out = await _tick(store, "g", evaluator, engine, notifier, summary_caller=summarizer)

    assert out is Outcome.IDLE
    assert summarizer.prompts == []
    assert evaluator.calls == 0


# ---- auto-merge on gate-green (hands-off; gated + best-effort) --------------


def _delivery_status():
    return GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "add /health"),
    )


# ---- SDLC pipeline P2: milestone-keyed mega-dump guardrail ------------------


def _long_lived_executing_status():
    """A long_lived goal mid-flight on an advance task — resolves to the
    goal-branch topology (accumulating PLAN.md), the only topology the slice
    guardrail reasons about."""
    return GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    )


@pytest.mark.asyncio
async def test_slice_guardrail_advises_under_trust_and_ships(tmp_path, monkeypatch):
    """A >1-story-slice mega-dump under the default `trust` dial ADVISES: a loud
    goal-log line surfaces the finding, but the increment still ships (the
    done-gate + human merge are the backstop) — never a hard block. Zero-token."""
    monkeypatch.setattr("devclaw.goal.slice_guard.tasks_flips_sync", lambda ws: 3)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", mode="long_lived")
    store.save_status("g", _long_lived_executing_status())
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="built ahead",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())

    assert out is not Outcome.BLOCKED  # advise-and-ship, not wedged
    assert evaluator.calls == 0  # detection is pure git+string — zero cognition
    log = (tmp_path / "g" / "log.md").read_text()
    assert "slice guardrail" in log and "advanced 3 story-slices" in log
    assert "shipped anyway (trust mode)" in log


@pytest.mark.asyncio
async def test_slice_guardrail_blocks_under_strict_for_a_reslice(tmp_path, monkeypatch):
    """Under the `strict` dial the SAME mega-dump BLOCKS the goal for a re-slice —
    an owner ping + a legible reason, no silent ship. Zero-token."""
    monkeypatch.setattr("devclaw.goal.slice_guard.tasks_flips_sync", lambda ws: 2)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", mode="long_lived")
    store.set_strictness("g", "strict")
    store.save_status("g", _long_lived_executing_status())
    evaluator = FakeClaude()
    notifier = RecordingNotifier()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="built ahead",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    assert evaluator.calls == 0
    assert store.load_status("g").phase == "blocked"
    assert any("slice guardrail" in m and "re-slice" in m.lower() for m in notifier.sent)
    log = (tmp_path / "g" / "log.md").read_text()
    assert "Parked for a re-slice (strict mode)" in log


@pytest.mark.asyncio
async def test_slice_guardrail_fails_open_when_detection_finds_no_megadump(tmp_path, monkeypatch):
    """DETECTION fails OPEN: an unparseable / clean tasks.md reads as ≤1 flip, so
    even under `strict` the guardrail never trips — a detection blind spot must
    never wedge a legitimately-sliced increment."""
    monkeypatch.setattr("devclaw.goal.slice_guard.tasks_flips_sync", lambda ws: 1)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", mode="long_lived")
    store.set_strictness("g", "strict")
    store.save_status("g", _long_lived_executing_status())
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="one clean milestone",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is not Outcome.BLOCKED
    assert store.load_status("g").phase != "blocked"
    assert "slice guardrail" not in (tmp_path / "g" / "log.md").read_text()


# ---- speckit contract enforcement at the DISPATCH boundary (issue #679) -----
# These three tests cover the gate added to tick_dispatch._dispatch_action:
# (a) no graded spec → held, (b) single feature → allowed, (c) 3 features → held.
# All use monkeypatch on speckit_feature_state_sync (zero-token pure-fs) so no
# real workspace is needed. The goal status must be idle (not in-flight) so the
# tick reaches the dispatch path.


@pytest.mark.asyncio
async def test_dispatch_held_without_graded_spec(tmp_path, monkeypatch):
    """(a) Spec dirs exist but none are graded (no tasks.md) — dispatch is held
    on the next tick. Named regression for issue #679: the gate was missing and
    the goal would dispatch into an unplanned session."""
    # total=1 spec dir, graded=0 (no tasks.md), active=0
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_feature_state_sync", lambda ws: (1, 0, 0)
    )
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator = FakeClaude()
    engine = FakeEngine()

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())

    assert out is Outcome.SLEPT          # held, not dispatched
    assert engine.dispatched == []       # no worker launched
    assert evaluator.calls == 0          # zero-token enforcement
    log = (tmp_path / "g" / "log.md").read_text()
    assert "no tasks.md" in log or "not graded" in log or "spec dir(s) present but none graded" in log


@pytest.mark.asyncio
async def test_dispatch_proceeds_with_single_graded_active_feature(tmp_path, monkeypatch):
    """(b) Exactly one feature with pending tasks — the single-feature happy path.
    Gate must not block a legitimately-sliced goal."""
    # total=1, graded=1, active=1
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_feature_state_sync", lambda ws: (1, 1, 1)
    )
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_dispatch_held_with_concurrent_build_ahead(tmp_path, monkeypatch):
    """(c) Genuine concurrent build-ahead is rejected — named regression for
    issue #679 (updated by #728 denominator fix). The guard holds only when
    speckit_offending_dirs_sync returns dirs, meaning 2+ features were advanced
    in the same session (same mtime window, not historical leftovers)."""
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_feature_state_sync", lambda ws: (3, 3, 3)
    )
    # Simulate two genuinely concurrent (same-session) active features beyond
    # the current one — the build-ahead signal the scoped guard still catches.
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_offending_dirs_sync",
        lambda ws, cur: ["specs/002-b", "specs/003-c"],
    )
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator = FakeClaude()
    engine = FakeEngine()

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())

    assert out is Outcome.SLEPT          # held, not dispatched
    assert engine.dispatched == []
    assert evaluator.calls == 0
    log = (tmp_path / "g" / "log.md").read_text()
    assert "specs/002-b" in log or "pending tasks" in log


@pytest.mark.asyncio
async def test_dispatch_proceeds_with_historical_active_features(tmp_path, monkeypatch):
    """Named regression for issue #728 (finance-sentry workspace shape):
    40 dirs total, 32 graded, 5 with unchecked tasks — all historical.
    The scoped guard finds no offending dirs (historical mtimes) so dispatch
    PROCEEDS instead of being permanently held.

    Fixture shape matches the live finance-sentry workspace measured 2026-08-28:
    total=40, graded=32, active=5 (001-bank-account-sync, 011-connect-providers,
    021-market-regime, 037-structured-data-sources, 039-ips-risk-boundary)."""
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_feature_state_sync", lambda ws: (40, 32, 5)
    )
    # speckit_offending_dirs_sync is NOT patched: the tmp_path has no specs/ dir,
    # so current_feature_dir_sync returns "" → speckit_offending_dirs_sync
    # returns [] (no baseline, all dirs treated as historical) → gate allows.
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED     # historical features must not block
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_dispatch_proceeds_with_real_finance_sentry_fixture(tmp_path):
    """Named regression for issue #728 — real on-disk fixture, no guard mocks.

    Constructs a workspace shaped exactly like the live finance-sentry measurement
    (2026-08-28): 40 feature dirs, 32 with all tasks.md rows checked, 5 with
    unchecked rows and mtimes older than the current in-flight feature dir (the
    historical offenders), plus 1 current in-flight dir with the NEWEST mtime and
    2 ungraded dirs (spec.md only, no tasks.md).

    Unlike test_dispatch_proceeds_with_historical_active_features (which patches
    speckit_feature_state_sync directly and uses a no-specs workspace), this test
    drives the real guard functions against the on-disk structure so the mtime-
    scoping logic in speckit_offending_dirs_sync is proven end-to-end. Dispatch
    must proceed: DISPATCHED, not SLEPT."""
    import os as _os

    _CHECKED_TASKS = (
        "# Tasks\n"
        "- [x] T001 [US1] scaffold the module\n"
        "- [x] T002 [US1] wire the endpoint\n"
        "- [x] T003 [US2] add the second story\n"
    )
    _PENDING_TASKS = (
        "# Tasks\n"
        "- [ ] T001 [US1] scaffold the module\n"
        "- [x] T002 [US1] wire the endpoint\n"
    )

    workspace = tmp_path / "workspace"
    specs = workspace / "specs"

    # 32 completed feature dirs — all tasks checked, very old mtime
    for i in range(1, 33):
        feat = specs / f"{i:03d}-completed-{i}"
        feat.mkdir(parents=True)
        (feat / "tasks.md").write_text(_CHECKED_TASKS)
        _os.utime(feat / "tasks.md", (1_000, 1_000))

    # 5 historical active dirs — unchecked tasks, OLD mtime
    historical = [
        "001-bank-account-sync",
        "011-connect-providers",
        "021-market-regime",
        "037-structured-data-sources",
        "039-ips-risk-boundary",
    ]
    for feat_name in historical:
        feat = specs / feat_name
        feat.mkdir(parents=True)
        (feat / "tasks.md").write_text(_PENDING_TASKS)
        _os.utime(feat / "tasks.md", (2_000, 2_000))  # old — historical

    # 1 current in-flight feature — unchecked tasks, NEWEST mtime
    current_feat = specs / "040-outflow-honesty"
    current_feat.mkdir(parents=True)
    (current_feat / "tasks.md").write_text(_PENDING_TASKS)
    _os.utime(current_feat / "tasks.md", (9_000, 9_000))  # newest → current

    # 2 ungraded dirs (spec.md only, no tasks.md) to reach 40 total
    for uname in ("050-ungraded-a", "051-ungraded-b"):
        ufeat = specs / uname
        ufeat.mkdir(parents=True)
        (ufeat / "spec.md").write_text("# Spec\n")

    # Seed the goal pointing at the workspace with the real specs structure
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", workspace_dir=str(workspace))
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    # The 5 historical dirs have mtime < current dir's mtime → not offending.
    # speckit_offending_dirs_sync returns [] → dispatch PROCEEDS.
    assert out is Outcome.DISPATCHED, (
        f"expected DISPATCHED but got {out}; "
        "historical feature dirs with old mtimes must not block dispatch"
    )
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_dispatch_proceeds_when_no_specs_yet(tmp_path, monkeypatch):
    """First dispatch on a fresh goal has no specs at all (0, 0, 0) — gate
    must sail through and allow the worker to create the spec."""
    # total=0 — no specs directory yet
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_feature_state_sync", lambda ws: (0, 0, 0)
    )
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_speckit_dispatch_gate_is_zero_token(tmp_path, monkeypatch):
    """The gate is pure fs, never an LLM call — zero-token invariant."""
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_feature_state_sync", lambda ws: (2, 2, 2)
    )
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator = FakeClaude()

    await _tick(store, "g", evaluator, FakeEngine(), RecordingNotifier())

    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_speckit_dispatch_gate_fails_open_on_probe_error(tmp_path, monkeypatch):
    """A probe hiccup (exception from speckit_feature_state_sync) must never
    wedge dispatch — the gate fails OPEN so a fs error doesn't stall a goal."""
    def _raise(ws: str):
        raise OSError("simulated fs error")
    monkeypatch.setattr("devclaw.goal.slice_guard.speckit_feature_state_sync", _raise)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED     # sailed through despite the probe error
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_slice_hold_cap_transitions_to_blocked_with_offending_dirs(
    tmp_path, monkeypatch
):
    """N consecutive holds escalate to blocked with blocked_kind='mechanical:slice_hold'
    and a reason naming the offending dirs — named regression for issue #728 Part B.
    Seeds a goal at slice_hold_count == _SLICE_HOLD_CAP - 1, patches both probe
    functions to return one offending dir, ticks once, and asserts the goal reaches
    blocked (not idle), blocked_kind names the mechanism, and the offending dir
    appears in the blocked reason."""
    from devclaw.goal.tick_dispatch import _SLICE_HOLD_CAP

    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_feature_state_sync", lambda ws: (3, 3, 3)
    )
    monkeypatch.setattr(
        "devclaw.goal.slice_guard.speckit_offending_dirs_sync",
        lambda ws, cur: ["specs/001-a"],
    )
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(slice_hold_count=_SLICE_HOLD_CAP - 1))
    evaluator = FakeClaude()
    engine = FakeEngine()
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    assert engine.dispatched == []
    assert evaluator.calls == 0          # zero-token: pure fs probe, no LLM
    saved = store.load_status("g")
    assert saved.phase == "blocked"
    assert saved.blocked_kind == "mechanical:slice_hold"
    assert "specs/001-a" in (saved.blocked_on or ""), (
        f"offending dir not in blocked_on: {saved.blocked_on!r}"
    )
    assert saved.slice_hold_count == 0   # reset on transition to blocked
    owner_pings = [m for m in notifier.sent if "specs/001-a" in m]
    assert owner_pings, f"expected an owner ping naming the offending dir; got {notifier.sent}"


def test_slice_hold_count_resets_on_steer_and_resume(tmp_path):
    """steer_goal and resume_goal must zero slice_hold_count so a human who
    resolves a mechanical:slice_hold block doesn't re-trigger the cap on the
    very next hold (issue #728 Part B)."""
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue
    from devclaw.goal.tick_dispatch import _SLICE_HOLD_CAP

    goals_dir = tmp_path / "goals"
    seed_goal(goals_dir, "g")

    db = StateStore(str(tmp_path / "state.db"))
    try:
        cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
        svc = GoalService(TaskQueue(db), db, config=cfg)

        # steer_goal: unblock a mechanical:slice_hold → slice_hold_count must be 0
        svc._goal_store.save_status(
            "g",
            GoalStatus(
                phase="blocked",
                blocked_on="specs/001-a has pending tasks",
                blocked_kind="mechanical:slice_hold",
                slice_hold_count=_SLICE_HOLD_CAP,
            ),
        )
        svc.steer_goal("g", "narrowed to one feature — carry on")
        saved = svc._goal_store.load_status("g")
        assert saved.phase == "idle"
        assert saved.slice_hold_count == 0, (
            f"steer_goal must zero slice_hold_count; got {saved.slice_hold_count}"
        )

        # resume_goal: same contract
        svc._goal_store.save_status(
            "g",
            GoalStatus(
                phase="blocked",
                blocked_on="specs/001-a has pending tasks",
                blocked_kind="mechanical:slice_hold",
                slice_hold_count=_SLICE_HOLD_CAP,
            ),
        )
        svc.resume_goal("g")
        saved = svc._goal_store.load_status("g")
        assert saved.phase == "idle"
        assert saved.slice_hold_count == 0, (
            f"resume_goal must zero slice_hold_count; got {saved.slice_hold_count}"
        )
    finally:
        db.close()


# ---- #394: total merge-policy resolution + settle-time mergeability --------


@pytest.mark.asyncio
async def test_conflicting_pr_at_settle_pings_owner_and_logs_conflict(tmp_path, monkeypatch):
    """#394 done-when 1: a delivery whose PR is CONFLICTING at settle is a
    degraded delivery and must be LOUD — an owner ping naming the conflict and
    a log line that says the PR cannot land — never a silent `done`
    indistinguishable from a landable one (closeloop-bench PR #8 accumulated
    three such deliveries overnight, 2026-07-28)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight(
            "devclaw", "implement_feature", "t1", "task", "add /health",
        ),  # goal-branch topology: PR stays open → probed
    ))
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="ok",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))
    notifier, probe = RecordingNotifier(), RecordingProbe(verdict=True)

    await _tick(store, "g", evaluator, engine, notifier,
                mergeability_probe=probe)

    assert probe.asked == ["https://github.com/o/r/pull/9"]
    pings = [m for m in notifier.sent if "cannot land" in m]
    assert pings, f"expected an owner ping for a CONFLICTING PR, got {notifier.sent}"
    assert "https://github.com/o/r/pull/9" in pings[0]
    log = (tmp_path / "g" / "log.md").read_text()
    assert "CONFLICTING" in log


@pytest.mark.asyncio
async def test_mergeable_open_pr_at_settle_stays_quiet(tmp_path):
    """The probe's False (cleanly mergeable) and None (could-not-tell) verdicts
    add nothing: no ping, no conflict log line — the probe only ever speaks on
    a definite CONFLICTING, so a gh hiccup can't cry wolf."""
    for verdict in (False, None):
        goal_id = f"g{verdict}"
        store = _store(tmp_path, Clock())
        seed_goal(tmp_path, goal_id)
        store.save_status(goal_id, GoalStatus(
            phase="in_flight", lifecycle="executing",
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "x"),
        ))
        notifier = RecordingNotifier()
        engine = FakeEngine(poll_result=PollResult(
            terminal=True, status="done", detail="ok",
            pr_url="https://github.com/o/r/pull/9", gate_passed=True,
        ))

        await _tick(store, goal_id, FakeClaude(), engine, notifier,
                    mergeability_probe=RecordingProbe(verdict=verdict))

        assert not any("cannot land" in m for m in notifier.sent)
        assert "CONFLICTING" not in (tmp_path / goal_id / "log.md").read_text()




@pytest.mark.asyncio
async def test_tick_all_resolves_verify_done_per_goal(tmp_path):
    """The per-goal-freshness contract for the done-gate
    re-check flag: tick_all must call verify_done_resolver once per goal (so a
    project's verify_done override can't leak onto another goal in the sweep),
    not resolve one fleet-wide value. Prove the resolver is invoked per goal_id."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "a", workspace_dir="/repos/a")
    seed_goal(tmp_path, "b", workspace_dir="/repos/b")
    store.save_status("a", GoalStatus(lifecycle="investigating"))
    store.save_status("b", GoalStatus(lifecycle="investigating"))

    seen: list[str] = []

    def _vd_resolver(goal):
        seen.append(goal.workspace_dir)
        return goal.workspace_dir == "/repos/a"  # per-goal, distinct values

    evaluator = FakeClaude()
    engine = FakeEngine()
    notifier = RecordingNotifier()

    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done_resolver=_vd_resolver,
    )

    assert sorted(seen) == ["/repos/a", "/repos/b"]  # called fresh per goal


@pytest.mark.asyncio
async def test_tick_all_resolves_autodeploy_per_goal(tmp_path):
    """autodeploy gets the same per-goal-freshness treatment: tick_all calls
    autodeploy_resolver once per goal so a project's autodeploy override can't
    leak onto another goal in the sweep."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "a", workspace_dir="/repos/a")
    seed_goal(tmp_path, "b", workspace_dir="/repos/b")
    store.save_status("a", GoalStatus(lifecycle="investigating"))
    store.save_status("b", GoalStatus(lifecycle="investigating"))

    seen: list[str] = []

    def _ad_resolver(goal):
        seen.append(goal.workspace_dir)
        return goal.workspace_dir == "/repos/a"

    evaluator = FakeClaude()
    engine = FakeEngine()
    notifier = RecordingNotifier()

    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
        autodeploy_resolver=_ad_resolver,
    )

    assert sorted(seen) == ["/repos/a", "/repos/b"]


@pytest.mark.asyncio
async def test_auto_deploy_disabled_returns_empty_without_deploying(tmp_path):
    """_auto_deploy honors its resolved `enabled` flag (no longer the env var):
    enabled=False short-circuits to '' before any deploy attempt."""
    from devclaw.goal.tick import _auto_deploy

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", workspace_dir="/repos/g")
    goal = store.load_goal("g")
    out = await _auto_deploy("g", goal, store, enabled=False)
    assert out == ""


# ---- the lifecycle heal moved off the tick and into the cutoff (#616) -------


@pytest.mark.asyncio
async def test_the_tick_no_longer_heals_a_lifecycle_because_none_can_be_stale(tmp_path):
    """The spec 008 shrink left pre-shrink ``investigating``/``firming`` rows
    in the DB, and the tick healed them loudly on first touch — a migration
    living on the hot path of every goal, forever, with no cutoff.

    #616 moved it where a migration belongs: the cutoff runs once, at store
    construction, and the tick branch is gone. This pins the second half of
    that — the tick performs NO heal write and logs nothing about a
    lifecycle, so the zero-token, zero-churn idle tick really is zero-work."""
    seed_goal(tmp_path, "g", cadence="1d")
    store = _store(tmp_path, Clock())
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.IDLE
    # the heal was the only thing an idle tick would ever log
    assert store.recent_log("g") == ""
    assert store.load_status("g").lifecycle == "executing"
    assert evaluator.calls == 0
    assert engine.dispatched == []


@pytest.mark.asyncio
async def test_legacy_goal_skips_investigation_and_dispatches(tmp_path):
    """A goal with no lifecycle (created before the front-end existed) behaves as
    'executing' — it dispatches an advance session immediately, no discovery review."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")  # default status → lifecycle None
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert action.tool == "implement_feature"                # the advance session, NOT a discovery review
    assert "Advance this goal" in action.goal


# ---- notification altitude (owner hears only owner-level by default) --------


@pytest.mark.asyncio
async def test_per_task_dispatch_is_suppressed_by_default(tmp_path, monkeypatch):
    """At the default 'owner' altitude the 🚀 per-task dispatch line — the spam a
    non-technical owner should never see — must NOT reach the notifier, even
    though the action is genuinely dispatched."""
    monkeypatch.delenv("DEVCLAW_NOTIFY_ALTITUDE", raising=False)  # default = owner
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED          # the action still ran
    assert len(engine.dispatched) == 1
    assert notifier.sent == []                # …but the owner heard nothing about it


@pytest.mark.asyncio
async def test_owner_level_blocker_always_sends(tmp_path, monkeypatch):
    """A real blocker (needs-you) is owner-altitude — it reaches the owner even at
    the default 'owner' floor. Driven by the lost-ref block, a real needs-you."""
    monkeypatch.delenv("DEVCLAW_NOTIFY_ALTITUDE", raising=False)
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t_gone", "task", "add /health"),
    ))
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    engine = FakeEngine(poll_exc=GoalEngineError("unknown task_id: t_gone"))

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    assert any("t_gone" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_task_altitude_restores_the_firehose(tmp_path, monkeypatch):
    """DEVCLAW_NOTIFY_ALTITUDE=task is the debug firehose: the 🚀 per-task dispatch
    line is sent again."""
    monkeypatch.setenv("DEVCLAW_NOTIFY_ALTITUDE", "task")
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    assert any("🚀" in m for m in notifier.sent)


# ---- workspace-prep failure: block legibly, never silently loop -------------


async def _failing_prepare(
    workspace_dir: str, repo_url: str | None = None, branch: str | None = None,
) -> str:
    """A prep that always fails the way a bad/missing/private repo_url does."""
    from devclaw.engine.workspace import WorkspaceError

    raise WorkspaceError("clone failed: remote: Repository not found.")


async def _tick_prep(store, goal_id, engine, notifier, *, prepare_ws):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=FakeClaude(), notifier=notifier,
        notify_url="http://relay", prepare_ws=prepare_ws,
    )


@pytest.mark.asyncio
async def test_executing_prep_failure_blocks_with_real_error(tmp_path):
    """A clone failure on the executing path must BLOCK with the git error as
    blocked_on (not drop to phase=idle and re-clone forever), and notify the
    owner — the regression that made a 1-char repo_url typo look like a dead goal."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle"))
    engine, notifier = FakeEngine(), RecordingNotifier()

    out = await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare)

    assert out is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.phase == "blocked"
    assert "Repository not found" in (st.blocked_on or "")
    assert engine.dispatched == []                       # never ran the agent
    assert any("Repository not found" in m for m in notifier.sent)  # owner heard it


@pytest.mark.asyncio
async def test_investigation_prep_failure_blocks_without_cognition(tmp_path):
    """A long_lived goal preps its workspace on the executing/advance-dispatch
    path (demolition P4 — it does not investigate). ``prepare_ws`` runs BEFORE
    the dispatch, so a prep failure there blocks immediately (the same
    ``_block_on_prep_failure`` the one_shot investigation path uses), spending
    zero cognition tokens and dispatching nothing."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    engine, notifier = FakeEngine(), RecordingNotifier()

    out = await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare)

    assert out is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.phase == "blocked" and st.lifecycle == "executing"
    assert "Repository not found" in (st.blocked_on or "")
    assert engine.dispatched == []


@pytest.mark.asyncio
async def test_blocked_on_prep_failure_does_not_respam(tmp_path):
    """After a prep-failure block, a cadence-only tick (no steering) stays idle
    and silent — one notification, not one per tick. Zero cognition."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle"))
    engine, notifier = FakeEngine(), RecordingNotifier()

    await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare)
    sent_after_block = len(notifier.sent)

    out = await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare)

    assert out is Outcome.IDLE
    assert len(notifier.sent) == sent_after_block        # no second ping


# ---- done-gate review brief — the two-axis structural fix -------------------


def test_done_gate_review_brief_carries_both_axes(tmp_path):
    """The brief must instruct the reviewer to grade TWO axes — functional
    clauses AND structural health. Without the second axis, four PRs can each
    satisfy the clauses while compounding bloat (closeloop App.tsx 1153 →
    1827 LOC). The brief now demands a ``## Structural health`` section."""
    from devclaw.goal.tick import _done_gate_review_brief
    seed_goal(tmp_path, "g")
    store = _store(tmp_path, Clock())
    goal = store.load_goal("g")
    brief = _done_gate_review_brief(goal)
    # functional axis (pre-existing)
    assert "## Per-clause evidence" in brief
    # structural axis (new)
    assert "## Structural health" in brief
    assert "verdict: clean | concerns | poor" in brief
    # the "what would a senior engineer do" framing — agency, not rules
    assert "senior engineer" in brief.lower()
    # the failure modes the structural section must catch
    body = brief.lower()
    for smell in ("god object", "untested behaviour", "no-op stub"):
        assert smell in body, f"structural section should name {smell!r} as a thing to catch"
    # the summary must speak to BOTH axes — not just clauses
    assert "BOTH axes" in brief or "both axes" in brief or "covering BOTH" in brief


# ---- grounded remote-checks at the done-gate (2026-07-06 benchmark fix) -----
#
# Backstory: closeloop-bench-2026-07-05 closed `achieved` while all 32 GitHub
# Actions runs on its goal branch were startup_failure — the sandbox gate was
# green and nothing ever looked at the repo's real check surface. When a
# remote checker is bound and the goal works on a shared goal branch, an
# `achieved` must survive the real CI state before the goal closes.

_ACHIEVED_EVAL = json.dumps({
    "verdict": "achieved",
    "rationale": "all clauses met",
    "clauses": [
        {"clause": "/health returns 200", "satisfied": True,
         "evidence": "src/Health.cs:12; HealthTests.cs:8 passes"},
    ],
    "structural_health": "clean",
})


class FakeRemoteChecker:
    """Records (repo_url, branch) calls; returns a canned result or raises."""

    def __init__(self, result=None, exc: Exception | None = None):
        from devclaw.goal.remote_checks import RemoteChecksResult

        self.result = result or RemoteChecksResult("passing", "all green")
        self.exc = exc
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, repo_url: str, branch: str):
        self.calls.append((repo_url, branch))
        if self.exc:
            raise self.exc
        return self.result


def _verifying_goal_branch_goal(store, tmp_path, goal_id="g"):
    """Seed a goal-branch-topology goal parked at the done-gate review settle.

    The checker keys on the delivery topology, which since spec 008 shrink is
    the explicit ``lifecycle="executing"`` stamp (the checklist that used to be
    the goal-branch signal is gone)."""
    seed_goal(tmp_path, goal_id)
    store.save_status(goal_id, GoalStatus(
        phase="verifying", lifecycle="executing",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))


@pytest.mark.asyncio
async def test_failing_remote_checks_block_the_close(tmp_path):
    from devclaw.goal.remote_checks import RemoteChecksResult

    store = _store(tmp_path, Clock())
    _verifying_goal_branch_goal(store, tmp_path)
    checker = FakeRemoteChecker(RemoteChecksResult("failing", "32 failed of 32 (32× startup_failure)"))
    evaluator = FakeClaude(_ACHIEVED_EVAL)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review ok"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, remote_checker=checker)

    assert out is Outcome.SLEPT                       # not done — steered back in
    assert checker.calls == [("https://example.com/demo.git", "goal/g")]
    s = store.load_status("g")
    assert s.phase == "idle" and s.phase != "done"
    assert s.last_eval_verdict == "off_track"
    assert "remote checks (goal/g): failing" in store.recent_log("g")
    # the correction steers the fix
    steering = store.unread_steering("g")
    assert "[remote-checks]" in steering
    assert "startup_failure" in steering


@pytest.mark.asyncio
async def test_never_ran_ci_blocks_the_close_under_strict_gate(tmp_path, monkeypatch):
    from devclaw.goal import remote_checks as _rc
    from devclaw.goal.remote_checks import RemoteChecksResult

    monkeypatch.setattr(_rc, "CI_GATE_MODE", "strict")
    store = _store(tmp_path, Clock())
    _verifying_goal_branch_goal(store, tmp_path)
    checker = FakeRemoteChecker(RemoteChecksResult("none", "workflows exist but zero runs"))
    evaluator = FakeClaude(_ACHIEVED_EVAL)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review ok"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, remote_checker=checker)

    assert out is Outcome.SLEPT
    assert store.load_status("g").phase != "done"
    steering = store.unread_steering("g")
    assert "ZERO" in steering or "zero" in steering


@pytest.mark.asyncio
async def test_broken_ci_infra_closes_with_annotation_under_flexible_gate(tmp_path):
    # Default (flexible) ci-gate: startup_failure-only CI is infrastructure
    # trouble, not code trouble — the verified close is honored, but the
    # verdict the owner reads says loudly that CI never executed.
    from devclaw.goal.remote_checks import RemoteChecksResult

    store = _store(tmp_path, Clock())
    _verifying_goal_branch_goal(store, tmp_path)
    checker = FakeRemoteChecker(
        RemoteChecksResult("infra_broken", "5 of 5 died at startup — CI infrastructure never executed")
    )
    evaluator = FakeClaude(_ACHIEVED_EVAL)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review ok"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, remote_checker=checker)

    assert out is Outcome.DONE
    assert store.load_status("g").phase == "done"
    assert "internal verify gate only" in store.load_status("g").last_eval_note


@pytest.mark.asyncio
async def test_passing_remote_checks_let_the_goal_close(tmp_path):
    store = _store(tmp_path, Clock())
    _verifying_goal_branch_goal(store, tmp_path)
    checker = FakeRemoteChecker()  # passing
    evaluator = FakeClaude(_ACHIEVED_EVAL)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review ok"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, remote_checker=checker)

    assert out is Outcome.DONE
    assert store.load_status("g").phase == "done"
    assert "remote checks (goal/g): passing" in store.recent_log("g")


@pytest.mark.asyncio
async def test_unknown_remote_state_fails_open_but_logs(tmp_path):
    from devclaw.goal.remote_checks import RemoteChecksResult

    store = _store(tmp_path, Clock())
    _verifying_goal_branch_goal(store, tmp_path)
    checker = FakeRemoteChecker(RemoteChecksResult("unknown", "gh: network unreachable"))
    evaluator = FakeClaude(_ACHIEVED_EVAL)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review ok"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, remote_checker=checker)

    # infra uncertainty must not wedge a verified goal — but it IS observable
    assert out is Outcome.DONE
    assert "remote checks (goal/g): unknown" in store.recent_log("g")


@pytest.mark.asyncio
async def test_checker_exception_fails_open(tmp_path):
    store = _store(tmp_path, Clock())
    _verifying_goal_branch_goal(store, tmp_path)
    checker = FakeRemoteChecker(exc=RuntimeError("gh exploded"))
    evaluator = FakeClaude(_ACHIEVED_EVAL)
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review ok"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier, remote_checker=checker)

    assert out is Outcome.DONE
    assert "unknown" in store.recent_log("g")


def test_done_gate_review_brief_forbids_existence_only_test_evidence(tmp_path):
    """The in-sandbox reviewer must be told the same rule the evaluator
    enforces: spec files existing ≠ tests passing (closeloop-bench-2026-07-05
    shipped a verify.sh that only grepped for the Playwright files)."""
    from devclaw.goal.tick import _done_gate_review_brief
    from devclaw.goal.models import Goal

    brief = _done_gate_review_brief(Goal(
        id="g", objective="o", cadence="1d", engine="devclaw", workspace_dir="/ws",
        done_when="the flow is tested end to end",
    ))
    assert "merely EXIST" in brief
    assert "does NOT satisfy" in brief


# ---- standing-goal done-gate (the 2026-07-06 benchmark fix) -----------------


@pytest.mark.asyncio
async def test_standing_goal_done_gate_blocks_instead_of_closing(tmp_path):
    """closeloop-bench-2026-07-05: a done_when that declares the goal STANDING
    ("not a bounded criterion") must never terminally close via the done-gate.
    An all-axes-pass review becomes needs_human → the goal BLOCKS and the owner
    gets the close-or-steer decision; phase stays out of 'done'."""
    store = _store(tmp_path, Clock())
    seed_goal(
        tmp_path, "g",
        done_when=(
            "Not applicable as a bounded criterion — this is a standing goal. "
            "Judge each delivery against the four axes; fail any → off_track."
        ),
    )
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "achieved",
        "rationale": "every axis passes",
        "clauses": [
            {"clause": "research is real", "satisfied": True, "evidence": "docs/research/crm.md"},
        ],
        "structural_health": "clean",
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review ok"))
    notifier = RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.phase == "blocked"
    assert s.phase != "done"
    assert "standing" in (s.blocked_on or "").lower()
    # the evaluator prompt carried the contract note
    assert "STANDING-GOAL CONTRACT" in evaluator.last_prompt
    # owner was told, not bypassed
    assert any("standing" in m.lower() for m in notifier.sent)


# ---- orphaned-ref sweep (2026-07-09 lost-in-flight-ref incident) -----------
#
# The program-ref sweep cases that lived here died with the program/DAG lane
# (spec 022 US3) — the sweep now probes only the task finder, pinned in
# test_goal_transactional.py (sweep-extends-to-tasks + legacy-log seeding).


@pytest.mark.asyncio
async def test_save_status_is_atomic_replace(tmp_path):
    """save_status must never leave a partial STATUS.md or a stray tmp file —
    the file is the only link to in-flight work."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(phase="in_flight", in_flight=InFlight("devclaw", "start_program", "p1", "program")),
    )
    d = tmp_path / "g"
    assert not (d / "STATUS.md.tmp").exists()
    assert store.load_status("g").in_flight.id == "p1"


# ---- lost in-flight ref: block legibly, never wedge (audit 2026-07-10) ------
# The engine row a ref points at can vanish (DB loss, manual cleanup, a
# cross-environment restore). poll then raises GoalEngineError on EVERY tick,
# and tick_all's catch-all logs "tick error (isolated)" without clearing
# in_flight — a silent, permanent error loop. These tests pin the guard: one
# BLOCKED tick that clears the ref, one owner ping, then zero-token idle.


@pytest.mark.asyncio
async def test_lost_action_ref_blocks_and_notifies_owner(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t_gone", "task", "add /health"),
    ))
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    engine = FakeEngine(poll_exc=GoalEngineError("unknown task_id: t_gone"))

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.phase == "blocked"
    assert st.in_flight is None                          # the lost ref is cleared
    assert "task t_gone" in (st.blocked_on or "")
    assert "unknown task_id" in (st.blocked_on or "")    # the real error, not a paraphrase
    assert evaluator.calls == 0   # zero cognition on the failure path
    assert len(notifier.sent) == 1                       # owner heard it exactly once
    assert "t_gone" in notifier.sent[0]
    assert "t_gone" in store.recent_log("g")


@pytest.mark.asyncio
async def test_lost_ref_block_does_not_loop(tmp_path):
    """The wedge regression proper: the NEXT tick after a lost-ref block must
    idle at 0 tokens (no re-poll, no re-raise, no second ping) — cadence never
    re-pokes a blocked goal, only steering does."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t_gone", "task", "add /health"),
    ))
    notifier = RecordingNotifier()
    engine = FakeEngine(poll_exc=GoalEngineError("unknown task_id: t_gone"))
    await _tick(store, "g", FakeClaude(), engine, notifier)
    sent_after_block = len(notifier.sent)

    out = await _tick(store, "g", FakeClaude(), engine, notifier)

    assert out is Outcome.IDLE
    assert engine.polls == 1                              # nothing left to poll
    assert len(notifier.sent) == sent_after_block         # no second ping


@pytest.mark.asyncio
async def test_lost_discovery_ref_blocks_and_notifies_owner(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "review_repository", "t_disc", "task", "analyze"),
    ))
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    engine = FakeEngine(poll_exc=GoalEngineError("unknown task_id: t_disc"))

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.phase == "blocked" and st.in_flight is None
    assert "task t_disc" in (st.blocked_on or "")
    # (This used to seed lifecycle="investigating" and assert the block pinned
    # it back to "executing", so _classify couldn't route the next tick into a
    # fresh review that contradicted the "paused; steer me" ping. That phase
    # was removed by the spec 008 shrink and the #616 cutoff migrated the last
    # rows carrying it, so there is one lifecycle value and nothing to pin.)
    assert st.lifecycle == "executing"
    assert evaluator.calls == 0
    assert len(notifier.sent) == 1 and "t_disc" in notifier.sent[0]

    # Next tick: a true block — idles at zero tokens, no re-dispatch, no re-ping.
    out2 = await _tick(store, "g", evaluator, engine, notifier)
    assert out2 is Outcome.IDLE
    assert evaluator.calls == 0
    assert len(notifier.sent) == 1
    assert store.load_status("g").phase == "blocked"


@pytest.mark.asyncio
async def test_lost_done_gate_ref_blocks_and_notifies_owner(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "t_gate", "task", "verify", is_done_check=True),
    ))
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    engine = FakeEngine(poll_exc=GoalEngineError("unknown task_id: t_gate"))

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.phase == "blocked" and st.in_flight is None
    assert "task t_gate" in (st.blocked_on or "")
    assert evaluator.calls == 0
    assert len(notifier.sent) == 1 and "t_gate" in notifier.sent[0]


# ---- goal.yaml is the whole contract (checklist/firmed overlays died) -------


@pytest.mark.asyncio
async def test_goal_yaml_alone_is_the_whole_contract(tmp_path):
    """A goal directory holding ONLY goal.yaml advances normally — the
    checklist.yaml / firmed-draft.yaml overlays (and their corrupt-doc probe)
    died with the host-cognition chain (spec 008 shrink)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")  # goal.yaml only
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED  # advance dispatch unchanged
    assert len(engine.dispatched) == 1
    assert store.load_status("g").phase == "in_flight"


# ---- blocked_kind: structured block classification (F8 prerequisite) --------
# A planned auto-heal pass must distinguish MECHANICAL blocks (condition
# cheaply re-checkable without an LLM) from NEEDS-ANSWER blocks (cognition
# asked the owner) and BUG blocks (the force_block escape hatch) — by a
# structured field, never by string-matching the blocked_on prose.


@pytest.mark.asyncio
async def test_blocked_kind_stamped_per_block_site(tmp_path, monkeypatch):
    """Each block class stamps its machine-readable kind next to the prose:
    a workspace-prep failure → mechanical:prep, the dispatch-cap backstop
    → mechanical:dispatch_cap, the done-gate blocking for a human decision
    → needs_answer, and force_block (the illegal-transition escape hatch) → bug."""
    # Each sub-scenario is an INDEPENDENT goal, so each gets its own workspace:
    # goals sharing one project now serialize under the single-writer hold
    # (spec 010 P1), which would queue every goal after the first and starve the
    # scenarios below of the very block sites they exist to exercise.
    store = _store(tmp_path, Clock())
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    # mechanical:prep — the workspace couldn't be prepared (clone 404)
    seed_goal(tmp_path, "gc", workspace_dir="/repos/gc")
    assert await _tick_prep(store, "gc", engine, notifier, prepare_ws=_failing_prepare) is Outcome.BLOCKED
    s = store.load_status("gc")
    assert s.phase == "blocked" and s.blocked_kind == "mechanical:prep"
    # the STATUS.md view surfaces the kind next to blocked_on
    text = (tmp_path / "gc" / "STATUS.md").read_text()
    assert "blocked [mechanical:prep] —" in text

    # mechanical:dispatch_cap — the runaway backstop (backlog 2 → cap 4)
    seed_goal(tmp_path, "gd", workspace_dir="/repos/gd")
    store.save_status("gd", GoalStatus(phase="idle", actions_dispatched=4))
    assert await _tick(store, "gd", evaluator, engine, notifier) is Outcome.BLOCKED
    assert store.load_status("gd").blocked_kind == "mechanical:dispatch_cap"

    # bug — the force_block illegal-transition escape hatch
    seed_goal(tmp_path, "gb", workspace_dir="/repos/gb")
    assert store.force_block("gb", "illegal state transition: EXEC_IDLE --ACHIEVE-> …") is True
    sb = store.load_status("gb")
    assert sb.phase == "blocked" and sb.blocked_kind == "bug"


def test_blocked_kind_cleared_on_unblock(tmp_path):
    """steer_goal lifts the block → blocked_kind returns to "" (enforced at
    the store's write choke point: any write landing on a non-blocked phase
    clears the kind, so no unblock path can leak a stale classification) and
    heal_attempts returns to 0 (a HUMAN lifting the block restores the full
    mechanical auto-heal budget)."""
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    seed_goal(goals_dir, "g")

    db = StateStore(str(tmp_path / "state.db"))
    try:
        cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
        svc = GoalService(TaskQueue(db), db, config=cfg)
        svc._goal_store.save_status(
            "g", GoalStatus(phase="blocked", blocked_on="cap hit",
                            blocked_kind="mechanical:dispatch_cap", actions_dispatched=5,
                            heal_attempts=2),
        )
        assert svc._goal_store.load_status("g").blocked_kind == "mechanical:dispatch_cap"

        svc.steer_goal("g", "resume with new approach")

        saved = svc._goal_store.load_status("g")
        assert saved.phase == "idle"
        assert saved.blocked_kind == ""
        assert saved.heal_attempts == 0
    finally:
        db.close()


def test_resume_goal_restores_the_autoheal_budget(tmp_path):
    """resume_goal is the OTHER human unblock verb — it must restore the
    mechanical auto-heal budget exactly like steer_goal does: a parked goal
    (heal_attempts past the cap, a prep backoff window still pending) that a
    human resumes gets heal_attempts=0 and next_heal_at=None, so a future
    mechanical block starts with the full budget instead of inheriting the
    exhausted one."""
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.state_store import StateStore
    from devclaw.task_queue import TaskQueue

    goals_dir = tmp_path / "goals"
    seed_goal(goals_dir, "g")

    db = StateStore(str(tmp_path / "state.db"))
    try:
        cfg = GoalConfig(goals_dir=goals_dir, notify_url="", tick_seconds=900, verify_done=False)
        svc = GoalService(TaskQueue(db), db, config=cfg)
        svc._goal_store.save_status(
            "g", GoalStatus(phase="blocked", blocked_on="clone failed: repo not found",
                            blocked_kind="mechanical:prep", actions_dispatched=3,
                            heal_attempts=6, next_heal_at="2099-01-01T00:00:00"),
        )

        out = svc.resume_goal("g")
        assert out["resumed"] is True

        saved = svc._goal_store.load_status("g")
        assert saved.phase == "idle"
        assert saved.blocked_kind == ""
        assert saved.heal_attempts == 0
        assert saved.next_heal_at is None
    finally:
        db.close()


@pytest.mark.asyncio
async def test_autoheal_never_fires_on_human_gated_blocks(tmp_path):
    """A healthy store must NOT unblock a needs_answer block (the owner has a
    question to answer), a bug block (the force_block escape hatch), or a
    legacy mechanical:corrupt_doc block (its contract files died with the
    host-cognition chain — spec 008 shrink — so there is nothing mechanical
    left to recheck; resume_goal is the way out). Auto-heal is prep-only."""
    # Independent goals ⇒ independent workspaces (see the note in
    # test_blocked_kind_stamped_per_block_site): same-project goals now queue
    # behind one another under the spec 010 single-writer hold.
    store = _store(tmp_path, Clock())
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    # legacy mechanical:corrupt_doc — a pre-shrink row still parked on it
    seed_goal(tmp_path, "gl", workspace_dir="/repos/gl")
    store.save_status("gl", GoalStatus(
        phase="blocked", blocked_on="checklist.yaml is corrupted",
        blocked_kind="mechanical:corrupt_doc",
    ))
    assert await _tick(store, "gl", evaluator, engine, notifier) is Outcome.IDLE
    sl = store.load_status("gl")
    assert sl.phase == "blocked" and sl.blocked_kind == "mechanical:corrupt_doc"
    assert sl.heal_attempts == 0                          # never even attempted

    # needs_answer — the owner has a question to answer (kind-stamping from a
    # real block site is covered by test_blocked_kind_stamped_per_block_site).
    seed_goal(tmp_path, "gq", workspace_dir="/repos/gq")
    store.save_status("gq", GoalStatus(
        phase="blocked", blocked_on="which auth provider?", blocked_kind="needs_answer",
    ))
    pings = len(notifier.sent)
    assert await _tick(store, "gq", evaluator, engine, notifier) is Outcome.IDLE
    sq = store.load_status("gq")
    assert sq.phase == "blocked" and sq.blocked_kind == "needs_answer"
    assert sq.heal_attempts == 0                          # never even attempted

    # bug — the force_block illegal-transition escape hatch.
    seed_goal(tmp_path, "gb", workspace_dir="/repos/gb")
    assert store.force_block("gb", "illegal state transition: …") is True
    assert await _tick(store, "gb", evaluator, engine, notifier) is Outcome.IDLE
    sb = store.load_status("gb")
    assert sb.phase == "blocked" and sb.blocked_kind == "bug"
    assert evaluator.calls == 0
    assert len(notifier.sent) == pings                    # no heal chatter either


@pytest.mark.asyncio
async def test_lost_ref_block_stays_human_gated(tmp_path):
    """mechanical:lost_ref never auto-heals: _block_on_lost_ref destroys the
    in_flight ref at block time (the id survives only in blocked_on prose), so
    there is nothing mechanical left to recheck — the block is deliberately
    human-gated even though the store itself is perfectly healthy."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t_gone", "task", "add /health"),
    ))
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    engine = FakeEngine(poll_exc=GoalEngineError("unknown task_id: t_gone"))

    assert await _tick(store, "g", evaluator, engine, notifier) is Outcome.BLOCKED
    assert store.load_status("g").blocked_kind == "mechanical:lost_ref"
    pings = len(notifier.sent)

    # Healthy store, many ticks: stays blocked, zero cognition, no auto-unblock.
    for _ in range(3):
        assert await _tick(store, "g", evaluator, engine, notifier) is Outcome.IDLE
    s = store.load_status("g")
    assert s.phase == "blocked" and s.blocked_kind == "mechanical:lost_ref"
    assert s.heal_attempts == 0
    assert evaluator.calls == 0
    assert len(notifier.sent) == pings


# ---- prep-failure auto-heal (F8): ls-remote recheck on a capped backoff -----
# Unlike the corrupt-doc probe (free, every tick), the prep recheck costs a
# git subprocess, so it runs only when the persisted next_heal_at window is
# open — between windows a blocked goal is a zero-subprocess, zero-cognition
# tick. The ls-remote seam (tick_guards._ls_remote_ok_sync) is stubbed here;
# the block itself is driven by the REAL prep-failure path.


@pytest.mark.asyncio
async def test_prep_block_autoheals_when_remote_reachable_again(tmp_path, monkeypatch):
    """Prep-blocked goal + reachable remote → the first due recheck heals
    (one ls-remote against the goal's repo_url, no ping, log line), and the
    tick proceeds to dispatch with the REAL prepare_ws."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # executing/advance path: prep runs BEFORE any dispatch, so the block tick
    # is zero-token (mirrors test_investigation_prep_failure_blocks_without_cognition)
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    engine, notifier = FakeEngine(), RecordingNotifier()

    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.BLOCKED
    assert store.load_status("g").blocked_kind == "mechanical:prep"
    assert len(notifier.sent) == 1                        # the block ping only

    probes: list[str] = []

    def reachable(url: str) -> bool:
        probes.append(url)
        return True

    monkeypatch.setattr("devclaw.goal.tick_guards._ls_remote_ok_sync", reachable)
    out = await _tick_prep(store, "g", engine, notifier, prepare_ws=fake_prepare)

    assert out is Outcome.DISPATCHED                      # healed and went on to dispatch
    assert probes == ["https://example.com/demo.git"]     # exactly one ls-remote, the goal's URL
    assert len(engine.dispatched) == 1
    s = store.load_status("g")
    assert s.phase == "in_flight" and s.blocked_kind == ""
    assert s.heal_attempts == 1 and s.next_heal_at is None
    assert "auto-resumed: repo reachable again (heal 1/5)" in store.recent_log("g")
    assert len(notifier.sent) == 1                        # a heal logs; it never pings


@pytest.mark.asyncio
async def test_prep_heal_respects_backoff_window(tmp_path, monkeypatch):
    """A failed recheck arms an exponential next_heal_at window (30min·2^n,
    capped): before it opens, a tick runs NO recheck at all — zero subprocess,
    zero cognition. The heal budget continues across windows."""
    clock = Clock()
    store = _store(tmp_path, clock)
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    engine, notifier = FakeEngine(), RecordingNotifier()
    await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare)

    probes: list[str] = []
    reachable = {"now": False}

    def recheck(url: str) -> bool:
        probes.append(url)
        return reachable["now"]

    monkeypatch.setattr("devclaw.goal.tick_guards._ls_remote_ok_sync", recheck)

    # First recheck is due immediately (no window yet) — fails, arms 30min.
    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
    s = store.load_status("g")
    assert len(probes) == 1 and s.heal_attempts == 1 and s.next_heal_at is not None

    # Inside the window: NO recheck — the tick never even spawns the subprocess.
    clock.advance(10 * 60)
    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
    assert len(probes) == 1

    # Window open (t=31min > 30min): recheck fires, fails, window doubles to 1h.
    clock.advance(21 * 60)
    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
    assert len(probes) == 2 and store.load_status("g").heal_attempts == 2

    # 31min into the 1h window: still closed.
    clock.advance(31 * 60)
    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
    assert len(probes) == 2

    # Past the window and the remote is back: heal, budget continuing at 3/5.
    clock.advance(30 * 60)
    reachable["now"] = True
    out = await _tick_prep(store, "g", engine, notifier, prepare_ws=fake_prepare)
    assert out is Outcome.DISPATCHED
    assert len(probes) == 3
    s = store.load_status("g")
    assert s.blocked_kind == "" and s.heal_attempts == 3 and s.next_heal_at is None


@pytest.mark.asyncio
async def test_prep_heal_gives_up_after_cap(tmp_path, monkeypatch):
    """5 failed rechecks spend the budget; the next tick parks the goal with
    exactly ONE plain gave-up ping and never rechecks again — a parked goal
    is a zero-subprocess, zero-cognition tick until a human steers."""
    clock = Clock()
    store = _store(tmp_path, clock)
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    engine, notifier = FakeEngine(), RecordingNotifier()
    await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare)

    probes: list[str] = []

    def never_reachable(url: str) -> bool:
        probes.append(url)
        return False

    monkeypatch.setattr("devclaw.goal.tick_guards._ls_remote_ok_sync", never_reachable)

    # 5 failed rechecks, jumping past every backoff window (max 6h).
    for n in (1, 2, 3, 4, 5):
        clock.advance(7 * 3600)
        assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
        assert store.load_status("g").heal_attempts == n
    assert len(probes) == 5
    assert not any("gave up" in m for m in notifier.sent)

    # Budget spent: the next tick parks — one plain gave-up ping, NO recheck.
    clock.advance(7 * 3600)
    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
    assert len(probes) == 5                                # parked before any subprocess
    assert len([m for m in notifier.sent if "gave up" in m]) == 1
    s = store.load_status("g")
    assert s.phase == "blocked" and s.blocked_kind == "mechanical:prep"

    # And stays parked: no rechecks, no pings, zero cognition.
    pings = len(notifier.sent)
    for _ in range(3):
        clock.advance(7 * 3600)
        assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
    assert len(probes) == 5 and len(notifier.sent) == pings


@pytest.mark.asyncio
async def test_prep_heal_checks_workspace_git_when_no_repo_url(tmp_path, monkeypatch):
    """A goal with no repo_url (pre-existing-workspace config) rechecks by
    stat — does <workspace_dir>/.git exist — and must never spawn ls-remote."""
    clock = Clock()
    store = _store(tmp_path, clock)
    ws = tmp_path / "ws"
    seed_goal(tmp_path, "g", repo_url=None, workspace_dir=str(ws))
    store.save_status("g", GoalStatus(phase="idle"))  # executing path (world-research skips prep)
    engine, notifier = FakeEngine(), RecordingNotifier()

    def no_ls_remote(url: str) -> bool:
        raise AssertionError("ls-remote must not run for a goal without a repo_url")

    monkeypatch.setattr("devclaw.goal.tick_guards._ls_remote_ok_sync", no_ls_remote)

    # Executing path: plan → dispatch → prep fails → block (one plan call).
    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.BLOCKED
    assert store.load_status("g").blocked_kind == "mechanical:prep"

    # Workspace still isn't a checkout → the stat recheck fails, backoff arms.
    assert await _tick_prep(store, "g", engine, notifier, prepare_ws=_failing_prepare) is Outcome.IDLE
    assert store.load_status("g").heal_attempts == 1

    # The checkout appears; past the window the recheck passes and heals.
    (ws / ".git").mkdir(parents=True)
    clock.advance(31 * 60)
    out = await _tick_prep(store, "g", engine, notifier, prepare_ws=fake_prepare)
    assert out is Outcome.DISPATCHED
    s = store.load_status("g")
    assert s.blocked_kind == "" and s.heal_attempts == 2 and s.next_heal_at is None

# ---- trace volume hygiene (harden/trace-retention, 2026-07-15) --------------


class RecordingTrendDetector:
    """Trend-detector double — records which goals got a per-goal sweep."""

    def __init__(self):
        self.per_goal: list[str] = []
        self.harness_self = 0

    async def run_per_goal(self, *, goal_id: str, workspace_dir: str) -> None:
        self.per_goal.append(goal_id)

    async def run_harness_self(self) -> None:
        self.harness_self += 1


@pytest.mark.asyncio
async def test_trend_sweep_skips_cancelled_and_done_goals(tmp_path):
    """Dead goals get no trend sweep. Production 2026-07-15: the detector wrote
    ~350 trend rows per goal per night across 17 goals of which 15 were
    cancelled/done — the sweep must select only live goals. The harness-self
    pass (which observes devclaw itself, not any goal) still runs once."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "live", workspace_dir="/repos/live")
    seed_goal(tmp_path, "dead", workspace_dir="/repos/dead")
    seed_goal(tmp_path, "finished", workspace_dir="/repos/finished")
    store.save_status("live", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    store.save_status("dead", GoalStatus(phase="cancelled"))
    store.save_status("finished", GoalStatus(phase="done"))

    td = RecordingTrendDetector()
    evaluator = FakeClaude()
    engine, notifier = FakeEngine(), RecordingNotifier()

    await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
        trend_detector=td,
    )

    assert td.per_goal == ["live"]      # terminal goals not swept
    assert td.harness_self == 1         # the global pass still ran
    assert evaluator.calls == 0


class PruningEngine(FakeEngine):
    """FakeEngine that exposes both retention prune seams + the vacuum seam."""

    def __init__(self):
        super().__init__()
        self.prunes = 0
        self.event_prunes = 0
        self.vacuums = 0

    def prune_traces(self) -> int:
        self.prunes += 1
        return 0

    def prune_events(self) -> int:
        self.event_prunes += 1
        return 0

    def vacuum(self) -> bool:
        self.vacuums += 1
        return False


@pytest.mark.asyncio
async def test_tick_all_runs_trace_prune_on_cheap_path_with_zero_tokens(tmp_path):
    """tick_all invokes the engine's daily trace-retention prune once per
    sweep, AFTER the cheap gates — and it costs zero LLM calls (the zero-token
    idle guard is untouched: prune is pure SQLite)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    engine = PruningEngine()
    evaluator, notifier = FakeClaude(), RecordingNotifier()

    out = await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert engine.prunes == 1
    assert out["g"] is Outcome.IDLE
    assert evaluator.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "boom"),
    [
        ("prune_traces", "database is locked"),
        ("prune_events", "database is locked"),
        ("vacuum", "disk full"),
    ],
)
async def test_maintenance_crash_never_breaks_the_heartbeat(tmp_path, method, boom):
    """Any cheap-path maintenance crash (trace prune, events prune, VACUUM) is
    swallowed — housekeeping must never take down the sweep; goals still tick.

    One parametrized test replaces three byte-for-byte-parallel ones that
    differed only in which FakeEngine method raised: same fixture, same single
    assertion, three copies to keep in step."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    def _explode(self):
        raise RuntimeError(boom)

    engine_cls = type("ExplodingMaintenanceEngine", (FakeEngine,), {method: _explode})
    evaluator, notifier = FakeClaude(), RecordingNotifier()

    out = await tick_all(
        store=store, engine=engine_cls(), evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert out["g"] is Outcome.IDLE    # the sweep survived the crash

@pytest.mark.asyncio
async def test_tick_all_runs_events_prune_on_cheap_path_with_zero_tokens(tmp_path):
    """tick_all invokes the engine's daily events-retention prune once per
    sweep, AFTER the cheap gates, alongside the trace prune — and it costs zero
    LLM calls (pure SQLite, so the zero-token idle guard is untouched)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    engine = PruningEngine()
    evaluator, notifier = FakeClaude(), RecordingNotifier()

    out = await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert engine.event_prunes == 1
    assert out["g"] is Outcome.IDLE
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_tick_all_runs_vacuum_on_cheap_path_with_zero_tokens(tmp_path):
    """tick_all invokes the engine's weekly VACUUM once per sweep, AFTER the
    cheap gates, beside the prunes — and it costs zero LLM calls."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    engine = PruningEngine()
    evaluator, notifier = FakeClaude(), RecordingNotifier()

    out = await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert engine.vacuums == 1
    assert out["g"] is Outcome.IDLE
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_tick_all_pings_owner_when_db_size_alerts_at_zero_tokens(tmp_path):
    """When the DB-size alarm fires, tick_all pings the owner ONCE on the cheap
    path — and it costs zero LLM calls (raw owner send, no summarizer)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    class AlertingEngine(FakeEngine):
        def check_db_size_alert(self) -> "str | None":
            return "⚠️ devclaw.db has grown to 2.10 GB"

    evaluator, notifier = FakeClaude(), RecordingNotifier()

    out = await tick_all(
        store=store, engine=AlertingEngine(), evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert any("devclaw.db has grown" in m for m in notifier.sent)
    assert out["g"] is Outcome.IDLE
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_no_db_size_ping_when_under_threshold(tmp_path):
    """A None from the alarm (under threshold) sends nothing."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    class QuietEngine(FakeEngine):
        def check_db_size_alert(self) -> "str | None":
            return None

    evaluator, notifier = FakeClaude(), RecordingNotifier()

    await tick_all(
        store=store, engine=QuietEngine(), evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert not any("devclaw.db" in m for m in notifier.sent)


@pytest.mark.asyncio
async def test_db_size_alert_failure_never_breaks_the_heartbeat(tmp_path):
    """An alarm-check crash is swallowed — maintenance must not take down the
    sweep; goals still tick and the owner isn't spammed with a stack trace."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    class ExplodingAlertEngine(FakeEngine):
        def check_db_size_alert(self) -> "str | None":
            raise RuntimeError("stat failed")

    evaluator, notifier = FakeClaude(), RecordingNotifier()

    out = await tick_all(
        store=store, engine=ExplodingAlertEngine(), evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert out["g"] is Outcome.IDLE    # the sweep survived the alarm crash


# ---- human-facing renderings show the objective, never the advance brief (#550)


@pytest.mark.asyncio
async def test_goal_next_and_log_show_objective_never_advance_brief(tmp_path):
    """#550 named regression — the display half of the #547 class. The
    thin-advance brief is dispatch plumbing: after an advance dispatch, every
    human-facing projection of it — ``status.next`` (→ get_goal / console /
    STATUS.md) and the goal-log head — renders the goal's OBJECTIVE, while the
    WORKER still receives the full brief verbatim."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")   # objective: "Drive the demo repo to done."
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    out = await _tick(store, "g", evaluator, engine, notifier)

    assert out is Outcome.DISPATCHED
    # Machine-facing dispatch plumbing is untouched: the worker gets the brief.
    action, _, _ = engine.dispatched[0]
    assert action.goal.startswith("Advance this goal by one substantive")
    # Human-facing: `next` and the log head carry the objective, never the brief.
    status = store.load_status("g")
    assert status.next == "Drive the demo repo to done."
    log = store.recent_log("g", n=10)
    assert "dispatched implement_feature: Drive the demo repo to done." in log
    assert "Advance this goal" not in log


@pytest.mark.asyncio
async def test_delivery_record_and_labels_show_objective_never_advance_brief(tmp_path):
    """#550: on settle, the delivery record (deliveries.md / tail_goal — and,
    via the re-stamped ref's label, the trace rows RUN_SUMMARY projects) names
    the goal's objective, never the raw advance brief."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))
    evaluator, notifier = FakeClaude(), RecordingNotifier()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="ok", gate_passed=True,
    ))

    await _tick(store, "g", evaluator, engine, notifier)   # dispatch the advance
    await _tick(store, "g", evaluator, engine, notifier)   # settle it

    deliveries = store.recent_deliveries("g")
    assert "Drive the demo repo to done." in deliveries
    assert "Advance this goal" not in deliveries


@pytest.mark.asyncio
async def test_done_gate_off_track_below_cap_counts_the_round(tmp_path):
    """Each non-closing done-gate round increments the persisted counter —
    the churn brake's bookkeeping."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "off_track", "rationale": "clause 2 unmet",
        "corrections": ["[clause 2] add the parity test"],
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review text"))
    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())
    assert out is Outcome.SLEPT
    s = store.load_status("g")
    assert s.phase == "idle"
    assert s.donegate_rounds == 1


@pytest.mark.asyncio
async def test_done_gate_round_that_beats_the_best_clause_count_restarts_the_churn_counter(tmp_path):
    """Progress-aware brake (2026-09-02): a refusal is churn only when the
    satisfied-clause count is FLAT. devclaw-030 parked at 14/15 after three
    refusals while the count rose every round — the loop was winning and the
    brake could not tell. A round that beats the persisted best resets the
    counter to 1 and persists the new best; it never parks."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="verifying",
        donegate_rounds=DONEGATE_ROUND_CAP - 1, donegate_progress=1,
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "off_track", "rationale": "clause 3 unmet",
        "corrections": ["[clause 3] wire the scan to the registry"],
        "clauses": [
            {"clause": "clause 1", "satisfied": True, "evidence": "a.py:1"},
            {"clause": "clause 2", "satisfied": True, "evidence": "b.py:2"},
            {"clause": "clause 3", "satisfied": False, "evidence": "missing — should live in tick.py"},
        ],
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review text"))
    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())
    assert out is Outcome.SLEPT           # kept going — NOT parked at the cap
    s = store.load_status("g")
    assert s.phase == "idle"
    assert s.blocked_kind != "donegate_churn"
    assert s.donegate_rounds == 1          # restarted, not cap-1 + 1
    assert s.donegate_progress == 2        # the new best persists


@pytest.mark.asyncio
async def test_one_shot_done_gate_off_track_rides_the_advance_loop_with_the_churn_brake(tmp_path):
    """Named regression (round-2 amputation): a one_shot goal's non-achieved
    done-gate rides the SAME path as long_lived — both modes share ONE
    execution path (spec 008), so "keep going" is meaningful for one_shot
    too: the corrections steer the next advance, and runaway rounds are the
    churn brake's job (DONEGATE_ROUND_CAP), not a block-on-first-refusal.
    The goal lands idle with the round counted and the correction in unread
    steering — NOT blocked."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", mode="one_shot")
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "off_track", "rationale": "clause 2 unmet",
        "corrections": ["[clause 2] add the parity test"],
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review text"))
    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())
    assert out is Outcome.SLEPT
    s = store.load_status("g")
    assert s.phase == "idle"
    assert s.donegate_rounds == 1
    assert "[clause 2] add the parity test" in store.unread_steering("g")


@pytest.mark.asyncio
async def test_done_gate_churn_brake_parks_after_cap_rounds(tmp_path):
    """Named regression (2026-08-19 fs-book-figures night): four consecutive
    done-gate refusals re-advanced the same goal all night; only the 05:00 run
    window stopped it. At DONEGATE_ROUND_CAP consecutive non-closing rounds
    the goal parks for the owner (human-gated: donegate_churn is not a
    mechanical:* kind, so no auto-heal touches it) with the full verdict in
    blocked_on."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # FLAT progress: the best seen is 1 and this round reports 1 again — the
    # treadmill the brake was built for. A rising count is covered by
    # test_done_gate_round_that_beats_the_best_clause_count_restarts_the_churn_counter.
    store.save_status("g", GoalStatus(
        phase="verifying", donegate_rounds=DONEGATE_ROUND_CAP - 1, donegate_progress=1,
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify", is_done_check=True),
    ))
    evaluator = FakeClaude(json.dumps({
        "verdict": "off_track", "rationale": "clause 2 unmet",
        "corrections": ["[clause 2] add the parity test"],
        "clauses": [
            {"clause": "clause 1", "satisfied": True, "evidence": "a.py:1"},
            {"clause": "clause 2", "satisfied": False, "evidence": "missing — should live in b.py"},
        ],
    }))
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="review text"))
    notifier = RecordingNotifier()
    out = await _tick(store, "g", evaluator, engine, notifier)
    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.phase == "blocked"
    assert s.blocked_kind == "donegate_churn"
    # spec 031: the park carries a typed Problem; blocked_on is its summary
    assert s.problem_id and "donegate_churn" in (s.blocked_on or "")
    assert store.current_problem("g").raised_by == "churn_park"
    # the correction still lands as steering so a resumed goal has direction
    assert "[clause 2] add the parity test" in store.unread_steering("g")


# ---- spec 031: typed Problems, two verbs, the timebox ----------------------

def _open_problem(store, goal_id, *, timebox_ms, default_key="correct", closes=False, now_ms=1_000_000):
    """Seed a blocked goal carrying one OPEN Problem (the shape raise_problem
    writes), returning the Problem. Pure store writes — no tick."""
    from devclaw.goal import problems as _pb
    opts = _pb.CHURN_OPTIONS if not closes else (_pb.ACCEPT_CLOSE, _pb.CORRECT)
    p = _pb.new_problem(
        goal_id, kind="needs_answer", raised_by="done_gate",
        what="clause 3 is undecided", clause="clause 3", why="two readings",
        options=opts, default_key=default_key, timebox_s=1, now_ms=now_ms,
    )
    p = replace(p, timebox_at=timebox_ms)
    store.raise_problem(p)
    return p


@pytest.mark.asyncio
async def test_blocked_goal_with_open_problem_costs_zero_cognition(tmp_path):
    """Zero-token idle (constitution III): a goal blocked with an OPEN Problem
    whose timebox has not elapsed ticks with no cognition and no dispatch —
    the timebox check is a timestamp compare, nothing more."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    p = _open_problem(store, "g", timebox_ms=10**15)  # far future
    store.save_status("g", GoalStatus(
        phase="blocked", blocked_on="needs_answer: see problem", blocked_kind="needs_answer",
        problem_id=p.id,
    ))
    evaluator, engine = FakeClaude(), FakeEngine()
    await _tick(store, "g", evaluator, engine, RecordingNotifier())
    assert evaluator.calls == 0
    assert engine.dispatched == []
    s = store.load_status("g")
    assert s.phase == "blocked" and s.problem_id == p.id


@pytest.mark.asyncio
async def test_timebox_default_applies_without_cognition(tmp_path):
    """Spec 031 FR-005: at the timebox the tick applies the default as a
    Decision (provenance=defaulted), UNBLOCKs, notifies once — zero cognition."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    p = _open_problem(store, "g", timebox_ms=0)  # already elapsed
    store.save_status("g", GoalStatus(
        phase="blocked", blocked_on="needs_answer: see problem", blocked_kind="needs_answer",
        problem_id=p.id, actions_dispatched=2, donegate_rounds=2,
    ))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()
    await _tick(store, "g", evaluator, engine, notifier)
    assert evaluator.calls == 0
    s = store.load_status("g")
    assert s.phase != "blocked" and s.problem_id == ""
    assert s.donegate_rounds == 0                       # the vouch shape
    assert len(engine.dispatched) == 1                  # …and it kept moving on this tick
    decs = store.decisions("g")
    assert len(decs) == 1 and decs[0].provenance == "defaulted" and decs[0].option_key == "correct"
    assert store.problem_by_id(p.id).status == "defaulted"
    assert sum("defaulted" in m for m in notifier.sent) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("strictness,expect_blocked,expect_decisions", [
    ("trust", False, 1),   # the default acts; the done-gate closes on its next round
    ("strict", True, 0),   # the timeout parks; only an explicit decide can close
])
async def test_defaulted_accept_and_close_never_emits_achieve(tmp_path, strictness, expect_blocked, expect_decisions):
    """Q2 → C and constitution V: a defaulted accept_close never reaches
    `done` from the tick — under trust the goal returns to idle for the
    done-gate's single ACHIEVE emitter; under strict it stays blocked with
    no Decision written."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    p = _open_problem(store, "g", timebox_ms=0, default_key="accept_close", closes=True)
    store.set_strictness("g", strictness)
    store.save_status("g", GoalStatus(
        phase="blocked", blocked_on="churn: see problem", blocked_kind="donegate_churn",
        problem_id=p.id,
    ))
    notifier = RecordingNotifier()
    await _tick(store, "g", FakeClaude(), FakeEngine(), notifier)
    s = store.load_status("g")
    assert (s.phase == "blocked") is expect_blocked
    assert s.phase != "done"
    assert len(store.decisions("g")) == expect_decisions
    assert len(notifier.sent) >= 1


@pytest.mark.asyncio
async def test_steer_goal_is_refused_while_a_problem_is_open(tmp_path):
    """Q1 → A: prose steering is refused while a Problem is open — the
    refusal carries the Problem and the two verbs, and writes nothing."""
    svc, db, goals_dir = _resume_service(tmp_path)
    try:
        seed_goal(goals_dir, "g")
        store = svc._goal_store
        p = _open_problem(store, "g", timebox_ms=10**15)
        store.save_status("g", GoalStatus(
            phase="blocked", blocked_on="needs_answer: see problem", blocked_kind="needs_answer",
            problem_id=p.id,
        ))
        before = store.load_status("g")
        with pytest.raises(ValueError) as ei:
            svc.steer_goal("g", "just do X")
        msg = str(ei.value)
        assert p.id in msg and "correct_implementation" in msg and "decide" in msg
        after = store.load_status("g")
        assert after.version == before.version and after.phase == "blocked"
        assert not store.unread_steering("g")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_worker_honest_block_raises_a_problem_without_burning_the_cap(tmp_path):
    """Spec 031 US1 (R4): a failed settle whose detail carries the worker's
    honest BLOCK raises a Problem immediately — blocked with the fixed
    worker-block options, dispatch count unchanged — instead of spending two
    more dispatches to park at the cap."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight", actions_dispatched=1,
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "do X"),
    ))
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed",
        detail="Error:\nworker reported BLOCKED: needs a Telegram credential the sandbox lacks — the worker reports it cannot complete",
    ))
    notifier = RecordingNotifier()
    out = await _tick(store, "g", FakeClaude(), engine, notifier)
    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.phase == "blocked" and s.blocked_kind == "needs_answer"
    assert s.problem_id and s.actions_dispatched == 1
    p = store.current_problem("g")
    assert p is not None and p.raised_by == "worker_block"
    assert {o.key for o in p.options} == {"correct", "supply", "cancel"}
    assert "Telegram" in p.what
    assert any("correct_implementation" in m and "decide" in m for m in notifier.sent)
    assert not any("steer_goal" in m for m in notifier.sent)
    # T044: the block named a capability the admission lint should have refused
    # ("Telegram") — the MISS is recorded in the problems catalog so the class
    # gets fixed, not the instance. This assertion is what caught the silent
    # AttributeError the try/except around it used to swallow.
    rows = store._state.list_problems(category="admission")
    assert any(r.get("kind") == "lint_miss" for r in rows), rows


# ---- spec 031 US3: the done_when admission lint -----------------------------

@pytest.mark.parametrize("done_when,expect", [
    # (a) sandbox-impossible → refused (the 2026-09-02 issue-414 shape)
    ("A real Telegram brief is sent and sanity-checked against browser Ledger numbers.", "refuse"),
    ("The job runs with the production credentials and posts to Slack.", "refuse"),
    # (b) baseline-less absolute → rewritten (the issue-443 shape)
    ("All tests pass (dotnet test backend/FinanceSentry.sln).", "rewrite"),
    ("Zero lint warnings in the frontend.", "rewrite"),
    # already baselined / decided → admitted unchanged
    ("No new failures relative to main after the change.", "ok"),
    ("The capability scan holds a goal whose project has a red probe on record.", "ok"),
    ("Committed share of outflow over the last 3 complete months is shown on the dashboard.", "ok"),
])
def test_admission_lint_catches_the_three_classes(done_when, expect):
    """Spec 031 US3 / SC-004: classes (a) and (b) are mechanical and
    deterministic; a decided contract admits unchanged. The four 2026-09-02
    contracts replay as the first two rows of each kind."""
    from devclaw.goal import admission_lint as L
    r = L.lint_mechanical(done_when)
    if expect == "refuse":
        assert r.refused and "sandbox cannot provide" in L.refusal_message(r)
    elif expect == "rewrite":
        assert not r.refused and r.rewrites and "no new failures relative to the default branch" in r.done_when
    else:
        assert not r.refused and not r.rewrites and r.done_when == done_when


@pytest.mark.asyncio
async def test_admission_refusal_persists_nothing_and_undecided_raises_a_problem(tmp_path, monkeypatch):
    """(a) refuses with nothing persisted; (c) admits the goal BLOCKED with an
    admission Problem before any dispatch, and a tick over it costs zero
    cognition (constitution III — the lint's one call was at creation)."""
    from devclaw.goal import service as _svc
    svc, db, goals_dir = _resume_service(tmp_path)
    try:
        store = svc._goal_store
        ws = tmp_path / "ws"; ws.mkdir()
        # (a) refusal — nothing persisted
        with pytest.raises(ValueError) as ei:
            await svc.create_goal_async(
                "g-refused", objective="x", workspace_dir=str(ws), out_of_scope=[], invariants=[], established=[], backlog=["one step"],
                done_when="A real Telegram brief is sent.",
            )
        assert "sandbox cannot provide" in str(ei.value)
        assert not store.exists("g-refused")
        # (c) undecided — judged by the patched module-global, raised as a Problem
        async def fake_judge(done_when, caller):
            return (L.Undecided(clause="the scan sources from somewhere", choice="registry or live workspaces?",
                                options=("walk the project registry", "walk live workspaces")),), ""
        from devclaw.goal import admission_lint as L
        monkeypatch.setattr(_svc, "_judge_undecided", fake_judge)
        out = await svc.create_goal_async(
            "g-undecided", objective="x", workspace_dir=str(ws), out_of_scope=[], invariants=[], established=[], backlog=["one step"],
            done_when="The scan sources from somewhere and holds red projects.",
        )
        assert out["admission"]["problem"]["raised_by"] == "admission_lint"
        s = store.load_status("g-undecided")
        assert s.phase == "blocked" and s.problem_id
        p = store.current_problem("g-undecided")
        assert p is not None and p.kind == "admission" and len(p.options) == 2
        # zero-token: the admitted-but-blocked goal ticks with no cognition
        ev, eng = FakeClaude(), FakeEngine()
        await _tick(store, "g-undecided", ev, eng, RecordingNotifier())
        assert ev.calls == 0 and eng.dispatched == []
    finally:
        db.close()


@pytest.mark.asyncio
async def test_admission_rewrite_is_recorded_as_a_decision(tmp_path, monkeypatch):
    """(b): the rewrite lands in the contract AND as an admission Decision that
    the feed-forward will carry to the worker and the gate (US4)."""
    from devclaw.goal import service as _svc
    svc, db, goals_dir = _resume_service(tmp_path)
    try:
        async def no_undecided(done_when, caller):
            return (), ""
        monkeypatch.setattr(_svc, "_judge_undecided", no_undecided)
        ws = tmp_path / "ws"; ws.mkdir()
        out = await svc.create_goal_async(
            "g-rewrite", objective="x", workspace_dir=str(ws), out_of_scope=[], invariants=[], established=[], backlog=["one step"],
            done_when="All tests pass.",
        )
        assert out["admission"]["rewrites"][0]["to"].startswith("no new failures relative")
        decs = svc._goal_store.decisions("g-rewrite")
        assert len(decs) == 1 and decs[0].provenance == "admission"
        assert svc._goal_store.load_status("g-rewrite").phase != "blocked"
    finally:
        db.close()


@pytest.mark.asyncio
async def test_resume_goal_resets_the_donegate_round_count(tmp_path):
    """A human vouching for a parked goal (resume/steer) restores the full
    round budget — same contract as heal_attempts."""
    svc, db, goals_dir = _resume_service(tmp_path)
    try:
        seed_goal(goals_dir, "g")
        store = svc._goal_store
        store.save_status("g", GoalStatus(
            phase="blocked", blocked_on="done-gate churn brake: parked",
            donegate_rounds=DONEGATE_ROUND_CAP, donegate_progress=14,
        ))
        svc.resume_goal("g")
        s = store.load_status("g")
        assert s.donegate_rounds == 0
        assert s.donegate_progress == 0   # the vouch restores the FULL budget
    finally:
        db.close()
async def test_failed_settle_reason_rides_the_next_advance_brief(tmp_path):
    """Named regression (2026-08-19 night run): a terminal task failure was
    collapsed to bool(finished_detail) — the 3h context-overflow and the 1h
    wall-clock burns were each followed by a byte-identical advance brief.
    The next session must SEE the terminal reason so it can adapt (smaller
    slice), and the dispatch log must not render the steered/failure-context
    re-dispatch identically to a blind one."""
    from devclaw.advance_brief import FAILURE_CONTEXT_MARKER

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=0,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance"),
        ),
    )
    reason = (
        "Prompt is too long — the worker conversation overflowed the model "
        "context. Re-dispatch this task with a smaller scope."
    )
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="failed", detail=reason))
    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert FAILURE_CONTEXT_MARKER in action.goal
    assert "overflowed the model context" in action.goal


@pytest.mark.asyncio
async def test_steering_only_advance_brief_carries_no_failure_context(tmp_path):
    """The absence pin: an advance triggered by owner steering alone must not
    grow a failure-context section."""
    from devclaw.advance_brief import FAILURE_CONTEXT_MARKER

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle"))
    store.append_steering("g", ["try the other API"], source="owner")
    engine = FakeEngine()
    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert FAILURE_CONTEXT_MARKER not in action.goal
    assert "try the other API" in action.goal


# ---- the saga feed-forward (spec 012 US1) ---------------------------------


@pytest.mark.asyncio
async def test_advance_brief_carries_prior_increments_after_a_settled_delivery(tmp_path):
    """US1 end-to-end: once an increment has settled, the NEXT increment's
    brief states its position and what that increment delivered — outcome and
    verdict — so the work is not re-implemented (the observed cost this story
    exists to remove)."""
    from devclaw.advance_brief import PRIOR_INCREMENTS_MARKER

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # An increment settles: delivery row + settlement row, exactly as
    # tick_settle writes them.
    store.record_settlement("g", ref_id="t1", ref_kind="task", status="done")
    store.append_delivery(
        "g", "add the /health endpoint",
        "PR: https://github.com/o/r/pull/7\n\n"
        "Agent summary:\nI did the thing and it works great.\n\n"
        "Verify gate `pytest -q`: PASSED\n",
        ref_id="t1",
    )
    store.save_status("g", GoalStatus(phase="idle"))
    store.append_steering("g", ["keep going"], source="owner")
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert PRIOR_INCREMENTS_MARKER in action.goal
    assert "This is increment 2 of this goal" in action.goal
    assert "add the /health endpoint" in action.goal
    assert "status=done" in action.goal
    assert "gate=PASSED" in action.goal
    # #358: the previous worker's self-report must NOT become this worker's
    # premise — only devclaw's own settlement facts cross the channel.
    assert "it works great" not in action.goal


@pytest.mark.asyncio
async def test_decisions_marker_present_and_capped(tmp_path):
    """Spec 031 US4 structural guard (beside the prior-increments guard): the
    section's head line is DECISIONS_MARKER, superseded Decisions are absent,
    the entry list caps under the budget with the marker never truncated, and
    the next dispatch's brief carries it."""
    from devclaw.advance_brief import DECISIONS_MARKER
    from devclaw.goal import decisions as _dec
    from devclaw.goal.models import Decision as _D
    from devclaw.goal.prompt_budget import DECISIONS_KEEP, DECISIONS_TRUNCATION_MARKER

    rows = [_D(id=f"dec_{i}", goal_id="g", problem_id="", clause=f"clause {i}", verb="decide",
               option_key="correct", text="x" * 300, provenance="owner", made_by="denys",
               made_at=1_000 + i, superseded_by=("dec_9" if i == 0 else ""))
            for i in range(40)]
    text = _dec.render(rows)
    assert text.startswith(DECISIONS_MARKER)
    assert "clause 0" not in text                      # superseded → absent
    assert DECISIONS_TRUNCATION_MARKER in text          # capped
    assert len(text.split("\n", 2)[2]) <= DECISIONS_KEEP + len(DECISIONS_TRUNCATION_MARKER) + 8
    assert _dec.render([]) == ""                        # absence needs no statement

    # end-to-end: a recorded Decision reaches the next brief as fact
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.record_decision(_D(id="dec_live", goal_id="g", problem_id="", clause="clause 7",
                             verb="decide", option_key="accept_close", text="",
                             provenance="owner", made_by="denys", made_at=5))
    store.save_status("g", GoalStatus(phase="idle"))
    store.append_steering("g", ["keep going"], source="owner")
    engine = FakeEngine()
    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())
    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert DECISIONS_MARKER in action.goal and "clause 7" in action.goal


@pytest.mark.asyncio
async def test_first_advance_brief_states_no_prior_increments(tmp_path):
    """FR-004: with nothing settled, the absence is stated, not omitted."""
    from devclaw.advance_brief import PRIOR_INCREMENTS_MARKER

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle"))
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert PRIOR_INCREMENTS_MARKER in action.goal
    assert "this is the first" in action.goal.lower()


@pytest.mark.asyncio
async def test_idle_tick_performs_no_increment_record_read(tmp_path, monkeypatch):
    """Constitution III / SC-007: the feed-forward read sits BELOW the
    should_plan gate. An idle tick spends no cognition AND performs no delivery
    read — the zero-token idle guard covers I/O, not just LLM calls."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    reads: list[str] = []
    real = type(store).increment_records
    monkeypatch.setattr(
        type(store), "increment_records",
        lambda self, goal_id: (reads.append(goal_id), real(self, goal_id))[1],
    )
    evaluator, engine = FakeClaude(), FakeEngine()

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())

    assert out is Outcome.IDLE
    assert evaluator.calls == 0
    assert reads == []
    assert engine.dispatched == []
# ---- the authored saga framing (spec 012 US2) ------------------------------


@pytest.mark.asyncio
async def test_advance_brief_carries_the_authored_saga_slots(tmp_path):
    """FR-007/FR-009a: the framing a worker receives is the authored schema,
    re-sent in full — a fresh sandbox has no memory, so a pointer would be a
    request while a slot is a fact."""
    from devclaw.goal import saga_framing as sf

    store = _store(tmp_path, Clock())
    seed_goal(
        tmp_path, "g",
        out_of_scope=["the mobile client"],
        invariants=["the existing test suite stays green"],
        established=["Postgres is the datastore — decided, do not revisit"],
    )
    store.save_status("g", GoalStatus(phase="idle"))
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert sf.OUT_OF_SCOPE_LABEL in action.goal
    assert "- the mobile client" in action.goal
    assert "- the existing test suite stays green" in action.goal
    assert "Postgres is the datastore" in action.goal


@pytest.mark.asyncio
async def test_advance_brief_for_a_goal_authored_before_the_slot_schema_is_unchanged(tmp_path):
    """The live-goal regression: goals already running on the VPS were authored
    as prose, and their goal.yaml has no slot keys. Their brief must not grow
    three sections claiming nothing is excluded — that would be devclaw putting
    words in the author's mouth."""
    from devclaw.goal import saga_framing as sf

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")  # no slot keys — the pre-US2 goal.yaml shape
    store.save_status("g", GoalStatus(phase="idle"))
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    # The framing is exactly what it always was...
    assert "Goal: Drive the demo repo to done." in action.goal
    assert "Done when: all backlog items merged" in action.goal
    # ...and NOT one slot section more.
    for label in (sf.OUT_OF_SCOPE_LABEL, sf.INVARIANTS_LABEL, sf.ESTABLISHED_LABEL):
        assert label not in action.goal


@pytest.mark.asyncio
async def test_idle_tick_composes_no_saga_framing(tmp_path, monkeypatch):
    """Constitution III / SC-007: US2 adds no per-tick work. Framing is
    composed on the dispatch path only, so an idle tick spends no cognition and
    renders nothing."""
    from devclaw.goal import saga_framing as sf

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d", out_of_scope=[], invariants=[], established=[])
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))

    rendered: list[object] = []
    real = sf.render
    monkeypatch.setattr(
        "devclaw.goal.tick._saga_framing.render",
        lambda goal: (rendered.append(goal.id), real(goal))[1],
    )
    evaluator, engine = FakeClaude(), FakeEngine()

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier())

    assert out is Outcome.IDLE
    assert evaluator.calls == 0
    assert rendered == []
    assert engine.dispatched == []


# ---- the single-writer project hold (spec 010 P1) --------------------------


def _seed_dated(store, tmp_path, goal_id, *, workspace="/repos/demo", created_at_ms, phase="idle"):
    """Seed a goal with an EXPLICIT creation timestamp — the age the project
    hold orders by. `append_log` stamps wall-clock, so two seeds in one test can
    share a millisecond and reduce an age-ordered assertion to the id tie-break."""
    seed_goal(tmp_path, goal_id, workspace_dir=workspace)
    store.save_status(goal_id, GoalStatus(phase=phase, lifecycle="executing"))
    store._goal_state.append_log_row(goal_id, "- [t] goal created", created_at_ms)


@pytest.mark.asyncio
async def test_second_goal_on_one_project_is_queued_and_dispatches_nothing(tmp_path):
    """Acceptance 1 (FR-001/FR-002): two goals, one project — only the holder
    dispatches. This is the #553 class closed by construction: the second goal
    never reaches planning while the first holds the repo."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "holder", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "waiter", created_at_ms=2_000)
    engine = FakeEngine()

    held = await _tick(store, "holder", FakeClaude(), engine, RecordingNotifier())
    queued = await _tick(store, "waiter", FakeClaude(), engine, RecordingNotifier())

    assert held is Outcome.DISPATCHED
    assert queued is Outcome.QUEUED
    assert [a.goal for a, _g, _u in engine.dispatched] and len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_goal_on_another_project_dispatches_while_one_project_is_held(tmp_path):
    """Acceptance 2 / SC-005: the hold is per-project, never global — N goals on
    N distinct projects dispatch exactly as before this spec."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "alpha", workspace="/repos/alpha", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "alpha2", workspace="/repos/alpha", created_at_ms=2_000)
    _seed_dated(store, tmp_path, "beta", workspace="/repos/beta", created_at_ms=3_000)
    engine = FakeEngine()

    assert await _tick(store, "alpha", FakeClaude(), engine, RecordingNotifier()) is Outcome.DISPATCHED
    assert await _tick(store, "alpha2", FakeClaude(), engine, RecordingNotifier()) is Outcome.QUEUED
    assert await _tick(store, "beta", FakeClaude(), engine, RecordingNotifier()) is Outcome.DISPATCHED


@pytest.mark.asyncio
async def test_blocked_goal_releases_project_lane_for_queued_successor(tmp_path):
    """Spec 025 FR-015 (skip-over, reversing spec 010 FR-008's blocked-holder
    ruling): a parked goal must not idle its project lane — the queued
    successor starts, the parked goal waits for the operator. The 2026-08-28
    night pinned the cost of the old behavior: one OOM-blocked goal idled the
    devclaw lane for 14 hours with a healthy successor queued behind it."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "parked", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "waiter", created_at_ms=2_000)
    store.save_status("parked", GoalStatus(
        phase="blocked", lifecycle="executing",
        blocked_on="sandbox OOM at cap", blocked_kind="mechanical:env_cap",
    ))
    engine = FakeEngine()
    evaluator = FakeClaude()

    out = await _tick(store, "waiter", evaluator, engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1
    # the parked goal stays parked at zero cognition (its tick reads QUEUED —
    # the successor now holds the lane — and its status stays blocked)
    assert await _tick(store, "parked", evaluator, engine, RecordingNotifier()) is Outcome.QUEUED
    assert store.load_status("parked").phase == "blocked"
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_idle_head_with_nothing_to_do_releases_lane_to_runnable_successor(tmp_path):
    """Runnable-head rule (owner ruling 2026-09-01, generalizing spec 025's
    blocked skip-over): a head that cannot act this sweep — idle, nothing in
    flight, no unread steering, cadence not due — must not strand runnable
    successors. On 2026-08-31 one such head held the finance-sentry lane a
    whole night with 7 runnable goals queued behind it."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "head", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "succ", created_at_ms=2_000)
    # head just planned: cadence (1d) freshly stamped, nothing else pending
    store.save_status("head", GoalStatus(
        phase="idle", lifecycle="executing", last_plan_at=store.now_iso(),
    ))
    engine = FakeEngine()

    out = await _tick(store, "succ", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_runnable_again_head_reclaims_lane_from_idle_successor(tmp_path):
    """The reclaim half of the runnable-head rule: steering arriving on the
    skipped head makes it runnable, and — with no successor work in flight —
    age names it holder again. (With successor work IN flight the existing
    in-flight-outranks-age rule keeps the lane on the successor; that facet
    is pinned by test_resumed_predecessor_waits_while_successor_task_is_in_flight.)"""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "head", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "succ", created_at_ms=2_000)
    store.save_status("head", GoalStatus(
        phase="idle", lifecycle="executing", last_plan_at=store.now_iso(),
    ))
    store.append_steering("head", ["operator: change course"], source="denys")

    out = await _tick(store, "succ", FakeClaude(), engine := FakeEngine(), RecordingNotifier())

    assert out is Outcome.QUEUED
    assert engine.dispatched == []


@pytest.mark.asyncio
async def test_resumed_predecessor_waits_while_successor_task_is_in_flight(tmp_path):
    """The skip-over trap: resuming a parked predecessor while its skip-over
    successor is MID-TASK must not hand the lane back by age — that would
    dispatch a second writer against a workspace with a live task in it
    (the #553/#722 class). In-flight work outranks age in the holder
    derivation."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "resumed", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "successor", created_at_ms=2_000)
    store.save_status("successor", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "work"),
    ))
    engine = FakeEngine()

    out = await _tick(store, "resumed", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.QUEUED
    assert engine.dispatched == []


@pytest.mark.asyncio
async def test_queued_goal_starts_automatically_after_the_holder_goes_terminal(tmp_path):
    """Acceptance 3 / SC-003: no operator action, no cognition spent on the
    handover — the derivation simply names a different goal once the holder is
    terminal. Nothing is 'released', because nothing was ever acquired."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "holder", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "waiter", created_at_ms=2_000)
    engine = FakeEngine()

    assert await _tick(store, "waiter", FakeClaude(), engine, RecordingNotifier()) is Outcome.QUEUED

    store.save_status("holder", GoalStatus(phase="done", lifecycle="executing"))

    assert await _tick(store, "waiter", FakeClaude(), engine, RecordingNotifier()) is Outcome.DISPATCHED


@pytest.mark.asyncio
async def test_queued_goal_tick_spends_zero_cognition_and_does_not_churn_state(tmp_path):
    """FR-004 / SC-004: the hold check is a cheap local read placed BEFORE any
    cognition and before the steering read (which lazily ingests, and therefore
    writes).

    Zero cognition is the requirement. The stronger property worth pinning
    alongside it is that a queued goal does not CHURN: this path runs every
    heartbeat for as long as the holder works, so a per-tick state write or log
    append would bury the goal's real history under queue noise. The first tick
    of any goal seeds the no-progress watchdog (column-only telemetry, unrelated
    to the hold), so churn is measured from the second tick on."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "holder", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "waiter", created_at_ms=2_000)
    evaluator, engine = FakeClaude(), FakeEngine()

    first = await _tick(store, "waiter", evaluator, engine, RecordingNotifier())
    settled_version = store.load_status("waiter").version
    settled_log = store.recent_log("waiter", n=50)

    second = await _tick(store, "waiter", evaluator, engine, RecordingNotifier())
    third = await _tick(store, "waiter", evaluator, engine, RecordingNotifier())

    assert first is second is third is Outcome.QUEUED
    assert evaluator.calls == 0
    assert engine.dispatched == []
    assert store.load_status("waiter").version == settled_version
    assert store.recent_log("waiter", n=50) == settled_log
    assert store.load_status("waiter").phase == "idle"  # queued is not blocked


@pytest.mark.asyncio
async def test_queued_goal_still_settles_in_flight_work(tmp_path):
    """The upgrade edge case: a goal that already had work in flight when the
    hold shipped must finish settling it — nothing orphaned — and then dispatch
    nothing further. The gate sits BELOW the settle path for exactly this."""
    store = _store(tmp_path, Clock())
    _seed_dated(store, tmp_path, "holder", created_at_ms=1_000)
    _seed_dated(store, tmp_path, "waiter", created_at_ms=2_000)
    store.save_status("waiter", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed", detail="agent died",
    ))

    out = await _tick(store, "waiter", FakeClaude(), engine, RecordingNotifier())

    assert store.is_settled("waiter", "t1")          # settled, not orphaned
    assert engine.dispatched == []                    # but nothing new dispatched
    assert out is Outcome.QUEUED


# ---- spec 012 FR-012a: the completion judgement is never bypassed ----------
# US3 records how BIG a work item is. The temptation that creates is exactly
# "expected_increments == 1 and the tests are green, so we're done". FR-012a
# forbids it: mechanical verification passing is never sufficient evidence of
# completion, at any size. These two tests are the guard rail — one drives the
# real tick, one closes the door structurally so a future shortcut cannot be
# added quietly.


@pytest.mark.asyncio
async def test_a_single_increment_work_item_still_faces_the_completion_judgement(
    tmp_path,
):
    """FR-012a / FR-012: a work item its filer sized at ONE increment, whose one
    increment settles green with the sandbox verify gate PASSED and a PR pushed,
    is NOT done. It proposes done, the done-gate reviews the repository against
    ``done_when``, and only an ``achieved`` verdict closes it — an ``off_track``
    verdict sends it back round exactly like a multi-increment saga.

    Discriminates: a size-based shortcut ("one increment + green gate ⇒ close")
    would close the goal at the settle or survive the off_track round, and both
    assertions below would fail.
    """
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # the whole work item is one unit of work — the smallest thing a saga can be
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "delete the dead flag"),
    ))
    evaluator = FakeClaude()
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="Agent summary: flag deleted",
        pr_url="https://github.com/o/r/pull/9", gate_passed=True,
    ))

    settled = await _tick(store, "g", evaluator, engine, RecordingNotifier())

    # 1. green mechanical verification settles the INCREMENT, never the goal
    assert settled is Outcome.VERIFYING
    assert store.load_status("g").phase != "done"
    assert any(a.tool == "review_repository" for a, _g, _u in engine.dispatched)
    assert evaluator.calls == 0          # the settle itself judges nothing

    # 2. the done-gate says off_track — still not done, however small the item
    off_track = FakeClaude(json.dumps({
        "verdict": "off_track",
        "rationale": "the flag's call sites are still wired",
        "corrections": ["unwire the call sites too"],
    }))
    reviewed = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="call sites still reference the flag",
    ))
    out = await _tick(store, "g", off_track, reviewed, RecordingNotifier())
    # not closed. (It routes to the #430 open-unmerged-PR block rather than
    # straight back to idle, because the increment left a PR open — a different
    # non-close, and still a non-close: what FR-012a forbids is the CLOSE.)
    assert out is not Outcome.DONE
    assert store.load_status("g").phase != "done"

    # 3. only the grounded achieved verdict closes it
    store.save_status("g", replace(
        store.load_status("g"),
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev2", "task", "verify", is_done_check=True),
    ))
    achieved = FakeClaude(json.dumps({
        "verdict": "achieved",
        "rationale": "the flag and every call site are gone",
        "clauses": [{"clause": "flag removed", "satisfied": True, "evidence": "src/Flags.cs deleted"}],
    }))
    closed = await _tick(
        store, "g", achieved,
        FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="nothing references the flag")),
        RecordingNotifier(),
    )
    assert closed is Outcome.DONE
    assert store.load_status("g").phase == "done"


def test_green_mechanical_verification_alone_never_closes_a_goal():
    """FR-012a, structurally: there is exactly ONE way for a goal to reach
    ``done``, and it runs through the grounded completion judgement.

    WHY the bypass is unreachable, in three links:

    1. ``State.DONE`` appears in the ``LEGAL`` transition table only ever as the
       target of ``Event.ACHIEVE``. No other event can land a goal on done —
       ``ACTION_SETTLED`` (what a green verify/quality gate produces) is only
       permitted to land on ``EXECUTING_IDLE``.
    2. ``Event.ACHIEVE`` is emitted from exactly one production site,
       ``goal/tick_donegate.py``, and ``GoalStore.transition`` rejects any
       phase change that the table does not permit, so the site cannot be
       side-stepped by writing the field directly.
    3. That one site sits behind ``if ev.verdict == "achieved"`` — the
       evaluator's verdict over the read-only ``review_repository`` report,
       judged against ``done_when``.

    Discriminates: adding a second ``Event.ACHIEVE`` emitter (say, a
    "one increment and the gate is green" shortcut in the settle path), or
    widening the table so a settle can land on DONE, or closing without the
    verdict guard, fails one of the three assertions below.
    """
    import pathlib

    from devclaw.goal.transitions import LEGAL, Event, State

    # 1. DONE is only ever the target of ACHIEVE
    done_edges = {
        (frm, evt) for (frm, evt), targets in LEGAL.items() if State.DONE in targets
    }
    assert done_edges and {evt for _frm, evt in done_edges} == {Event.ACHIEVE}
    # ...and a settled action can never be one of them
    for (frm, evt), targets in LEGAL.items():
        if evt is Event.ACTION_SETTLED:
            assert State.DONE not in targets, f"{frm.value} settle can reach done"

    # 2. exactly one production emitter
    root = pathlib.Path(__file__).resolve().parents[1]
    emitters = sorted(
        path.relative_to(root).as_posix()
        for path in list((root / "devclaw").rglob("*.py"))
        + list((root / "runner").rglob("*.py"))
        if "Event.ACHIEVE" in path.read_text(encoding="utf-8")
        and path.name != "transitions.py"  # the table itself, not an emitter
    )
    assert emitters == ["devclaw/goal/tick_donegate.py"]

    # 3. that emitter is guarded by the evaluator's verdict. Since spec 025
    #    there are exactly TWO ACHIEVE transitions in the file, both
    #    verdict-owned: the primary close inside the `achieved` block (now
    #    additionally gated on merge-on-close succeeding — the merge is a
    #    consequence of the verdict, never a bypass of it), and the
    #    pending-merge retry (_finalize_pending_merge), reachable only via
    #    `pending_merge_pr`, which is only ever set on the primary path after
    #    an achieved verdict — the verdict still stands, only the merge is
    #    re-attempted (FR-003).
    src = (root / "devclaw/goal/tick_donegate.py").read_text(encoding="utf-8")
    assert src.count("Event.ACHIEVE,") == 2, "unexpected ACHIEVE emitter count"
    assert 'if ev.verdict == "achieved":' in src, "the close lost its verdict guard"
    guard = (
        'store.transition(\n            goal_id, Event.ACHIEVE,\n'
        '            replace(base, phase="done", next=ev.rationale[:200], donegate_rounds=0, donegate_progress=0,\n'
        '                    pending_merge_pr="", merge_heal_attempted=False),'
    )
    assert guard in src, "the primary ACHIEVE no longer clears the merge marker inside the verdict block"
    assert "async def _finalize_pending_merge" in src and "status.pending_merge_pr" in src, (
        "the retry ACHIEVE lost its pending-merge guard"
    )

    # 4. the US3-specific shortcut: nothing on the execution path may read the
    #    work item's expected increment count. The count is intake-only — it
    #    sizes the plan, it never feeds a completion decision (FR-012).
    sizing_names = (
        "expected_increments",
        "increment_basis",
        "parse_expected_increments",
        "NEEDS_SIZING_LABEL",
    )
    execution_paths = [
        root / "devclaw/goal",
        root / "devclaw/engine",
        root / "devclaw/quality",
        root / "runner",
    ]
    leaks = [
        f"{path.relative_to(root).as_posix()}:{name}"
        for base in execution_paths
        for path in base.rglob("*.py")
        for name in sizing_names
        if name in path.read_text(encoding="utf-8")
    ]
    assert leaks == [], f"execution path reads the expected increment count: {leaks}"


# ---- sandbox OOM environment-cap class (spec 020 US1) ---------------------

_OOM_DETAIL = (
    "sandbox OOM-killed (cap=2g, oom_kill=1): session/prompt failed: Internal "
    "error: The Claude Agent process exited unexpectedly. — the sandbox memory "
    "cap was exhausted and the kernel OOM killer took the agent."
)


@pytest.mark.asyncio
async def test_sandbox_oom_failure_gets_one_adapted_redispatch_with_bounding_advice(tmp_path):
    """Spec 020 FR-002a: the FIRST environment-cap failure re-dispatches ONCE
    with cap-aware bounding advice — never the generic "strictly smaller
    slice" directive, which cannot shrink a test suite."""
    from devclaw.advance_brief import FAILURE_CONTEXT_MARKER

    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=0,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="failed", detail=_OOM_DETAIL))
    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert FAILURE_CONTEXT_MARKER in action.goal
    assert "MEMORY CAP" in action.goal
    assert "DEVCLAW_SANDBOX_MEMORY" in action.goal
    assert "strictly smaller slice" not in action.goal  # wrong advice for this class
    assert store.load_status("g").envcap_redispatches == 1


@pytest.mark.asyncio
async def test_second_sandbox_oom_blocks_with_env_cap_kind_naming_the_cap(tmp_path):
    """FR-002a's other half: the class recurring past the one-adapted-retry
    budget parks the goal with the cap in the reason — resume_goal after
    raising sizing is the recovery verb."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=1, envcap_redispatches=1,
            in_flight=InFlight("devclaw", "implement_feature", "t2", "task", "advance"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="failed", detail=_OOM_DETAIL))
    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.blocked_kind == "mechanical:env_cap"
    assert "sandbox OOM at cap 2g" in st.blocked_on
    assert "resume_goal" in st.blocked_on
    assert engine.dispatched == []  # no third burn


@pytest.mark.asyncio
async def test_productive_settle_resets_envcap_redispatch_budget(tmp_path):
    """A shipped increment proves the environment now fits — the budget
    resets alongside heal_attempts (same stability signal)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=1, envcap_redispatches=1,
            in_flight=InFlight("devclaw", "implement_feature", "t3", "task", "advance"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(terminal=True, status="done", gate_passed=True))
    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())
    assert store.load_status("g").envcap_redispatches == 0


@pytest.mark.asyncio
async def test_dispatch_cap_reason_is_honest_when_nothing_was_delivered(tmp_path):
    """Spec 020 FR-003: "review the open PRs" was a lie when every dispatch
    failed and nothing shipped (the 2026-08-26 shape). With only failed
    increments on record, the cap block says so and carries the last error."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")  # default 2-item backlog → cap = 4
    store.save_status(
        "g", GoalStatus(
            phase="in_flight", actions_dispatched=4,  # at cap already
            in_flight=InFlight("devclaw", "implement_feature", "t4", "task", "advance"),
        ),
    )
    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="failed", detail="Error:\nboom-mechanism died",
    ))
    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())

    assert out is Outcome.BLOCKED
    st = store.load_status("g")
    assert st.blocked_kind == "mechanical:dispatch_cap"
    assert "NO delivered increment" in st.blocked_on
    assert "review the open PRs" not in st.blocked_on
