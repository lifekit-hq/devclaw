"""Per-goal run-window at the heartbeat: a goal outside its OWN window is skipped
(0 tokens for it) while every other goal ticks normally — the loop-level half of
the mechanism unit-tested in test_dispatch_gate.py.

Confines a token-heavy standing goal (e.g. a CloseLoop ownership loop) to nights
without gating the rest of the engine, which the global run-window can't do."""
from __future__ import annotations

from devclaw.goal.tick import Outcome, tick_all
from devclaw.goal.store import GoalStore
from tests.goal_fakes import Clock, FakeClaude, FakeEngine, RecordingNotifier, fake_prepare, seed_goal


class WindowEngine(FakeEngine):
    """FakeEngine (global gates read open via getattr) plus a per-goal window gate
    driven by an explicit blocked-set, so the test controls which goals are
    outside their window without touching the wall clock."""

    def __init__(self, blocked: set[str], **kw) -> None:
        super().__init__(**kw)
        self._blocked = set(blocked)

    def goal_operator_block(self, goal_id: str, now_ms: int) -> tuple[bool, str]:
        return (True, "outside run window") if goal_id in self._blocked else (False, "")


async def test_windowed_out_goal_is_skipped_others_tick(tmp_path):
    store = GoalStore(tmp_path, now=Clock())
    seed_goal(tmp_path, "day", workspace_dir="/repos/day")
    seed_goal(tmp_path, "night", workspace_dir="/repos/night")
    evaluator = FakeClaude()
    engine = WindowEngine({"night"})

    out = await tick_all(
        store=store, engine=engine, evaluator_caller=evaluator,
        notifier=RecordingNotifier(), prepare_ws=fake_prepare, eval_every=99,
    )

    assert out["night"] is Outcome.RATE_LIMITED   # outside its window → held
    assert out["day"] is Outcome.DISPATCHED       # inside → ticked normally
    # zero cognition either way — the thin advance dispatch is mechanical, and
    # the windowed-out goal never even got that far
    assert evaluator.calls == 0
    # exactly one dispatch, and it was the day goal's advance session
    assert len(engine.dispatched) == 1
    action, goal, _ = engine.dispatched[0]
    assert action.tool == "implement_feature"
    assert "Advance this goal" in action.goal
    assert goal.workspace_dir == "/repos/day"
