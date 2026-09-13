"""Pillar 1 and pillar 5 as build guards (spec 046).

The goal layer is the one place the line between Python and reasoning lives,
and it must fit one afternoon of the owner's reading. And the host stores
observations, never judgments: a column that names a failure kind, a budget,
a hold or a counter is the mechanical second brain growing back."""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_GOAL = _ROOT / "devclaw" / "goal"
_PROMPTS = _ROOT / "devclaw" / "prompts"

GOAL_LAYER_CEILING = 1500


def _lines(paths) -> int:
    return sum(len(p.read_text(encoding="utf-8").splitlines()) for p in paths)


def test_the_goal_layer_fits_one_afternoon_of_reading():
    total = _lines(sorted(_GOAL.glob("*.py"))) + _lines(sorted(_PROMPTS.glob("*.md")))
    assert total <= GOAL_LAYER_CEILING, (
        f"devclaw/goal + devclaw/prompts is {total} lines — over the {GOAL_LAYER_CEILING} ceiling. "
        "The PR that grew it is the defect: move the judgment into the session, not the host."
    )


_FORBIDDEN = re.compile(
    r"\b(blocked_kind|heal_attempts|next_heal_at|donegate_rounds|donegate_progress|"
    r"pause_count_goal|retry|budget|hold_until|churn|problem_id|timebox)\b"
)


def test_the_goals_table_holds_no_derived_state():
    schema = (_ROOT / "devclaw" / "state_store" / "schema.py").read_text(encoding="utf-8")
    goals_ddl = schema[schema.index("CREATE TABLE IF NOT EXISTS goals"):schema.index("CREATE TABLE IF NOT EXISTS decisions")]
    hit = _FORBIDDEN.search(goals_ddl)
    assert hit is None, f"the goals table grew a judgment column: {hit.group(0)!r} (pillar 5)"
