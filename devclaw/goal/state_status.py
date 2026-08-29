"""Status rows — the ``goal_status`` / ``goal_phase_history`` half of ``GoalState``.

:class:`GoalStateStatusMixin` carries the pure-DB surface for the status-side
tables: the ``goal_status`` row (``has_status`` / ``current_phase`` /
``read_status`` / ``write_status`` / the :data:`~GoalStateStatusMixin
.STATUS_FIELD_COLUMNS` column-only path) and the append-only
``goal_phase_history`` — the rows the store's
:class:`~devclaw.goal.store.status.GoalStatusMixin` reads and writes through.

Split out of :class:`~devclaw.goal.state.GoalState` as a mixin on the SAME
instance — every method here runs against the ``self._store`` the base
``GoalState`` borrows (the shared sqlite connection, its ``RLock``, its
``_commit()`` no-op-inside-``transaction()`` seam), so the transaction /
single-writer semantics are byte-identical to the pre-split monolith.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from ..state_store import _now_ms
from .models import GoalStatus, InFlight

if TYPE_CHECKING:
    from ..state_store import StateStore


class GoalStateStatusMixin:
    if TYPE_CHECKING:
        # The composing class owns this (its docstring names the same contract in
        # prose); declared under TYPE_CHECKING so the seam is checked, never run.
        _store: StateStore

    # ---- goal_status persistence (PR3: STATUS.md re-backed onto SQLite) ----
    #
    # The store orchestrates STATUS.md-as-view + lazy migration; these methods
    # are the pure DB surface. All borrow the shared connection + lock and use
    # the store's _commit() (a no-op inside an open transaction()), so a status
    # write can join the same atomic unit as a task write in a later PR.

    def has_status(self, goal_id: str) -> bool:
        """Whether a ``goal_status`` row exists — the lazy-migration guard."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT 1 FROM goal_status WHERE goal_id = ? LIMIT 1", (goal_id,)
            ).fetchone()
        return row is not None

    def current_phase(self, goal_id: str) -> "str | None":
        """The stored phase, or None when no row exists. Read by save_status to
        decide whether the phase changed (and a phase_history entry is due)."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT phase FROM goal_status WHERE goal_id = ?", (goal_id,)
            ).fetchone()
        return row["phase"] if row else None

    def read_status(self, goal_id: str) -> GoalStatus:
        """Rehydrate the full :class:`GoalStatus` (incl. in_flight + phase
        history). Caller must ensure a row exists (see :meth:`has_status`)."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT * FROM goal_status WHERE goal_id = ?", (goal_id,)
            ).fetchone()
        return _row_to_status(row, self.read_phase_history(goal_id))

    def write_status(self, goal_id: str, status: GoalStatus) -> None:
        """Upsert the status row. The InFlight is serialized to in_flight_json
        (authoritative) with id/kind denormalized for later indexing; the
        phase_history tuple is NOT written here — it lives in
        goal_phase_history (see :meth:`append_phase_history`).

        ``version`` is bumped by exactly 1 on EVERY write — 1 on the first
        INSERT, ``version + 1`` on every subsequent UPDATE — the counter
        GoalStore.transition() CAS's against and computes its return value
        from (``fresh.version + 1``) without a re-read. ``state`` is written
        verbatim from ``status.state``: this method is a pure DB write, not a
        projector — the CALLER (GoalStore.save_status / .transition /
        .force_block) is responsible for stamping the derived
        devclaw.goal.transitions.State value onto ``status`` first."""
        in_flight_json = None
        in_flight_ref_id = None
        in_flight_kind = None
        if status.in_flight is not None:
            f = status.in_flight
            in_flight_ref_id = f.id
            in_flight_kind = f.ref_kind
            in_flight_json = json.dumps(
                {
                    "engine": f.engine,
                    "tool": f.tool,
                    "id": f.id,
                    "ref_kind": f.ref_kind,
                    "goal": f.goal,
                    "is_done_check": f.is_done_check,
                }
            )
        with self._store._lock:
            self._store._db.execute(
                """
                INSERT INTO goal_status (
                  goal_id, version, state, phase, lifecycle, blocked_on, blocked_kind,
                  heal_attempts, next_heal_at, "next",
                  last_plan_at, last_tick_at, actions_dispatched,
                  donegate_rounds, envcap_redispatches, slice_hold_count,
                  pending_merge_pr, merge_heal_attempted,
                  last_eval_verdict, last_eval_at, last_eval_note, last_progress_at,
                  no_progress_notified, in_flight_ref_id, in_flight_kind,
                  in_flight_json, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(goal_id) DO UPDATE SET
                  version               = goal_status.version + 1,
                  state                 = excluded.state,
                  phase                 = excluded.phase,
                  lifecycle             = excluded.lifecycle,
                  blocked_on            = excluded.blocked_on,
                  blocked_kind          = excluded.blocked_kind,
                  heal_attempts         = excluded.heal_attempts,
                  next_heal_at          = excluded.next_heal_at,
                  "next"                = excluded."next",
                  last_plan_at          = excluded.last_plan_at,
                  last_tick_at          = excluded.last_tick_at,
                  actions_dispatched    = excluded.actions_dispatched,
                  donegate_rounds       = excluded.donegate_rounds,
                  envcap_redispatches   = excluded.envcap_redispatches,
                  slice_hold_count      = excluded.slice_hold_count,
                  pending_merge_pr      = excluded.pending_merge_pr,
                  merge_heal_attempted  = excluded.merge_heal_attempted,
                  last_eval_verdict     = excluded.last_eval_verdict,
                  last_eval_at          = excluded.last_eval_at,
                  last_eval_note        = excluded.last_eval_note,
                  last_progress_at      = excluded.last_progress_at,
                  no_progress_notified  = excluded.no_progress_notified,
                  in_flight_ref_id      = excluded.in_flight_ref_id,
                  in_flight_kind        = excluded.in_flight_kind,
                  in_flight_json        = excluded.in_flight_json,
                  updated_at            = excluded.updated_at
                """,
                (
                    goal_id,
                    status.state,
                    status.phase,
                    status.lifecycle,
                    status.blocked_on,
                    status.blocked_kind,
                    status.heal_attempts,
                    status.next_heal_at,
                    status.next,
                    status.last_plan_at,
                    status.last_tick_at,
                    status.actions_dispatched,
                    status.donegate_rounds,
                    status.envcap_redispatches,
                    status.slice_hold_count,
                    status.pending_merge_pr,
                    1 if status.merge_heal_attempted else 0,
                    status.last_eval_verdict,
                    status.last_eval_at,
                    status.last_eval_note,
                    status.last_progress_at,
                    1 if status.no_progress_notified else 0,
                    in_flight_ref_id,
                    in_flight_kind,
                    in_flight_json,
                    _now_ms(),
                ),
            )
            self._store._commit()

    #: telemetry-only GoalStatus fields GoalStore.update_status_fields() may
    #: touch via :meth:`update_columns` — a column-only UPDATE, never a
    #: full-row rewrite (the mechanism that keeps a stale-snapshot bookkeeping
    #: write from ever clobbering a concurrent phase/lifecycle/in_flight
    #: transition). Keys are GoalStatus field names; values are the
    #: `goal_status` column name (identical today, kept as a mapping so a
    #: future rename only touches one side).
    STATUS_FIELD_COLUMNS: "dict[str, str]" = {
        "last_plan_at": "last_plan_at",
        "last_tick_at": "last_tick_at",
        "last_progress_at": "last_progress_at",
        "no_progress_notified": "no_progress_notified",
        "last_eval_verdict": "last_eval_verdict",
        "last_eval_at": "last_eval_at",
        "last_eval_note": "last_eval_note",
        "donegate_rounds": "donegate_rounds",
        # spec 020: the env-cap adapted-re-dispatch budget — bookkeeping the
        # goal loop stamps beside heal_attempts, never read by derive_state.
        "envcap_redispatches": "envcap_redispatches",
        # issue #728: consecutive dispatch-gate hold ticks (slice guard) —
        # bookkeeping stamped by the tick, never read by derive_state.
        "slice_hold_count": "slice_hold_count",
        # heal_attempts / next_heal_at are damping bookkeeping (never read by
        # derive_state) — the column-only path exists so the auto-heal's
        # gave-up marker and the prep-recheck backoff window can be stamped
        # on a still-BLOCKED goal without a full-row rewrite.
        "heal_attempts": "heal_attempts",
        "next_heal_at": "next_heal_at",
        # #430: the settle path stamps the unmerged-PR marker column-only (after
        # the atomic ACTION_SETTLED write, once the merge attempt's outcome is
        # known) — a telemetry field derive_state never reads.
    }

    def update_columns(self, goal_id: str, fields: dict) -> None:
        """Column-only ``UPDATE`` for telemetry fields — the mechanism behind
        :meth:`GoalStore.update_status_fields`. Bumps ``version`` by 1 like
        every other write, but touches ONLY the named columns (never phase/
        lifecycle/in_flight/blocked_on/next), so it can never be the write
        that clobbers a concurrent state transition. Caller has already
        validated ``fields`` keys against :data:`STATUS_FIELD_COLUMNS`; a
        no-op (no SQL issued) on an empty dict."""
        if not fields:
            return
        sets = []
        params: list = []
        for key, value in fields.items():
            col = self.STATUS_FIELD_COLUMNS[key]
            if key == "no_progress_notified":
                value = 1 if value else 0
            sets.append(f"{col} = ?")
            params.append(value)
        params.append(_now_ms())
        params.append(goal_id)
        with self._store._lock:
            self._store._db.execute(
                f"UPDATE goal_status SET {', '.join(sets)}, version = version + 1, "
                "updated_at = ? WHERE goal_id = ?",
                params,
            )
            self._store._commit()

    # ---- goal_phase_history (append-only phase transitions) ----------------

    def read_phase_history(self, goal_id: str) -> "tuple[dict, ...]":
        """The goal's phase transitions in append order — the tuple that lands
        on ``GoalStatus.phase_history`` and in the STATUS.md view."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT phase, at FROM goal_phase_history WHERE goal_id = ? ORDER BY id ASC",
                (goal_id,),
            ).fetchall()
        return tuple({"phase": r["phase"], "at": r["at"]} for r in rows)

    def append_phase_history(self, goal_id: str, phase: str, at: str) -> None:
        """Append one phase transition. Called by save_status when the phase
        differs from what's stored (one entry per entry-to-a-new-phase)."""
        with self._store._lock:
            self._store._db.execute(
                "INSERT INTO goal_phase_history (goal_id, phase, at) VALUES (?, ?, ?)",
                (goal_id, phase, at),
            )
            self._store._commit()

    def count_verifying_rounds(self, goal_id: str) -> int:
        """How many done-gate proposals this goal has made over its LIFE —
        every proposal enters the ``verifying`` phase exactly once, and
        phase history is append-only, so this count survives the
        ``donegate_rounds`` streak resets a human steer/resume performs."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT COUNT(*) AS n FROM goal_phase_history "
                "WHERE goal_id = ? AND phase = 'verifying'",
                (goal_id,),
            ).fetchone()
        return int(row["n"] if row else 0)

    def record_convergence(
        self, goal_id: str, *, outcome: str, rounds: int,
        workspace_dir: "str | None", closed_at: str,
    ) -> None:
        """INSERT the goal's one terminal convergence row (spec 018 US1).
        ``INSERT OR IGNORE``: terminal is terminal — a duplicate write (e.g.
        a cancel raced against a close) keeps the first row rather than
        clobbering it."""
        with self._store._lock:
            self._store._db.execute(
                "INSERT OR IGNORE INTO goal_convergence "
                "(goal_id, outcome, rounds, workspace_dir, closed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (goal_id, outcome, rounds, workspace_dir, closed_at),
            )
            self._store._commit()

    def seed_phase_history(self, goal_id: str, entries: "tuple[dict, ...]") -> None:
        """Bulk-insert existing phase entries verbatim — the lazy migration path
        that carries a pre-cutoff STATUS.md's phase_history onto the table."""
        if not entries:
            return
        with self._store._lock:
            self._store._db.executemany(
                "INSERT INTO goal_phase_history (goal_id, phase, at) VALUES (?, ?, ?)",
                [(goal_id, str(e["phase"]), str(e["at"])) for e in entries],
            )
            self._store._commit()


