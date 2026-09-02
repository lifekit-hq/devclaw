"""Row I/O for ``goal_problems`` / ``goal_decisions`` (spec 031).

Append-only tables; "current" is a query, never an overwrite. Every method
runs on the composing ``GoalState``'s shared connection under its lock and is
called by :class:`~devclaw.goal.store.content.GoalContentMixin` inside the
transaction that raises or clears a block — so a rolled-back BLOCK/UNBLOCK
rolls the Problem/Decision rows back with it (single writer, constitution IV).
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING, Optional

from ..state_store import _now_ms
from .models import Decision, Problem, ProblemOption

if TYPE_CHECKING:
    from ..state_store import StateStore


def _row_to_problem(row: sqlite3.Row) -> Problem:
    opts = tuple(
        ProblemOption(
            key=str(o.get("key", "")),
            label=str(o.get("label", "")),
            consequence=str(o.get("consequence", "")),
            closes_goal=bool(o.get("closes_goal", False)),
        )
        for o in json.loads(row["options_json"] or "[]")
        if isinstance(o, dict)
    )
    return Problem(
        id=row["id"], goal_id=row["goal_id"], kind=row["kind"],
        raised_by=row["raised_by"], what=row["what"] or "",
        clause=row["clause"] or "", why=row["why"] or "",
        options=opts, default_key=row["default_key"] or "",
        timebox_at=int(row["timebox_at"] or 0), status=row["status"],
        raised_at=int(row["raised_at"] or 0),
        closed_at=(int(row["closed_at"]) if row["closed_at"] is not None else None),
        closed_by_decision=row["closed_by_decision"] or "",
    )


def _row_to_decision(row: sqlite3.Row) -> Decision:
    return Decision(
        id=row["id"], goal_id=row["goal_id"], problem_id=row["problem_id"] or "",
        clause=row["clause"] or "", verb=row["verb"], option_key=row["option_key"] or "",
        text=row["text"] or "", provenance=row["provenance"], made_by=row["made_by"] or "",
        made_at=int(row["made_at"] or 0), superseded_by=row["superseded_by"] or "",
    )


class GoalStateProblemsMixin:
    """Composed into ``GoalState`` beside the status/content mixins; the
    composing class owns ``self._store`` (the shared StateStore)."""

    _store: "StateStore"

    # ---- problems ----------------------------------------------------------

    def insert_problem(self, p: Problem) -> None:
        with self._store._lock:
            self._store._db.execute(
                "INSERT INTO goal_problems (id, goal_id, kind, raised_by, what, clause, why, "
                "options_json, default_key, timebox_at, status, raised_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p.id, p.goal_id, p.kind, p.raised_by, p.what, p.clause, p.why,
                    json.dumps([o.__dict__ for o in p.options]), p.default_key,
                    p.timebox_at, p.status, p.raised_at or _now_ms(),
                ),
            )

    def supersede_open_problems(self, goal_id: str, now_ms: Optional[int] = None) -> int:
        """Mark every OPEN Problem of the goal ``superseded``. Returns the count."""
        with self._store._lock:
            cur = self._store._db.execute(
                "UPDATE goal_problems SET status='superseded', closed_at=? "
                "WHERE goal_id=? AND status='open'",
                (now_ms or _now_ms(), goal_id),
            )
            return cur.rowcount

    def close_problem(
        self, problem_id: str, status: str, decision_id: str = "",
        now_ms: Optional[int] = None,
    ) -> bool:
        """``open`` → ``resolved``/``defaulted``/``superseded``. Returns True
        iff the row moved (a second close is a no-op, never an overwrite)."""
        with self._store._lock:
            cur = self._store._db.execute(
                "UPDATE goal_problems SET status=?, closed_at=?, closed_by_decision=? "
                "WHERE id=? AND status='open'",
                (status, now_ms or _now_ms(), decision_id, problem_id),
            )
            return cur.rowcount == 1

    def current_problem(self, goal_id: str) -> Optional[Problem]:
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT * FROM goal_problems WHERE goal_id=? AND status='open' "
                "ORDER BY raised_at DESC LIMIT 1",
                (goal_id,),
            ).fetchone()
        return _row_to_problem(row) if row else None

    def problem_by_id(self, problem_id: str) -> Optional[Problem]:
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT * FROM goal_problems WHERE id=?", (problem_id,)
            ).fetchone()
        return _row_to_problem(row) if row else None

    # ---- decisions ---------------------------------------------------------

    def insert_decision(self, d: Decision) -> None:
        with self._store._lock:
            self._store._db.execute(
                "INSERT INTO goal_decisions (id, goal_id, problem_id, clause, verb, option_key, "
                "text, provenance, made_by, made_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d.id, d.goal_id, d.problem_id, d.clause, d.verb, d.option_key,
                    d.text, d.provenance, d.made_by, d.made_at or _now_ms(),
                ),
            )

    def supersede_decisions(self, goal_id: str, clause: str, by_id: str) -> int:
        """A later Decision on the same clause supersedes the earlier ones
        (history kept; only the latest is fed forward)."""
        if not clause:
            return 0
        with self._store._lock:
            cur = self._store._db.execute(
                "UPDATE goal_decisions SET superseded_by=? "
                "WHERE goal_id=? AND clause=? AND (superseded_by IS NULL OR superseded_by='') "
                "AND id != ?",
                (by_id, goal_id, clause, by_id),
            )
            return cur.rowcount

    def current_decisions(self, goal_id: str) -> list[Decision]:
        """Non-superseded Decisions, oldest first — the feed-forward input."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT * FROM goal_decisions WHERE goal_id=? "
                "AND (superseded_by IS NULL OR superseded_by='') ORDER BY made_at, id",
                (goal_id,),
            ).fetchall()
        return [_row_to_decision(r) for r in rows]
