"""The done-gate grounds on the executing feature's ``specs/NNN/spec.md`` (spec
008 US1, FR-006 / D6).

Post spec-008 the planning contract lives in the repo as speckit ``spec.md``, not
a host-side firmed ``done_when``. When a goal has an executing-feature directory
recorded (at dispatch), the done-gate reads that feature's ``spec.md`` from the
workspace and feeds it to the evaluator as the ``spec`` grounding ("judge done
against THIS"). When no feature dir is recorded, it falls back to the goal's
existing ``done_when`` text — the grounding source changed, the gate did not
weaken (verification stays fail-closed; ``done_when`` is always evaluated).
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


def _repo_with_spec(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q", "-b", "main"], check=True)
    feature = repo / "specs" / "012-widget"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text(_SPEC)
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
async def test_done_gate_falls_back_to_done_when_without_feature_dir(tmp_path, monkeypatch):
    repo = _repo_with_spec(tmp_path)
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g", workspace_dir=str(repo), done_when="the widget works")
    # No executing-feature recorded → fall back to done_when text (the spec.md
    # success criteria must NOT leak into the grounding).
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
