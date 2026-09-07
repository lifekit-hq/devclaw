""":class:`LoopHealthMixin` — the single writer of the spec 039 tables.

``loop_spans`` (idle attribution, run-length), ``usage_ledger`` (permanent
per-run usage) and ``intake_grades`` (the grader's prediction). Composed
into :class:`~devclaw.state_store.StateStore`; runs against the
``self._db`` / ``self._lock`` / ``self._commit`` the core store owns.

Domain: STATE (these rows) and MONEY (the ledger). Every method is a pure
SQLite write or read — no cognition, no subprocess — and every write is
idempotent under its key so a retried caller cannot double-count.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .rows import _now_ms

if TYPE_CHECKING:
    import sqlite3
    import threading


#: run-length coalescing tolerance is the caller's (three ticks); the seed
#: span at first sight has no interval to attribute, so it is zero-length.
_UNOBSERVED = "unobserved"


class LoopHealthMixin:
    if TYPE_CHECKING:
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...

    # ---- loop_spans (US1) -------------------------------------------------

    def record_loop_sample(
        self, *, now_ms: int, cause: str, detail: str = "", max_gap_ms: int
    ) -> str:
        """Attribute the interval since the previous heartbeat sweep to
        ``cause`` (FR-004): extend the last span when its cause matches, else
        open a new span at the last span's end. A gap longer than
        ``max_gap_ms`` (a process that was down, a redeploy) is recorded as
        an explicit ``unobserved`` span first — never attributed to whatever
        the restart happens to see. The first sample ever seeds a zero-length
        span. Returns the cause that was recorded for the interval (or
        ``unobserved`` when a gap was closed and the new span merely seeded).
        """
        cause = (cause or "").strip()[:64] or "unobserved"
        detail = (detail or "").strip()[:120]
        with self._lock:
            last = self._db.execute(
                "SELECT id, cause, end_ms FROM loop_spans ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if last is None:
                self._db.execute(
                    "INSERT INTO loop_spans (cause, start_ms, end_ms, ticks, detail) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (cause, now_ms, now_ms, detail),
                )
                self._commit()
                return cause
            prev_end = int(last["end_ms"])
            if now_ms <= prev_end:
                # a clock that did not advance (two pokes in one ms, a
                # clock step back): nothing to attribute, keep the record
                # contiguous by touching nothing.
                return str(last["cause"])
            if max_gap_ms > 0 and (now_ms - prev_end) > max_gap_ms:
                self._db.execute(
                    "INSERT INTO loop_spans (cause, start_ms, end_ms, ticks, detail) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (_UNOBSERVED, prev_end, now_ms, "heartbeat gap"),
                )
                self._db.execute(
                    "INSERT INTO loop_spans (cause, start_ms, end_ms, ticks, detail) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (cause, now_ms, now_ms, detail),
                )
                self._commit()
                return _UNOBSERVED
            if str(last["cause"]) == cause:
                self._db.execute(
                    "UPDATE loop_spans SET end_ms = ?, ticks = ticks + 1, detail = ? "
                    "WHERE id = ?",
                    (now_ms, detail, int(last["id"])),
                )
            else:
                self._db.execute(
                    "INSERT INTO loop_spans (cause, start_ms, end_ms, ticks, detail) "
                    "VALUES (?, ?, ?, 1, ?)",
                    (cause, prev_end, now_ms, detail),
                )
            self._commit()
            return cause

    def list_loop_spans(self, *, since_ms: int, limit: int = 100_000) -> list[dict]:
        """Every span that ends inside or after ``since_ms`` (a span straddling
        the window edge is returned whole; the reader clips it). Oldest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT id, cause, start_ms, end_ms, ticks, detail FROM loop_spans "
                "WHERE end_ms >= ? ORDER BY id ASC LIMIT ?",
                (int(since_ms), int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_loop_span(self) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT id, cause, start_ms, end_ms, ticks, detail FROM loop_spans "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    # ---- problems rollup (US2) ---------------------------------------------

    def self_heal_counts(self, *, since_ms: int) -> dict:
        """Σ recovered / Σ terminal over problems LAST SEEN in the window.
        The counters are lifetime per fingerprint — the read surface says so
        (``basis``); this is the raw material, not the rate."""
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(SUM(recovered_count), 0) AS rec, "
                "COALESCE(SUM(terminal_count), 0) AS term, COUNT(*) AS n "
                "FROM problems WHERE last_seen_ms >= ?",
                (int(since_ms),),
            ).fetchone()
        return {
            "recovered": int(row["rec"] or 0),
            "terminal": int(row["term"] or 0),
            "problems": int(row["n"] or 0),
            "now_ms": _now_ms(),
        }
