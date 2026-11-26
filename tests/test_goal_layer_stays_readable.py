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


#: Issue numbers and dispatch metadata — never a copy of issue or comment text
#: (#1058). The issue is the contract, read live; a text column here is a
#: second copy some tick, gate or merge path could read instead of the issue.
_GOAL_COLUMNS = {"id", "project_id", "workspace_dir", "repo_url", "issues_json", "branch",
                 "created_at", "closed_at", "outcome", "last_seen_json", "last_seen_at"}
_DECISION_COLUMNS = {"id", "goal_id", "comment_url", "made_at"}


def _columns(db, table: str) -> set[str]:
    return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}


def test_goal_and_decision_rows_hold_references_not_github_text(tmp_path):
    from dataclasses import fields

    from devclaw.state_store import Decision, Goal, StateStore

    store = StateStore(str(tmp_path / "s.db"))
    assert _columns(store._db, "goals") == _GOAL_COLUMNS
    assert _columns(store._db, "decisions") == _DECISION_COLUMNS
    assert {f.name for f in fields(Goal)} == (_GOAL_COLUMNS - {"issues_json"}) | {"issues"}
    assert {f.name for f in fields(Decision)} == _DECISION_COLUMNS


def test_an_older_database_drops_its_text_copies_and_keeps_every_row(tmp_path):
    import sqlite3

    from devclaw.state_store import StateStore

    path = str(tmp_path / "old.db")
    db = sqlite3.connect(path)
    db.executescript(
        """
        CREATE TABLE goals (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workspace_dir TEXT NOT NULL,
          repo_url TEXT NOT NULL DEFAULT '', objective TEXT NOT NULL DEFAULT '',
          issues_json TEXT NOT NULL DEFAULT '[]', branch TEXT NOT NULL, created_at INTEGER NOT NULL,
          closed_at INTEGER, outcome TEXT, last_seen_json TEXT, last_seen_at INTEGER);
        CREATE TABLE decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, goal_id TEXT NOT NULL,
          text TEXT NOT NULL, comment_url TEXT NOT NULL DEFAULT '', made_at INTEGER NOT NULL);
        INSERT INTO goals VALUES ('g', 'p', '/w', 'https://github.com/o/r', 'old title', '[7]',
          'goal/g', 1, NULL, NULL, NULL, NULL);
        INSERT INTO decisions (goal_id, text, comment_url, made_at)
          VALUES ('g', 'merge', 'https://github.com/o/r/issues/7#issuecomment-9', 2);
        """
    )
    db.commit()
    db.close()

    store = StateStore(path)
    assert _columns(store._db, "goals") == _GOAL_COLUMNS
    assert _columns(store._db, "decisions") == _DECISION_COLUMNS
    goal = store.get_goal("g")
    assert goal is not None and goal.issues == [7] and goal.open
    [d] = store.list_decisions("g")
    assert d.comment_url.endswith("#issuecomment-9")
    store.record_decision("g", "https://github.com/o/r/issues/7#issuecomment-10")
    assert len(store.list_decisions("g")) == 2
