"""The thin-path advance brief runs the speckit flow, not PLAN.md (spec 008 US1).

``_advance_brief`` is the ONLY brief the long_lived thin-path worker gets. Post
spec-008 it instructs the speckit flow (``specify → plan → tasks → implement``
for the current feature), names ``tasks.md`` as the execution contract, and
carries NO ``PLAN.md`` directive. It is prompt-text only — no host cognition —
so the load-bearing zero-token idle guard (Principle III) is unchanged: an idle
tick still leaves the evaluator at ``calls == 0``.
"""

from __future__ import annotations

import pytest

from devclaw.goal.models import Goal, GoalStatus
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, _advance_brief
from tests.goal_fakes import (
    Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal,
)


def _goal(**kw) -> Goal:
    base = dict(
        id="g",
        objective="Ship the widget end to end",
        cadence="1d",
        engine="devclaw",
        workspace_dir="/repos/demo",
        repo_url="https://example.com/demo.git",
        done_when="the widget renders and is tested",
    )
    base.update(kw)
    return Goal(**base)


def test_advance_brief_instructs_the_speckit_flow_and_names_tasks_md():
    brief = _advance_brief(_goal(), steering="")
    low = brief.lower()
    # Names the speckit flow + its execution contract file.
    assert "speckit" in low or "specify" in low
    assert "tasks.md" in low
    assert "specs/" in low  # the per-feature artifact set location
    # Still advances one slice, not the whole plan.
    assert "one" in low and ("slice" in low or "story" in low or "increment" in low)


def test_advance_brief_carries_no_plan_md_directive():
    brief = _advance_brief(_goal(), steering="")
    assert "PLAN.md" not in brief  # the retired spine — must be gone


def test_advance_brief_is_model_agnostic_no_slash_command_wiring():
    brief = _advance_brief(_goal(), steering="")
    # Model-agnostic (Principle II): plain imperative text, never Claude-Code
    # slash-command wiring.
    assert "/specify" not in brief and "/plan" not in brief
    assert "/tasks" not in brief and "/implement" not in brief


def test_advance_brief_passes_steering_through_verbatim():
    brief = _advance_brief(_goal(), steering="pause features, fix the failing CI first")
    assert "pause features, fix the failing CI first" in brief
    assert "Goal: Ship the widget end to end" in brief


@pytest.mark.asyncio
async def test_speckit_brief_change_keeps_idle_tick_zero_token(tmp_path):
    """The brief change is prompt-text only — an idle tick must still spend zero
    cognition (the load-bearing quota guard; mirrors
    test_goal_tick.test_idle_tick_spends_zero_tokens)."""
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "g", cadence="1d", mode="long_lived")
    store.save_status("g", GoalStatus(phase="idle", last_plan_at=store.now_iso()))
    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()

    from devclaw.goal.tick import tick_goal

    out = await tick_goal(
        "g", store=store, engine=engine, evaluator_caller=evaluator,
        notifier=notifier, notify_url="http://relay", prepare_ws=fake_prepare,
        verify_done=True,
    )

    assert out is Outcome.IDLE
    assert evaluator.calls == 0
    assert engine.dispatched == []
