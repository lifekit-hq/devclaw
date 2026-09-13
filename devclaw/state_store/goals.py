"""The goal layer's whole persisted state (spec 046, pillar 5): the ``goals``
and ``decisions`` tables. Identity plus the last-seen world fingerprint —
an observation, never a hold, a budget, or a counter. A mixin on the same
StateStore instance, so goal writes join task writes in one transaction."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from .rows import Decision, Goal, _now_ms, _row_to_decision, _row_to_goal

if TYPE_CHECKING:
    import sqlite3
    import threading


class GoalsMixin:
    if TYPE_CHECKING:
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...

    def create_goal(self, *, id: str, project_id: str, workspace_dir: str,
                    repo_url: str, objective: str, issues: list[int], branch: str) -> Goal:
        with self._lock:
            self._db.execute(
                "INSERT INTO goals (id, project_id, workspace_dir, repo_url, objective, "
                "issues_json, branch, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (id, project_id, workspace_dir, repo_url or "", objective or "",
                 json.dumps([int(n) for n in issues]), branch, _now_ms()),
            )
            self._commit()
        goal = self.get_goal(id)
        assert goal is not None
        return goal

    def get_goal(self, goal_id: str) -> Optional[Goal]:
        with self._lock:
            row = self._db.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        return _row_to_goal(row) if row else None

    def list_goals(self, *, open_only: bool = False) -> list[Goal]:
        sql = "SELECT * FROM goals"
        if open_only:
            sql += " WHERE outcome IS NULL"
        sql += " ORDER BY created_at ASC"
        with self._lock:
            rows = self._db.execute(sql).fetchall()
        return [_row_to_goal(r) for r in rows]

    def set_goal_last_seen(self, goal_id: str, world_json: str) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE goals SET last_seen_json = ?, last_seen_at = ? WHERE id = ?",
                (world_json, _now_ms(), goal_id),
            )
            self._commit()

    def close_goal(self, goal_id: str, outcome: str) -> bool:
        """Terminal, once: returns False when the goal was already closed."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE goals SET outcome = ?, closed_at = ? WHERE id = ? AND outcome IS NULL",
                (outcome, _now_ms(), goal_id),
            )
            self._commit()
            return cur.rowcount == 1

    def record_decision(self, goal_id: str, text: str, comment_url: str = "") -> Decision:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO decisions (goal_id, text, comment_url, made_at) VALUES (?, ?, ?, ?)",
                (goal_id, text, comment_url or "", _now_ms()),
            )
            self._commit()
            row = self._db.execute("SELECT * FROM decisions WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _row_to_decision(row)

    def list_decisions(self, goal_id: str) -> list[Decision]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM decisions WHERE goal_id = ? ORDER BY id ASC", (goal_id,)
            ).fetchall()
        return [_row_to_decision(r) for r in rows]
