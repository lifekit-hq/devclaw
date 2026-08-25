"""Dispatch-boundary freshness for referenced goals (spec 019 US1): the
worker brief carries LIVE issue state, closed issues drop out loudly, an
all-closed goal proposes done with zero worker sessions, an unfetchable ref
blocks human-gated, and idle/blocked ticks fetch nothing."""

from __future__ import annotations

import pytest

from devclaw.goal.issue_ref import ISSUE_CONTEXT_MARKER, IssueRefError, IssueSnapshot
from devclaw.goal.models import GoalStatus
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, FakeIssueFetcher, RecordingNotifier,
    fake_prepare, seed_goal,
)


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


async def _tick(store, goal_id, evaluator, engine, notifier, fetcher):
    return await tick_goal(
        goal_id, store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=True, issue_fetcher=fetcher,
    )


def _snap(n, *, state="open", title="t", body="b"):
    return IssueSnapshot(number=n, title=title, body=body, state=state)


@pytest.mark.asyncio
async def test_dispatch_brief_carries_post_edit_issue_body(tmp_path):
    """SC-001: the brief is built from the dispatch-time fetch — a
    creation-time copy is unrepresentable, so 'post-edit' is simply whatever
    the fetcher returns NOW."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7])
    store.save_status("g", GoalStatus(phase="idle"))
    fetcher = FakeIssueFetcher({7: _snap(7, title="Fix the widget", body="EDITED BODY: parse the new shape")})
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier(), fetcher)

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert ISSUE_CONTEXT_MARKER in action.goal
    assert "EDITED BODY: parse the new shape" in action.goal
    assert "Issue #7: Fix the widget" in action.goal
    assert fetcher.calls == 1


@pytest.mark.asyncio
async def test_closed_issue_dropped_loudly_open_ones_dispatch_in_order(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7, 8])
    store.save_status("g", GoalStatus(phase="idle"))
    fetcher = FakeIssueFetcher({
        7: _snap(7, state="closed", title="already fixed"),
        8: _snap(8, title="still open", body="do this one"),
    })
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier(), fetcher)

    assert out is Outcome.DISPATCHED
    action, _, _ = engine.dispatched[0]
    assert "Issue #8: still open" in action.goal
    assert "Issue #7 is closed" in action.goal      # explicit do-not-work marker
    assert "### Issue #7" not in action.goal        # not rendered as work
    assert "freshness guard" in store.recent_log("g")


@pytest.mark.asyncio
async def test_all_refs_closed_proposes_done_with_zero_worker_sessions(tmp_path):
    """SC-002 (the #684 class): work resolved out-of-band costs no worker
    session — the goal goes straight to the grounded done gate."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7])
    store.save_status("g", GoalStatus(phase="idle"))
    fetcher = FakeIssueFetcher({7: _snap(7, state="closed")})
    engine = FakeEngine()

    out = await _tick(store, "g", FakeClaude(), engine, RecordingNotifier(), fetcher)

    assert out is Outcome.VERIFYING
    tools = [a.tool for a, _g, _u in engine.dispatched]
    assert tools == ["review_repository"]           # the done-check, no worker
    assert store.load_status("g").phase == "verifying"
    assert "proposing done without dispatching a worker" in store.recent_log("g")


@pytest.mark.asyncio
async def test_unfetchable_ref_blocks_human_gated(tmp_path):
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", issue_refs=[7])
    store.save_status("g", GoalStatus(phase="idle"))
    fetcher = FakeIssueFetcher({7: IssueRefError("gh could not fetch o/r#7 (exit 1)")})
    evaluator, engine = FakeClaude(), FakeEngine()

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier(), fetcher)

    assert out is Outcome.BLOCKED
    s = store.load_status("g")
    assert s.phase == "blocked"
    assert s.blocked_kind == "lost_ref"
    assert "never a stale copy" in (s.blocked_on or "")
    assert engine.dispatched == []
    assert evaluator.calls == 0


@pytest.mark.asyncio
async def test_idle_and_blocked_ticks_make_no_issue_fetches(tmp_path):
    """The zero-token guard extends to fetches (spec 019 FR-004/SC-004):
    freshness work happens ONLY at a live dispatch boundary."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g", cadence="1d", issue_refs=[7])
    # idle, cadence not due, no steering
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    fetcher = FakeIssueFetcher({7: _snap(7)})
    evaluator, engine = FakeClaude(), FakeEngine()

    out = await _tick(store, "g", evaluator, engine, RecordingNotifier(), fetcher)
    assert out is Outcome.IDLE
    assert fetcher.calls == 0

    # blocked with no human steering: still zero fetches
    store.save_status("g", GoalStatus(
        phase="blocked", blocked_on="q", blocked_kind="needs_answer",
    ))
    out = await _tick(store, "g", evaluator, engine, RecordingNotifier(), fetcher)
    assert out is Outcome.IDLE
    assert fetcher.calls == 0
    assert evaluator.calls == 0