def _row_to_status(row, phase_history: "tuple[dict, ...]") -> GoalStatus:
    """Reconstruct a :class:`GoalStatus` from a ``goal_status`` row + its phase
    history. Mirrors the field-by-field degrade of the old STATUS.md reader so a
    migrated goal loads identically."""
    in_flight = None
    if row["in_flight_json"]:
        f = json.loads(row["in_flight_json"])
        in_flight = InFlight(
            engine=f["engine"],
            tool=f["tool"],
            id=f["id"],
            ref_kind=f["ref_kind"],
            goal=f.get("goal", ""),
            is_done_check=bool(f.get("is_done_check", False)),
        )
    return GoalStatus(
        phase=row["phase"] or "idle",
        lifecycle=row["lifecycle"] or None,
        in_flight=in_flight,
        blocked_on=row["blocked_on"] or None,
        # NULL on a row that predates the column (pre-blocked_kind DB, lazily
        # ALTERed by _bootstrap) reads as "" — unclassified, same as the default.
        blocked_kind=row["blocked_kind"] or "",
        heal_attempts=int(row["heal_attempts"] or 0),
        envcap_redispatches=int(row["envcap_redispatches"] or 0),
        # NULL on a pre-spec-025 row (lazily ALTERed) reads as the defaults.
        pending_merge_pr=row["pending_merge_pr"] or "",
        merge_heal_attempted=bool(row["merge_heal_attempted"]),
        next_heal_at=row["next_heal_at"] or None,
        next=row["next"] or "",
        last_plan_at=row["last_plan_at"] or None,
        last_tick_at=row["last_tick_at"] or None,
        actions_dispatched=int(row["actions_dispatched"] or 0),
        donegate_rounds=int(row["donegate_rounds"] or 0),
        slice_hold_count=int(row["slice_hold_count"] or 0),
        last_eval_verdict=row["last_eval_verdict"] or None,
        last_eval_at=row["last_eval_at"] or None,
        last_eval_note=row["last_eval_note"] or "",
        last_progress_at=row["last_progress_at"] or None,
        no_progress_notified=bool(row["no_progress_notified"]),
        phase_history=phase_history,
        state=row["state"] or None,
        version=int(row["version"] or 0),
    )
