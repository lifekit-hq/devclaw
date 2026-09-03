"""Row I/O for ``goal_interventions`` (spec 032 US5).

The north-star metric's numerator: every time a human had to act on a goal —
the four verbs (``steer``, ``resume``, ``decide``, ``correct_implementation``)
and every commit on a goal branch that the worker did not author. Append-only;
one writer (the composing ``GoalState``, constitution IV); read by the
scorecard as *interventions per achieved goal*. Never a decision input.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..state_store import _now_ms

if TYPE_CHECKING:
    from ..state_store import StateStore

INTERVENTION_VERBS: tuple[str, ...] = (
    "steer", "resume", "decide", "correct_implementation", "commit",
)


class GoalStateInterventionsMixin:
    """Composed into ``GoalState``; the composing class owns ``self._store``."""

    _store: "StateStore"

    def record_intervention(self, goal_id: str, verb: str, ref: str = "") -> None:
        if verb not in INTERVENTION_VERBS:
            raise ValueError(f"unknown intervention verb {verb!r}")
        with self._store._lock:
            self._store._db.execute(
                "INSERT INTO goal_interventions (goal_id, verb, ref, made_at) VALUES (?, ?, ?, ?)",
                (goal_id, verb, ref or "", _now_ms()),
            )
            self._store._commit()

    def interventions_since(self, since_ms: int) -> list[dict]:
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT goal_id, verb, ref, made_at FROM goal_interventions "
                "WHERE made_at >= ? ORDER BY made_at",
                (since_ms,),
            ).fetchall()
        return [
            {"goal_id": r["goal_id"], "verb": r["verb"], "ref": r["ref"], "made_at": int(r["made_at"])}
            for r in rows
        ]
