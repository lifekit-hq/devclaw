"""The read-only export of open work as envelopes (#1061, owner ruling 2026-11-26).

``devclaw tasks --json`` prints one envelope per open goal and per open task so
a fleet view can show devclaw's queue without calling its MCP tools. An
envelope is a *pointer*: it carries identity, a coarse stage and state, a
``since`` and who must act — never issue text, a prompt, a session's words, a
path or a credential. ``state`` is an A2A task state name (decision 0016);
``detail`` keeps devclaw's own word for it. The issue owns the item; ``ref`` names it.

Read-only by construction: the database is opened ``mode=ro`` with
``query_only`` on, so no statement can write, nothing is created when the file
is absent, and the schema bootstrap :class:`StateStore` runs on open is never
reached.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .goal.github import owner_repo
from .goal.service import _state_word
from .state_store.rows import Goal, Task, _row_to_goal, _row_to_task

STORE = "devclaw"
CLASS = "dev"
#: the only keys an envelope carries — a new key is a reviewed change
ENVELOPE_KEYS = ("id", "kind", "store", "ref", "refs", "stage", "state", "detail", "since", "owner", "class")

#: the A2A task states the fleet reads (decision 0016); ``state`` is always one of them
A2A_STATES = ("submitted", "working", "input-required", "auth-required",
              "completed", "failed", "canceled", "rejected")
#: every word ``goal.service._state_word`` produces → its A2A state. A DONE
#: proposal goes to the host's done-gate review and an INTERRUPTED session is
#: followed by a fresh one next tick, so both are still devclaw's work; only a
#: BLOCKED question waits for the owner.
GOAL_STATES = {
    "new": "submitted", "running": "working", "waiting": "working",
    "proposed done": "working", "interrupted": "working", "blocked": "input-required",
    "achieved": "completed", "cancelled": "canceled", "closed": "failed",
}
#: every task ``status`` (``state_store.rows.TaskStatus``) → its A2A state
TASK_STATES = {
    "pending": "submitted", "running": "working", "done": "completed",
    "failed": "failed", "cancelled": "canceled",
}


def _a2a(table: dict[str, str], word: str) -> str:
    """The A2A name for a devclaw word; an unmapped word fails loud, never leaks."""
    try:
        return table[word]
    except KeyError:
        raise ValueError(f"no A2A state for devclaw state {word!r}") from None


def _iso(ms: Optional[int]) -> str:
    return datetime.fromtimestamp((ms or 0) / 1000, tz=timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _stage(kind: Optional[str]) -> str:
    """``review`` for a read-only done-gate review session, else ``build``."""
    return "review" if kind == "review_repository" else "build"


def _issue_refs(goal: Goal) -> list[str]:
    slug = owner_repo(goal.repo_url) or goal.project_id
    return [f"{slug}#{n}" for n in goal.issues] or [goal.id]


def _goal_envelope(goal: Goal, last: Optional[Task]) -> dict:
    word = _state_word(goal, last)
    state = _a2a(GOAL_STATES, word)
    if last is None:
        since = goal.created_at
    elif last.status in ("pending", "running"):
        since = last.started_at or last.created_at
    else:
        since = last.completed_at or last.created_at
    refs = _issue_refs(goal)
    return {
        "id": f"{STORE}:{goal.id}", "kind": "goal", "store": STORE,
        "ref": refs[0], "refs": refs,
        "stage": _stage(last.kind if last else None), "state": state, "detail": word,
        "since": _iso(since),
        # the owner acts only on decisions: a BLOCKED question is the one stop that waits for them
        "owner": "owner" if state == "input-required" else "devclaw",
        "class": CLASS,
    }


def _task_envelope(task: Task) -> dict:
    return {
        "id": f"{STORE}:{task.id}", "kind": "task", "store": STORE,
        "ref": task.id, "refs": [task.id],
        "stage": _stage(task.kind), "state": _a2a(TASK_STATES, task.status), "detail": task.status,
        "since": _iso(task.started_at or task.created_at),
        "owner": "devclaw", "class": CLASS,
    }


def open_envelopes(db_path: str) -> list[dict]:
    """Every open goal, then every pending or running task, oldest first.
    An absent database or one never bootstrapped is an empty queue."""
    path = Path(db_path).expanduser()
    if not path.is_file():
        return []
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only = ON")
        try:
            goals = [_row_to_goal(r) for r in db.execute(
                "SELECT * FROM goals WHERE outcome IS NULL ORDER BY created_at ASC")]
            out = []
            for goal in goals:
                row = db.execute("SELECT * FROM tasks WHERE parent_goal_id = ? "
                                 "ORDER BY created_at DESC LIMIT 1", (goal.id,)).fetchone()
                out.append(_goal_envelope(goal, _row_to_task(row) if row else None))
            out.extend(_task_envelope(_row_to_task(r)) for r in db.execute(
                "SELECT * FROM tasks WHERE status IN ('pending', 'running') ORDER BY created_at ASC"))
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise
            return []
        return out
    finally:
        db.close()
