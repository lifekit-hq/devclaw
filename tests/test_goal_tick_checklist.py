"""Goal-branch delivery topology on the live tick path — which branch a
dispatch checks out (``goal/<id>`` accumulation vs the per-action default-branch
reset) and the branch-staleness probe (issue-393 wire-up) that guards it.

Historically this file exercised the checklist-mode wiring; the checklist (and
the whole host-cognition chain) died with spec 008 shrink, and
``delivery_strategy.resolve_strategy`` keyed on the explicit
``lifecycle="executing"`` stamp instead of ``read_checklist`` — and since the
#616 cutoff it does not branch at all: every goal is goal-branch. The two
tests that exercised the per-action path now SELECT that topology explicitly
rather than seeding a legacy ``lifecycle=None`` row, which is a goal shape
production stopped writing at the 008 shrink and the cutoff has since
migrated away."""

from __future__ import annotations

import pytest

from devclaw.goal.models import Action, GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, _dispatch_action, tick_goal
from tests.goal_fakes import Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal


def _store(tmp_path):
    return GoalStore(tmp_path, now=Clock())


# ---- which branch a dispatch checks out -------------------------------------


@pytest.mark.asyncio
async def test_long_lived_goal_accumulates_on_goal_branch(tmp_path):
    """The 2026-08-08 amnesia fix: a goal in the ``executing`` lifecycle
    accumulates its nightly thin-advance increments on ``goal/<id>`` — the SAME
    branch every night — so the per-task reset-to-``origin/main`` no longer
    wipes prior nights' work (which never merges to main, so main stays empty
    and a per-action reset rebuilds from scratch)."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))

    calls: list = []

    async def rec_prepare(ws, repo_url=None, branch=None):
        calls.append(branch)
        return branch or "main"

    engine = FakeEngine()

    await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=FakeClaude(role="evaluator"),
        notifier=RecordingNotifier(), prepare_ws=rec_prepare,
    )

    assert "goal/g" in calls


@pytest.mark.asyncio
async def test_review_repository_dispatch_does_not_use_goal_branch(tmp_path):
    """``review_repository`` is read-only — the shared dispatch path must run
    it on the default branch even for a goal-branch (``executing``) goal. No
    tick path routes a mid-goal review through ``_dispatch_action`` anymore
    (the planner that emitted them is gone, demolition P3b), so this drives the
    surviving guard directly — it also keeps the reviewer's checkout un-stacked
    for any future caller of the seam."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(phase="idle", lifecycle="executing"))

    calls: list = []

    async def rec_prepare(ws, repo_url=None, branch=None):
        calls.append(branch)
        return branch or "main"

    review = Action(engine="devclaw", tool="review_repository", goal="scan", open_pr=False)
    out = await _dispatch_action(
        "g", store.load_goal("g"), store.load_status("g"), review,
        store=store, engine=FakeEngine(), notifier=RecordingNotifier(),
        notify_url="", prepare_ws=rec_prepare,
    )

    assert out is Outcome.DISPATCHED
    assert calls == [None]


@pytest.mark.asyncio
async def test_done_gate_review_uses_goal_branch_for_executing_goal(tmp_path):
    """The done-gate review judges done_when against the goal's accumulated
    work — must read the goal branch, not the empty default branch. Triggered
    the thin way: a successful advance settle proposes done."""
    store = _store(tmp_path)
    seed_goal(tmp_path, "g")
    store.save_status("g", GoalStatus(
        phase="in_flight", lifecycle="executing",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))

    calls: list = []

    async def rec_prepare(ws, repo_url=None, branch=None):
        calls.append(branch)
        return branch or "main"

    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", pr_url="https://x/pr/1", gate_passed=True, detail="ok",
    ))

    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=FakeClaude(role="evaluator"),
        notifier=RecordingNotifier(), prepare_ws=rec_prepare,
    )

    assert out is Outcome.VERIFYING
    assert "goal/g" in calls


# ---- staleness probe (issue-393 wire-up) ------------------------------------


def _executing_goal(store, tmp_path, goal_id="g"):
    """Seed a goal-branch-topology goal ready to dispatch."""
    seed_goal(tmp_path, goal_id)
    store.save_status(goal_id, GoalStatus(phase="idle", lifecycle="executing"))


