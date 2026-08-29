"""Self-deploy on merge (spec 025 US2): a devclaw-repo merge-on-close records
a pending self-deploy; the heartbeat edge triggers the deploy workflow only at
task quiescence, expires loudly after the bounded wait, and never fires for
any other repo. All mechanical — zero cognition anywhere on the path."""

from __future__ import annotations

import json

import pytest

from devclaw.goal import merge_on_close as moc
from devclaw.goal import self_deploy
from devclaw.goal import tick_donegate
from devclaw.goal.models import GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, tick_goal
from devclaw.state_store import StateStore
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)

SELF_REPO = "lifekit-hq/devclaw"
ACHIEVED = json.dumps({
    "verdict": "achieved", "rationale": "done",
    "clauses": [{"clause": "x", "satisfied": True, "evidence": "src/x.py:1 f, tests/test_x.py:1"}],
})


class FakeTrigger:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[str] = []

    async def __call__(self, slug: str):
        self.calls.append(slug)
        return self.ok, "" if self.ok else "workflow run failed"


def _state(tmp_path) -> StateStore:
    return StateStore(str(tmp_path / "state.db"))


async def _achieved_close(tmp_path, monkeypatch, *, repo_url: str) -> GoalStore:
    """Drive one achieved close whose merge succeeds, for a goal on ``repo_url``."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g", repo_url=repo_url)
    store.save_status("g", GoalStatus(
        phase="verifying",
        in_flight=InFlight("devclaw", "review_repository", "rev1", "task", "verify",
                           is_done_check=True),
    ))

    async def merged(workspace_dir, branch):
        return moc.MergeResult(moc.MergeOutcome.MERGED, pr_url="https://x/pr/1",
                               merged_sha="abc123def456")

    monkeypatch.setattr(tick_donegate, "_attempt_merge", merged)
    out = await tick_goal(
        "g", store=store, engine=FakeEngine(poll_result=PollResult(terminal=True, status="done", detail="r")),
        evaluator_caller=FakeClaude(ACHIEVED), notifier=RecordingNotifier(),
        notify_url="http://relay", prepare_ws=fake_prepare, verify_done=True,
    )
    assert out is Outcome.DONE
    return store


@pytest.mark.asyncio
async def test_devclaw_repo_merge_records_deploy_pending_and_waits_for_quiescence(
        tmp_path, monkeypatch):
    monkeypatch.setenv("DEVCLAW_SELF_REPO", SELF_REPO)
    store = await _achieved_close(tmp_path, monkeypatch,
                                  repo_url=f"https://github.com/{SELF_REPO}.git")
    pending = store._state.deploy_pending()
    assert pending is not None
    sha, goal_id, since_ms = pending
    assert sha == "abc123def456" and goal_id == "g" and since_ms > 0

    # the heartbeat edge: a running task defers the trigger…
    trigger = FakeTrigger()
    monkeypatch.setattr(self_deploy, "_trigger", trigger)
    monkeypatch.setattr(store._state, "count_running", lambda: 1)
    assert await self_deploy.maybe_trigger(store._state, now_ms=since_ms + 1000) is None
    assert trigger.calls == []

    # …and quiescence fires it exactly once, clearing the pending row
    monkeypatch.setattr(store._state, "count_running", lambda: 0)
    assert await self_deploy.maybe_trigger(store._state, now_ms=since_ms + 2000) == "triggered"
    assert trigger.calls == [SELF_REPO]
    assert store._state.deploy_pending() is None
    last = store._state.deploy_last()
    assert last and last["outcome"] == "triggered" and last["sha"] == "abc123def456"


@pytest.mark.asyncio
async def test_deploy_pending_expires_loudly_after_bounded_wait(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVCLAW_SELF_REPO", SELF_REPO)
    state = _state(tmp_path)
    state.set_deploy_pending("abc", "g", since_ms=1_000)
    trigger = FakeTrigger()
    monkeypatch.setattr(self_deploy, "_trigger", trigger)
    monkeypatch.setattr(state, "count_running", lambda: 1)  # never quiescent

    late = 1_000 + (21_600 + 1) * 1000
    assert await self_deploy.maybe_trigger(state, now_ms=late) == "expired"
    assert trigger.calls == []
    assert state.deploy_pending() is None
    last = state.deploy_last()
    assert last and last["outcome"] == "expired"


@pytest.mark.asyncio
async def test_non_devclaw_merge_never_triggers_deploy(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVCLAW_SELF_REPO", SELF_REPO)
    store = await _achieved_close(
        tmp_path, monkeypatch, repo_url="https://github.com/lifekit-hq/finance-sentry.git")
    assert store._state.deploy_pending() is None


@pytest.mark.asyncio
async def test_deploy_trigger_is_free_when_nothing_is_owed(tmp_path, monkeypatch):
    # the idle heartbeat cost: one meta read, no subprocess, no state churn
    state = _state(tmp_path)
    trigger = FakeTrigger()
    monkeypatch.setattr(self_deploy, "_trigger", trigger)

    assert await self_deploy.maybe_trigger(state, now_ms=99_999) is None
    assert trigger.calls == [] and state.deploy_last() is None


@pytest.mark.asyncio
async def test_failed_workflow_trigger_records_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("DEVCLAW_SELF_REPO", SELF_REPO)
    state = _state(tmp_path)
    state.set_deploy_pending("abc", "g", since_ms=1_000)
    monkeypatch.setattr(self_deploy, "_trigger", FakeTrigger(ok=False))
    monkeypatch.setattr(state, "count_running", lambda: 0)

    assert await self_deploy.maybe_trigger(state, now_ms=2_000) == "trigger_failed"
    last = state.deploy_last()
    assert last and last["outcome"] == "trigger_failed"
    assert state.deploy_pending() is None  # never retried blind


def test_is_self_repo_matches_url_shapes(monkeypatch):
    monkeypatch.setenv("DEVCLAW_SELF_REPO", SELF_REPO)
    assert self_deploy.is_self_repo("https://github.com/lifekit-hq/devclaw.git")
    assert self_deploy.is_self_repo("https://github.com/lifekit-hq/devclaw")
    assert not self_deploy.is_self_repo("https://github.com/lifekit-hq/finance-sentry.git")
    monkeypatch.delenv("DEVCLAW_SELF_REPO")
    assert not self_deploy.is_self_repo("https://github.com/lifekit-hq/devclaw.git")
