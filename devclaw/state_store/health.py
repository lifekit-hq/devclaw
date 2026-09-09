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

import json

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

    # ---- intake_grades (US6) ------------------------------------------------

    def record_intake_grade(
        self, *, repo: str, issue_number: int, readiness: str,
        claimed_units: Optional[int] = None, assessed_units: Optional[int] = None,
        sizing: Optional[str] = None, stale: bool = False,
    ) -> None:
        """Persist the grader's prediction for ``(repo, issue_number)`` (FR-021)
        — a re-grade REPLACES the row, so the prediction that stands when a
        goal is created is the latest grade. Pure write; the intake
        orchestrator reaches it through a callback so layer 3 holds no
        store."""
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO intake_grades "
                "(repo, issue_number, readiness, claimed_units, assessed_units, "
                "sizing, stale, graded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(repo), int(issue_number), str(readiness),
                    None if claimed_units is None else int(claimed_units),
                    None if assessed_units is None else int(assessed_units),
                    None if sizing is None else str(sizing),
                    1 if stale else 0, _now_ms(),
                ),
            )
            self._commit()

    def intake_grade(self, repo: str, issue_number: int) -> Optional[dict]:
        with self._lock:
            row = self._db.execute(
                "SELECT repo, issue_number, readiness, claimed_units, assessed_units, "
                "sizing, stale, graded_at FROM intake_grades "
                "WHERE repo = ? AND issue_number = ?",
                (str(repo), int(issue_number)),
            ).fetchone()
        return dict(row) if row else None

    def count_goal_dispatches(self, goal_id: str) -> int:
        """Worker tasks the goal consumed — every task dispatched by the goal
        except the done-gate's read-only review (FR-022's ``dispatches``)."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM tasks "
                "WHERE parent_goal_id = ? AND kind != 'review_repository'",
                (goal_id,),
            ).fetchone()
        return int(row["n"] or 0)

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

    # ---- usage_ledger (US3) -------------------------------------------------

    _USAGE_TOKEN_KEYS = (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_read_tokens", "cache_read_tokens"),
        ("cache_creation_tokens", "cache_creation_tokens"),
    )

    @staticmethod
    def _usage_numbers(usage: object) -> "dict | None":
        """The canonical token/cost numbers out of a runner ``usage`` block, or
        None when the block carries no recognizable non-zero figure — so a
        block of zeros is treated as *not reported* (FR-011), the same rule
        ``acp_client.finalize_usage`` applies on the way out."""
        if not isinstance(usage, dict):
            return None
        out: dict = {}
        any_real = False
        for col, key in LoopHealthMixin._USAGE_TOKEN_KEYS:
            v = usage.get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out[col] = int(v)
                any_real = any_real or int(v) > 0
            else:
                out[col] = None
        c = usage.get("cost_usd")
        out["cost_usd"] = float(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None
        if out["cost_usd"] and out["cost_usd"] > 0:
            any_real = True
        return out if any_real else None

    def record_task_usage(
        self, task_id: str, *, attempt: int, usage: object, usage_source: str = "",
        at_ms: Optional[int] = None,
    ) -> None:
        """One permanent row per worker ATTEMPT (FR-009), written the moment the
        runner returns — before the gates, before delivery — so a retried or
        failed attempt's spend is never lost with the attempt. ``usage=None``
        (or a block of zeros) is a ``reported=0`` row with NULL tokens: absent
        is a fact, never a zero. Identity links (goal, workspace, kind) are
        copied from the task row at write time. Idempotent under
        UNIQUE(source, ref_id, attempt); best-effort like every telemetry
        write — a hiccup never unsettles the task."""
        try:
            nums = self._usage_numbers(usage)
            src = str(usage.get("source") or usage_source or "") if isinstance(usage, dict) else usage_source
            with self._lock:
                row = self._db.execute(
                    "SELECT parent_goal_id, workspace_dir, kind FROM tasks WHERE id = ?",
                    (task_id,),
                ).fetchone()
                self._db.execute(
                    "INSERT OR IGNORE INTO usage_ledger "
                    "(source, ref_id, attempt, goal_id, workspace_dir, kind, reported, "
                    " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
                    " cost_usd, usage_source, at_ms) "
                    "VALUES ('worker', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        task_id, int(attempt),
                        row["parent_goal_id"] if row else None,
                        row["workspace_dir"] if row else None,
                        row["kind"] if row else None,
                        1 if nums else 0,
                        nums["input_tokens"] if nums else None,
                        nums["output_tokens"] if nums else None,
                        nums["cache_read_tokens"] if nums else None,
                        nums["cache_creation_tokens"] if nums else None,
                        nums["cost_usd"] if nums else None,
                        (src or "")[:32],
                        _now_ms() if at_ms is None else int(at_ms),
                    ),
                )
                self._commit()
        except Exception:  # noqa: BLE001 — telemetry never breaks the observed operation
            pass

    def _insert_cognition_usage_row(self, trace_row_id: int, goal_id: str, payload: dict, at_ms: int) -> None:
        """The cognition half of the ledger — called INSIDE append_trace_event's
        lock/commit for kind='cognition' so a trace and its permanent usage
        row are one unit. ``reported`` iff the payload carries REAL usage from
        the CLI envelope (never the len/4 estimate)."""
        real_in, real_out = payload.get("tokens_in"), payload.get("tokens_out")
        reported = real_in is not None or real_out is not None
        c = payload.get("cost_usd")
        cost = float(c) if isinstance(c, (int, float)) and not isinstance(c, bool) else None
        self._db.execute(
            "INSERT OR IGNORE INTO usage_ledger "
            "(source, ref_id, attempt, goal_id, workspace_dir, kind, reported, "
            " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
            " cost_usd, usage_source, at_ms) "
            "VALUES ('cognition', ?, 0, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(trace_row_id), goal_id or None, str(payload.get("role") or "")[:64],
                1 if reported else 0,
                int(real_in or 0) if reported else None,
                int(real_out or 0) if reported else None,
                int(payload.get("cache_read") or 0) if reported and payload.get("cache_read") is not None else None,
                int(payload.get("cache_creation") or 0) if reported and payload.get("cache_creation") is not None else None,
                cost if reported else None,
                "cli_envelope" if reported else "",
                int(at_ms),
            ),
        )

    _BACKFILL_META_KEY = "usage_ledger_backfilled"

    def maybe_backfill_usage_ledger(self) -> int:
        """One-shot backfill (FR-010a): every surviving cognition trace and
        every settled task whose ``result_json`` retention has not yet
        compacted becomes a ledger row. Tasks already compacted get NO row —
        they read as ``tasks_without_record`` on the surfaces, never as zero.
        Watermarked in ``meta`` so it runs once per instance; pure SQL; returns
        rows inserted. Best-effort: a failure leaves the watermark unset so a
        later tick retries."""
        if self.get_meta(self._BACKFILL_META_KEY):  # type: ignore[attr-defined]
            return 0
        inserted = 0
        try:
            with self._lock:
                for r in self._db.execute(
                    "SELECT id, goal_id, ts, payload_json FROM traces WHERE kind = 'cognition'"
                ).fetchall():
                    try:
                        payload = json.loads(r["payload_json"])
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(payload, dict):
                        continue
                    before = self._db.total_changes
                    self._insert_cognition_usage_row(int(r["id"]), r["goal_id"] or "", payload, int(r["ts"]))
                    inserted += self._db.total_changes - before
                for r in self._db.execute(
                    "SELECT id, result_json, completed_at FROM tasks "
                    "WHERE result_json IS NOT NULL AND completed_at IS NOT NULL"
                ).fetchall():
                    try:
                        usage = (json.loads(r["result_json"]) or {}).get("usage")
                    except (TypeError, ValueError):
                        usage = None
                    before = self._db.total_changes
                    self._db.execute(
                        "INSERT OR IGNORE INTO usage_ledger "
                        "(source, ref_id, attempt, goal_id, workspace_dir, kind, reported, "
                        " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
                        " cost_usd, usage_source, at_ms) "
                        "SELECT 'worker', id, 0, parent_goal_id, workspace_dir, kind, ?, ?, ?, ?, ?, ?, ?, completed_at "
                        "FROM tasks WHERE id = ?",
                        self._worker_backfill_params(usage) + (r["id"],),
                    )
                    inserted += self._db.total_changes - before
                self.set_meta(self._BACKFILL_META_KEY, str(_now_ms()))  # type: ignore[attr-defined]
                self._commit()
        except Exception:  # noqa: BLE001 — maintenance must not break the heartbeat
            return inserted
        return inserted

    def _worker_backfill_params(self, usage: object) -> tuple:
        nums = self._usage_numbers(usage)
        src = str(usage.get("source") or "acp")[:32] if isinstance(usage, dict) and nums else ""
        return (
            1 if nums else 0,
            nums["input_tokens"] if nums else None,
            nums["output_tokens"] if nums else None,
            nums["cache_read_tokens"] if nums else None,
            nums["cache_creation_tokens"] if nums else None,
            nums["cost_usd"] if nums else None,
            src,
        )

    def usage_ledger_rows(self, *, since_ms: int = 0, goal_id: Optional[str] = None) -> list[dict]:
        """Ledger rows at or after ``since_ms`` (optionally one goal's), oldest
        first. The read surfaces roll these up; the sums always carry the
        record and reported counts (FR-011/FR-012)."""
        sql = (
            "SELECT id, source, ref_id, attempt, goal_id, workspace_dir, kind, reported, "
            " input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, "
            " cost_usd, usage_source, at_ms FROM usage_ledger WHERE at_ms >= ?"
        )
        params: list = [int(since_ms)]
        if goal_id is not None:
            sql += " AND goal_id = ?"
            params.append(goal_id)
        sql += " ORDER BY id ASC"
        with self._lock:
            rows = self._db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def goal_usage_tokens(self, goal_id: str) -> Optional[int]:
        """Total reported tokens attributed to one goal (spec 039 US6,
        data-model `cost_tokens`) — input + output over that goal's REPORTED
        ledger rows. ``None`` when the goal has no reported row at all: a goal
        whose runs reported nothing costs *unknown*, never 0 (FR-011). Cache
        reads are excluded — they are not fresh consumption."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n, "
                " COALESCE(SUM(COALESCE(input_tokens, 0)), 0) AS tin, "
                " COALESCE(SUM(COALESCE(output_tokens, 0)), 0) AS tout "
                "FROM usage_ledger WHERE goal_id = ? AND reported = 1",
                (goal_id,),
            ).fetchone()
        if row is None or not int(row["n"] or 0):
            return None
        return int(row["tin"] or 0) + int(row["tout"] or 0)

    def backfill_boundary_ms(self) -> Optional[int]:
        """When the ledger was backfilled (ms), or None when it never ran — the
        surfaces show rows before it as "surviving transcripts only"."""
        raw = self.get_meta(self._BACKFILL_META_KEY)  # type: ignore[attr-defined]
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
