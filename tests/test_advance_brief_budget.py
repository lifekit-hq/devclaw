"""Named regression tests for spec 025 — dispatch brief budget.

Three invariants pinned here:

1. The steering section of the advance brief is bounded by ``STEERING_KEEP``;
   the NEWEST steering line always survives compaction (SC-001, SC-002).
2. A 6-steer / 6-increment goal (the devclaw-022 live shape that caused
   `prompt is too long` failures) produces a brief within the combined section
   budgets (SC-002).
3. After an advance dispatch, the goal log records ``dispatch brief: N chars``
   so the ramp is visible in telemetry (SC-003).
"""

from __future__ import annotations

import pytest

from devclaw.goal import prior_increments as _pi
from devclaw.goal.models import GoalStatus
from devclaw.goal.prompt_budget import (
    PRIOR_INCREMENTS_KEEP,
    STEERING_KEEP,
    STEERING_TRUNCATION_MARKER,
    cap_steering,
)
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import Outcome, _advance_brief, tick_goal
from tests.goal_fakes import (
    Clock,
    FakeClaude,
    FakeEngine,
    RecordingNotifier,
    fake_prepare,
    seed_goal,
)


def _store(tmp_path, clock):
    return GoalStore(tmp_path, now=clock)


# ---- SC-001 / R1 / R5: steering is bounded; newest line survives -----------


def test_steering_section_bounded_and_newest_line_survives():
    """The steering section is tail-kept under STEERING_KEEP: adversarial
    many-steer input stays within budget, and the NEWEST steering line is
    always byte-present in the capped output (the active correction must
    never be the content dropped — R5)."""
    # Build 6 steering entries of 500 chars each = 3 000 chars total (~under budget)
    lines = [f"- [denys 2026-08-{i:02d}] correction {i}: " + "x" * 460 for i in range(1, 7)]
    steering = "\n".join(lines)

    # Under budget: passes through byte-identical.
    if len(steering) <= STEERING_KEEP:
        result = cap_steering(steering)
        assert result == steering
        assert lines[-1] in result
        return

    # Over budget: marker is inserted, newest line survives.
    result = cap_steering(steering)
    assert STEERING_TRUNCATION_MARKER in result
    assert len(result) <= STEERING_KEEP + len(STEERING_TRUNCATION_MARKER) + 1
    assert lines[-1] in result, "newest steering line must survive compaction intact"


def test_newest_steering_line_survives_when_older_lines_overflow():
    """Specifically: a large accumulated history where the TOTAL exceeds
    STEERING_KEEP must keep the last (newest) line intact — this is the
    hard invariant (R5): correction must never be the content dropped."""
    newest = "- [denys 2026-08-28] the ACTIVE direction that must survive"
    # Build enough older content to push well past budget.
    old_block = ("- [denys 2026-08-01] old correction " + "z" * 500 + "\n") * 20
    steering = old_block.rstrip("\n") + "\n" + newest

    result = cap_steering(steering)
    assert newest in result, "newest steering line must be byte-present after compaction"
    assert len(result) <= STEERING_KEEP + len(STEERING_TRUNCATION_MARKER) + 1


# ---- SC-002: full brief bounded with 6-steer / 6-increment history ---------


def _make_prior_increments(n: int) -> str:
    recs = [
        _pi.parse_record(
            f"increment {i}: add the {i}th module and wire it",
            f"PR: https://github.com/o/r/pull/{i}\nVerify gate `pytest -q`: PASSED\n",
            "done",
        )
        for i in range(n)
    ]
    return _pi.render(recs)


def test_brief_bounded_with_large_accumulated_history(tmp_path):
    """The devclaw-022 live shape: 6 steering lines + 6 prior increments.
    The rendered advance brief must stay under the combined section caps +
    a reasonable fixed-overhead allowance — it must NOT grow monotonically
    regardless of how many steering lines or increments accumulate."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    goal = store.load_goal("g")

    # 6 steering lines of 400 chars each (realistic correction length).
    steering_lines = [
        f"- [denys 2026-08-{i:02d}] correction {i}: please make sure the " + "y" * 340
        for i in range(1, 7)
    ]
    steering = "\n".join(steering_lines)

    # 6 prior increments (the shape that caused the failure).
    prior_increments = _make_prior_increments(6)

    brief = _advance_brief(goal, steering, prior_increments=prior_increments)

    # The brief must be bounded — the combined section budgets plus generous
    # fixed-overhead covers the saga framing, instructions, and markers.
    # 20 000 chars of overhead is an order of magnitude above any fixed text.
    max_expected = STEERING_KEEP + PRIOR_INCREMENTS_KEEP + 20_000
    assert len(brief) <= max_expected, (
        f"brief is {len(brief)} chars — exceeds {max_expected} "
        f"(STEERING_KEEP={STEERING_KEEP} + PRIOR_INCREMENTS_KEEP={PRIOR_INCREMENTS_KEEP} + overhead)"
    )


def test_brief_does_not_grow_with_more_steering(tmp_path):
    """A brief built from 300 steering lines is no larger than one built from
    6 + STEERING_KEEP: capping is flat, not monotonic."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    goal = store.load_goal("g")

    def _brief_with_steerings(n: int) -> str:
        lines = [f"- [denys 2026-08-01] steer {i}: fix the " + "w" * 100 for i in range(n)]
        return _advance_brief(goal, "\n".join(lines))

    brief_small = _brief_with_steerings(6)
    brief_large = _brief_with_steerings(300)
    assert len(brief_large) <= len(brief_small) + STEERING_KEEP


# ---- SC-003: dispatch logs brief size in the goal log ----------------------


@pytest.mark.asyncio
async def test_dispatch_logs_brief_size(tmp_path):
    """After an advance dispatch the goal log must contain a line of the form
    ``dispatch brief: N chars`` so the brief ramp is visible in telemetry
    without needing to read the raw prompt (R2)."""
    store = _store(tmp_path, Clock())
    seed_goal(tmp_path, "g")
    # Idle goal with cadence due — will dispatch.
    store.save_status("g", GoalStatus(phase="idle"))

    evaluator, engine, notifier = FakeClaude(), FakeEngine(), RecordingNotifier()
    out = await tick_goal(
        "g", store=store, engine=engine,
        evaluator_caller=evaluator, notifier=notifier,
        notify_url="http://relay", prepare_ws=fake_prepare,
    )

    assert out is Outcome.DISPATCHED
    log = store.recent_log("g")
    assert "dispatch brief:" in log and "chars" in log, (
        f"goal log should record 'dispatch brief: N chars'; got:\n{log}"
    )
    # Brief size should be > 0 and a parseable integer.
    import re
    m = re.search(r"dispatch brief: (\d+) chars", log)
    assert m is not None, "no 'dispatch brief: N chars' line found in goal log"
    assert int(m.group(1)) > 0
