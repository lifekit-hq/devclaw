"""The done-gate grounds on the executing feature's ``specs/NNN/spec.md`` (spec
008 US1, FR-006 / D6).

Post spec-008 the planning contract lives in the repo as speckit ``spec.md``, not
a host-side firmed ``done_when``. The done-gate grounds on the executing
feature's ``spec.md``, resolved as: the dir RECORDED at dispatch, else — the
common brand-new-feature case, where the dir did not exist pre-session — the
feature the increment actually landed in, DERIVED from the post-session working
tree. Only a repo with no ``specs/`` at all falls back to the goal's
``done_when`` text — the grounding source changed, the gate did not weaken
(verification stays fail-closed; ``done_when`` is always evaluated).
"""

from __future__ import annotations

import subprocess

import pytest

from devclaw.goal import tick_donegate
from devclaw.goal.models import EvalResult, GoalStatus, InFlight, PollResult
from devclaw.goal.store import GoalStore
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)

_SPEC = """# Feature Spec — Widget

## Success Criteria
- SC-DISTINCT-042: the widget renders in under 100ms and survives a reload
- SC-002: every widget action is covered by a test
"""


def _repo_with_spec(tmp_path, *, with_tasks: bool = False):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    feature = repo / "specs" / "012-widget"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text(_SPEC)
    if with_tasks:
        # A tasks.md is what current_feature_dir_sync globs to derive the active
        # feature when no dispatch-time record exists.
        (feature / "tasks.md").write_text("- [x] T001 [US1] build the widget\n")
    return repo


def _bare_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    return repo


async def _tick(store, goal_id, evaluator, engine, notifier, *, verify_done):
    from devclaw.goal.tick import tick_goal

    return await tick_goal(
        goal_id, store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
        eval_every=99, verify_done=verify_done,
    )


@pytest.mark.asyncio
async def test_done_gate_grounds_on_recorded_feature_spec(tmp_path, monkeypatch):
    repo = _repo_with_spec(tmp_path)
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g", workspace_dir=str(repo), done_when="the widget works")
    # The dispatch path records which feature the goal is executing.
    store.write_executing_feature("g", "specs/012-widget")
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))

    captured: dict = {}

    async def _capture_evaluate(goal, status, log, deliveries, **kw):
        captured.update(kw)
        return EvalResult(verdict="off_track", rationale="wip", corrections=["keep going"])

    monkeypatch.setattr(tick_donegate._evaluator, "evaluate", _capture_evaluate)

    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="shipped", gate_passed=True,
    ))
    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier(), verify_done=False)

    # The evaluator was grounded on the feature spec, not just done_when.
    assert "spec" in captured
    assert "SC-DISTINCT-042" in captured["spec"]


@pytest.mark.asyncio
async def test_done_gate_grounds_on_derived_feature_when_unrecorded(tmp_path, monkeypatch):
    # The brand-new-feature case: the worker AUTHORED specs/012-widget in-session,
    # so nothing was recorded at dispatch (the dir did not exist pre-session).
    # FR-006 must still fire — the gate derives the feature from the working tree
    # and grounds on its spec.md. (This is the fix: the old code fell back to
    # done_when here and the headline grounding never happened on the cycle that
    # produced the feature.)
    repo = _repo_with_spec(tmp_path, with_tasks=True)
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g", workspace_dir=str(repo), done_when="the widget works")
    # NO write_executing_feature call — nothing recorded.
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))

    captured: dict = {}

    async def _capture_evaluate(goal, status, log, deliveries, **kw):
        captured.update(kw)
        return EvalResult(verdict="off_track", rationale="wip", corrections=["keep going"])

    monkeypatch.setattr(tick_donegate._evaluator, "evaluate", _capture_evaluate)

    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="shipped", gate_passed=True,
    ))
    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier(), verify_done=False)

    assert "spec" in captured
    assert "SC-DISTINCT-042" in captured["spec"]  # derived, not just done_when


@pytest.mark.asyncio
async def test_done_gate_falls_back_to_done_when_when_no_specs_at_all(tmp_path, monkeypatch):
    # A repo with NO specs/ dir (nothing recorded, nothing to derive) → the gate
    # falls back to the goal's done_when; no phantom feature spec is invented.
    repo = _bare_repo(tmp_path)
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g", workspace_dir=str(repo), done_when="the widget works")
    store.save_status("g", GoalStatus(
        phase="in_flight",
        in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "advance the goal"),
    ))

    captured: dict = {}

    async def _capture_evaluate(goal, status, log, deliveries, **kw):
        captured.update(kw)
        assert goal.done_when == "the widget works"  # done_when is the fallback contract
        return EvalResult(verdict="off_track", rationale="wip", corrections=["keep going"])

    monkeypatch.setattr(tick_donegate._evaluator, "evaluate", _capture_evaluate)

    engine = FakeEngine(poll_result=PollResult(
        terminal=True, status="done", detail="shipped", gate_passed=True,
    ))
    await _tick(store, "g", FakeClaude(), engine, RecordingNotifier(), verify_done=False)

    assert "spec" in captured
    assert "SC-DISTINCT-042" not in captured["spec"]
