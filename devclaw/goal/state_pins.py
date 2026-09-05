"""Row I/O for ``goal_contract_pins`` (spec 035) — the pinned done-gate
rubric, one row per (goal, contract revision), history retained for audit
(clarified 2026-09-05).

Same composition contract as the problems mixin: methods run on the
composing ``GoalState``'s shared connection under its lock; only the
GoalStore's done-gate path calls them (single writer, constitution IV).
A row that cannot be parsed raises :class:`~devclaw.goal.clause_pin
.PinCorrupt` out of the read — the gate recovers loudly (FR-006), this
layer never repairs or drops silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from ..state_store import _now_ms
from .clause_pin import ContractPin, clauses_to_json, drops_to_json, pin_from_row

if TYPE_CHECKING:
    from ..state_store import StateStore


class GoalStatePinsMixin:
    """Composed into ``GoalState`` beside the status/content/problems mixins;
    the composing class owns ``self._store`` (the shared StateStore)."""

    _store: "StateStore"

    def read_contract_pin(self, goal_id: str, revision: str) -> Optional[ContractPin]:
        """The pin for exactly this (goal, revision), or ``None`` when the
        revision has never been decomposed. Raises ``PinCorrupt`` on an
        unparseable row — the caller owns the loud recovery."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT clauses, ceremony_drops, pinned_at_ms, pinned_by_round, recovery"
                " FROM goal_contract_pins WHERE goal_id = ? AND revision = ?",
                (goal_id, revision),
            ).fetchone()
        if row is None:
            return None
        return pin_from_row(
            goal_id, revision, row["clauses"], row["ceremony_drops"],
            int(row["pinned_at_ms"] or 0), int(row["pinned_by_round"] or 0),
            row["recovery"] or "",
        )

    def write_contract_pin(self, pin: ContractPin) -> ContractPin:
        """Persist a pin (INSERT OR REPLACE: the only legal overwrite of an
        existing (goal, revision) row is the FR-006 corrupt-pin recovery,
        which carries its reason in ``recovery``). Returns the pin with its
        write timestamp stamped."""
        at = pin.pinned_at_ms or _now_ms()
        from dataclasses import replace as _replace

        stamped = _replace(pin, pinned_at_ms=at)
        with self._store._lock:
            self._store._db.execute(
                "INSERT OR REPLACE INTO goal_contract_pins"
                " (goal_id, revision, clauses, ceremony_drops, pinned_at_ms,"
                "  pinned_by_round, recovery)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    stamped.goal_id, stamped.revision, clauses_to_json(stamped),
                    drops_to_json(stamped), stamped.pinned_at_ms,
                    stamped.pinned_by_round, stamped.recovery,
                ),
            )
            self._store._commit()
        return stamped
