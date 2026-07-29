"""Shared prompt-size budget helpers (#431) — the #422 class beyond the planner.

#426 capped the planner's ``recent_log`` in place; the same unbounded sections
ride into the EVALUATOR prompt (``recent_log`` + the grounded ``deliveries``
tail), and the evaluator hit the identical ``claude --print`` timeout
(``cognition|evaluator|timeout`` ×3 in the live catalog). These pins guard the
shared ``devclaw.goal.prompt_budget`` helpers and their adoption in the evaluator:
oversized context is tail-kept behind a marker, small/empty context passes
through byte-identical, and the planner's re-export stays wired so #426 is a pure
refactor.
"""

from __future__ import annotations

from devclaw.goal.evaluator import build_prompt
from devclaw.goal.models import Goal, GoalStatus
from devclaw.goal.prompt_budget import (
    DELIVERIES_KEEP,
    DELIVERIES_TRUNCATION_MARKER,
    LOG_KEEP,
    LOG_TRUNCATION_MARKER,
    cap_deliveries,
    cap_log,
    cap_section,
)


def _goal():
    return Goal(
        id="g", objective="ship a health endpoint", cadence="1d", engine="devclaw",
        workspace_dir="/ws", done_when="/health returns 200 and is tested",
        backlog=["add /health"],
    )


# ---- the pure helpers -------------------------------------------------------


def test_cap_section_passes_small_through_byte_identical():
    small = "a" * 100
    assert cap_section(small, keep=1000, marker="X") == small
    assert cap_section("", keep=1000, marker="X") == ""  # "" stays "" for the fallback


def test_cap_section_tail_keeps_oversized_behind_marker():
    over = "OLD" + ("z" * 5000)
    capped = cap_section(over, keep=1000, marker="MARK")
    assert capped.startswith("MARK\n")           # marker leads
    assert capped.endswith("z" * 1000)           # newest tail kept
    assert "OLD" not in capped                    # oldest content dropped
    assert len(capped) <= 1000 + len("MARK\n")


def test_cap_log_and_cap_deliveries_use_their_budgets():
    small = "recent line"
    assert cap_log(small) == small
    assert cap_deliveries(small) == small
    big_log = "x" * (2 * LOG_KEEP)
    big_del = "y" * (2 * DELIVERIES_KEEP)
    assert cap_log(big_log).startswith(LOG_TRUNCATION_MARKER)
    assert cap_deliveries(big_del).startswith(DELIVERIES_TRUNCATION_MARKER)
    assert len(cap_log(big_log)) <= LOG_KEEP + len(LOG_TRUNCATION_MARKER) + 1
    assert len(cap_deliveries(big_del)) <= DELIVERIES_KEEP + len(DELIVERIES_TRUNCATION_MARKER) + 1


# ---- adoption in the evaluator prompt (the actual #431 bug) -----------------


def test_oversized_recent_log_is_tail_kept_in_evaluator_prompt():
    """The evaluator embedded recent_log uncapped; a bloated log pushed the
    assembled prompt into the claude --print timeout (evaluator timeouts ×3)."""
    fat_log = "FIRST-EVENT\n" + ("x" * (3 * LOG_KEEP)) + "\nLAST-EVENT"
    prompt = build_prompt(_goal(), GoalStatus(), fat_log, "deliveries")
    assert LOG_TRUNCATION_MARKER in prompt
    assert "LAST-EVENT" in prompt          # newest kept
    assert "FIRST-EVENT" not in prompt     # oldest elided
    # The unbounded section can no longer blow the prompt past the budget.
    assert len(prompt) < LOG_KEEP + 20_000


def test_oversized_deliveries_is_tail_kept_in_evaluator_prompt():
    fat_deliveries = "FIRST-DELIVERY\n" + ("y" * (3 * DELIVERIES_KEEP)) + "\nLAST-DELIVERY"
    prompt = build_prompt(_goal(), GoalStatus(), "log", fat_deliveries)
    assert DELIVERIES_TRUNCATION_MARKER in prompt
    assert "LAST-DELIVERY" in prompt
    assert "FIRST-DELIVERY" not in prompt
    assert len(prompt) < DELIVERIES_KEEP + 20_000


def test_small_evaluator_sections_pass_through_unchanged():
    """Real-sized logs/deliveries must be byte-present and unmarked — the cap is
    invisible below budget, so existing behavior and stubs are unaffected."""
    prompt = build_prompt(_goal(), GoalStatus(), "a modest log line", "one delivery")
    assert "a modest log line" in prompt
    assert "one delivery" in prompt
    assert LOG_TRUNCATION_MARKER not in prompt
    assert DELIVERIES_TRUNCATION_MARKER not in prompt
