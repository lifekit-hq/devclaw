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



#: The paths that decide: the tick rule, the world, the two prompts, the
#: done-gate, the session settle and the engine. ``goals.objective`` and
#: ``decisions.text`` are display-only copies of GitHub text (#1058); none of
#: these paths may read them — the issue and the comment thread are the record.
_DECISION_PATHS = [*sorted(_GOAL.glob("*.py")), *sorted((_ROOT / "devclaw" / "queue").glob("*.py")),
                   *sorted((_ROOT / "devclaw" / "engine").glob("*.py")), _ROOT / "devclaw" / "task_queue.py"]
_DISPLAY_ONLY_READ = re.compile(r"\.objective\b|\blist_decisions\b")
#: the one exception: the goal service's read verb hands decisions to the console
_DISPLAY_VERBS = {("service.py", "list_decisions")}


def test_no_decision_path_reads_the_display_only_copies():
    hits = []
    for path in _DECISION_PATHS:
        for m in _DISPLAY_ONLY_READ.finditer(path.read_text(encoding="utf-8")):
            if (path.name, m.group(0)) not in _DISPLAY_VERBS:
                hits.append(f"{path.relative_to(_ROOT)}: {m.group(0)}")
    assert not hits, f"a decision path reads a display-only copy of GitHub text (#1058): {hits}"


def test_the_contract_is_read_from_the_issue_never_the_goal_row():
    from devclaw.goal import github as gh
    from devclaw.goal.prompts import review_brief, session_brief
    from devclaw.goal.world import World
    from devclaw.state_store import Goal

    goal = Goal(id="g", project_id="p", workspace_dir="/w", repo_url="https://github.com/o/r",
                objective="STALE-ROW-CLAUSE", issues=[1], branch="goal/g", created_at=0)
    issue = gh.Issue(1, "t", "Body.\n\n## Done when\n\n- LIVE-ISSUE-CLAUSE\n", "open", ("ready",))
    world = World(pr=gh.PrFacts("none", 0, "", "", ci="none"), issues=(issue,), comments=())
    for brief in (session_brief(goal, world, None), review_brief(goal, world)):
        assert "LIVE-ISSUE-CLAUSE" in brief
        assert "STALE-ROW-CLAUSE" not in brief