@pytest.mark.asyncio
async def test_dispatch_proceeds_when_branch_is_fresh_zero_ahead_zero_behind(tmp_path, monkeypatch):
    """THE LIVELOCK REGRESSION (#439 invariant-guard finding): a brand-new goal
    branch (first increment) — and a post-merge re-seeded branch — is 0 ahead /
    0 behind the default. The staleness skip must NOT fire on
    ``commits_ahead == 0`` alone, or the goal's first increment never dispatches
    → nothing ever lands → the branch stays 0-ahead forever (dead on arrival).
    A 0/0 branch is FRESH: it dispatches normally."""
    from devclaw.goal import tick_dispatch

    store = _store(tmp_path)
    _executing_goal(store, tmp_path)

    async def _staleness_fresh(workspace_dir, goal_id):
        return {"commits_behind": 0, "commits_ahead": 0}

    monkeypatch.setattr(tick_dispatch, "_branch_staleness", _staleness_fresh)

    engine = FakeEngine()

    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=FakeClaude(role="evaluator"),
        notifier=RecordingNotifier(), prepare_ws=fake_prepare,
    )

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1  # first increment dispatched, no livelock


@pytest.mark.asyncio
async def test_dispatch_proceeds_when_branch_is_young_below_stale_threshold(tmp_path, monkeypatch):
    """A branch 0 ahead but only a FEW commits behind (< threshold) is young, not
    stale — it dispatches. (The pre-fix code wrongly SLEPT here, treating any
    0-ahead branch as 'nothing to do'.)"""
    from devclaw.goal import tick_dispatch

    store = _store(tmp_path)
    _executing_goal(store, tmp_path)

    async def _staleness_young(workspace_dir, goal_id):
        return {"commits_behind": 3, "commits_ahead": 0}

    monkeypatch.setattr(tick_dispatch, "_branch_staleness", _staleness_young)

    engine = FakeEngine()

    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=FakeClaude(role="evaluator"),
        notifier=RecordingNotifier(), prepare_ws=fake_prepare,
    )

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1


@pytest.mark.asyncio
async def test_dispatch_skipped_when_branch_is_hard_stale(tmp_path, monkeypatch):
    """Only a HARD-STALE branch — 0 ahead AND >= BRANCH_STALE_THRESHOLD behind —
    is skipped (SLEPT), mirroring the prep-time refuse guard. This is the genuine
    'created off a very old base, a PR here is massive-conflict' case."""
    from devclaw.goal import tick_dispatch
    from devclaw.task_git import BRANCH_STALE_THRESHOLD

    store = _store(tmp_path)
    _executing_goal(store, tmp_path)

    async def _staleness_hard_stale(workspace_dir, goal_id):
        return {"commits_behind": BRANCH_STALE_THRESHOLD, "commits_ahead": 0}

    monkeypatch.setattr(tick_dispatch, "_branch_staleness", _staleness_hard_stale)

    engine = FakeEngine()

    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=FakeClaude(role="evaluator"),
        notifier=RecordingNotifier(), prepare_ws=fake_prepare,
    )

    assert out is Outcome.SLEPT
    assert engine.dispatched == []  # engine was never called
    store.render_mirrors("g")
    log = (tmp_path / "g" / "log.md").read_text()
    assert "hard-stale" in log


@pytest.mark.asyncio
async def test_dispatch_proceeds_when_branch_has_commits_ahead(tmp_path, monkeypatch):
    """When the goal branch has commits ahead of default, dispatch proceeds
    normally — the staleness probe is not a blocker."""
    from devclaw.goal import tick_dispatch

    store = _store(tmp_path)
    _executing_goal(store, tmp_path)

    async def _staleness_ahead(workspace_dir, goal_id):
        return {"commits_behind": 0, "commits_ahead": 4}

    monkeypatch.setattr(tick_dispatch, "_branch_staleness", _staleness_ahead)

    engine = FakeEngine()

    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=FakeClaude(role="evaluator"),
        notifier=RecordingNotifier(), prepare_ws=fake_prepare,
    )

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1  # engine was called


@pytest.mark.asyncio
async def test_dispatch_proceeds_when_staleness_probe_returns_none(tmp_path, monkeypatch):
    """A probe hiccup (None return) must never block a legitimate dispatch —
    the goal degrades to the pre-probe behavior (dispatch unchanged)."""
    from devclaw.goal import tick_dispatch

    store = _store(tmp_path)
    _executing_goal(store, tmp_path)

    async def _staleness_hiccup(workspace_dir, goal_id):
        return None  # probe failed

    monkeypatch.setattr(tick_dispatch, "_branch_staleness", _staleness_hiccup)

    engine = FakeEngine()

    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=FakeClaude(role="evaluator"),
        notifier=RecordingNotifier(), prepare_ws=fake_prepare,
    )

    assert out is Outcome.DISPATCHED
    assert len(engine.dispatched) == 1

