"""Observability logs — the two high-volume append-only tables the store
owns: ``events`` (raw runner SDK events, one row per agent action — the SSE
resume stream) and ``traces`` (per-tick observability: cognition / dispatch /
delivery / subprocess events), plus the retention prunes that keep both from
outgrowing the state they observe.

Split out of ``StateStore`` as a mixin on the SAME instance — every method here
runs against the ``self._db`` / ``self._lock`` / ``self._commit`` the core store
owns, so the single-connection / single-writer semantics are byte-identical to
the pre-split monolith. (The VACUUM / DB-size alarm siblings stay in ``core.py``
— they maintain the whole file, not these two tables.)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from .. import config as _config
from .rows import TaskEvent, _now_ms, _row_to_event

if TYPE_CHECKING:
    import sqlite3
    import threading

# ---- retention (volume hygiene) ---------------------------------------------
# Production evidence: a live devclaw.db reached 402MB with 200k+ trace rows —
# telemetry must not outgrow the state it observes. Two append-only, high-volume
# logs are pruned on an identical schedule: `traces` (per-tick observability,
# 2026-07-15) and `events` (raw runner SDK events, one row per agent action —
# the highest-volume table after traces, bounded 2026-07-18). The heartbeat
# calls :meth:`maybe_prune_traces` + :meth:`maybe_prune_events` on its cheap
# path; both route through the same table-agnostic :meth:`_maybe_prune_table`
# core — pure SQLite (zero LLM calls) and batched so a first prune of a huge
# backlog can never wedge a tick.

#: Days of trace history to keep when ``DEVCLAW_TRACE_RETENTION_DAYS`` is unset.
TRACE_RETENTION_DAYS_DEFAULT = 30
#: Days of event history to keep when ``DEVCLAW_EVENTS_RETENTION_DAYS`` is unset.
EVENTS_RETENTION_DAYS_DEFAULT = 30
#: Days a settled task keeps its full ``result_json`` transcript when
#: ``DEVCLAW_TASK_RESULT_RETENTION_DAYS`` is unset. Transcript-class data, so
#: it matches the events default; the settle summary (status/error/pr_url) and
#: the ``eval_outcomes`` projection are permanent and are never touched.
TASK_RESULT_RETENTION_DAYS_DEFAULT = 30
#: Max rows deleted per prune call — one bounded batch per heartbeat tick until
#: the backlog drains, so a 400MB first prune spreads across ticks.
TRACE_PRUNE_BATCH = 5000
#: A new prune cycle (per table) starts at most once per day (watermark in ``meta``).
_TRACE_PRUNE_INTERVAL_MS = 24 * 3600 * 1000
#: meta key holding the epoch-ms of the last COMPLETED (drained) trace prune cycle.
_TRACE_PRUNE_META_KEY = "trace_prune_last_ms"
#: meta key holding the epoch-ms of the last COMPLETED (drained) task-result
#: compaction cycle.
_TASK_RESULT_COMPACT_META_KEY = "task_result_compact_last_ms"
#: meta key holding the epoch-ms of the last COMPLETED (drained) events prune cycle.
_EVENTS_PRUNE_META_KEY = "events_prune_last_ms"


def _parse_retention_days(raw: Optional[str], default: int) -> int:
    """Parse a retention-days env value with the fail-safe semantics shared by
    every retention surface: unset/blank → ``default``; ``0``, a negative value,
    or anything unparseable → ``0`` (retention disabled, gracefully — a typo in
    an env var must never make a prune delete aggressively or crash the
    heartbeat). Callers read the env live via :mod:`devclaw.config` so the
    read stays a literal the doc-sync test (test_env_vars_doc_sync.py) can see."""
    if raw is None or not raw.strip():
        return default
    try:
        days = int(raw.strip())
    except ValueError:
        return 0
    return days if days > 0 else 0


def trace_retention_days() -> int:
    """Trace retention in days from ``DEVCLAW_TRACE_RETENTION_DAYS`` (see
    :func:`_parse_retention_days`)."""
    return _parse_retention_days(
        _config.trace_retention_days_raw(), TRACE_RETENTION_DAYS_DEFAULT
    )


def events_retention_days() -> int:
    """Event retention in days from ``DEVCLAW_EVENTS_RETENTION_DAYS`` (see
    :func:`_parse_retention_days`)."""
    return _parse_retention_days(
        _config.events_retention_days_raw(), EVENTS_RETENTION_DAYS_DEFAULT
    )


def task_result_retention_days() -> int:
    """Settled-task result retention in days from
    ``DEVCLAW_TASK_RESULT_RETENTION_DAYS`` (see :func:`_parse_retention_days`)."""
    return _parse_retention_days(
        _config.task_result_retention_days_raw(), TASK_RESULT_RETENTION_DAYS_DEFAULT
    )


class ObservabilityMixin:
    if TYPE_CHECKING:
        # The composing class owns these (its docstring names the same contract in
        # prose); declared under TYPE_CHECKING so the seam is checked, never run.
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...
        def get_meta(self, key: str) -> Optional[str]: ...
        def set_meta(self, key: str, value: str) -> None: ...

    # ---- events ---------------------------------------------------------

    def append_event(
        self,
        *,
        task_id: str,
        type: str,
        source: str,
        payload_json: str,
        ts: Optional[int] = None,
    ) -> int:
        """Append one event row. Returns the auto-assigned monotonic id, which
        the SSE layer uses as the resume cursor (Last-Event-Id).

        ``ts`` is normalized to MILLISECONDS here, at the single writer: the
        runner (a deployed sandbox image, which can lag this host) has emitted
        seconds-scale ``time.time()`` values, and a seconds ts in the ms column
        reads as 1970 — which the retention prune then deletes as ancient.
        ``10**12`` ms is 2001-09; every real seconds value sits far below it,
        every real ms value far above."""
        if ts is not None and 0 < ts < 10**12:
            ts = int(ts * 1000)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (task_id, type, source, payload_json, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, type, source, payload_json, ts if ts is not None else _now_ms()),
            )
            self._commit()
            assert cur.lastrowid is not None  # INSERT always assigns a rowid
            return int(cur.lastrowid)

    # ---- traces (per-tick observability) --------------------------------

    def append_trace_event(
        self,
        *,
        trace_id: str,
        goal_id: str,
        kind: str,
        payload: dict,
        ts: Optional[int] = None,
    ) -> int:
        """Persist one trace event (cognition / tick / dispatch / delivery /
        subprocess / notify / note). Best-effort by convention — callers should
        not propagate exceptions out of telemetry. Returns the monotonic id."""
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO traces (trace_id, goal_id, kind, ts, payload_json) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    trace_id,
                    goal_id,
                    kind,
                    ts if ts is not None else _now_ms(),
                    json.dumps(payload, default=str),
                ),
            )
            self._commit()
            assert cur.lastrowid is not None  # INSERT always assigns a rowid
            return int(cur.lastrowid)

    def read_traces(
        self,
        *,
        goal_id: Optional[str] = None,
        since_id: int = 0,
        limit: int = 500,
        kind: Optional[str] = None,
        role: Optional[str] = None,
        since_ms: Optional[int] = None,
        errors_only: bool = False,
        newest_first: bool = False,
    ) -> list[dict]:
        """Read trace events in emission order. Pure SELECT — every filter is
        applied in SQL (the production table holds 200k+ rows; loading-then-
        filtering in Python is not an option). ``goal_id``/``kind`` ride their
        indexes; ``since_ms`` rides ``idx_traces_ts``; ``role`` (cognition
        payload field) and ``errors_only`` (non-empty ``error`` payload field)
        use ``json_extract`` over the already-narrowed row set.

        Pass ``since_id`` to resume after a known cursor (exclusive);
        ``newest_first=True`` flips the ordering to ``id DESC`` so "the last N
        matching events" is one indexed query, not a full-table read."""
        sql = (
            "SELECT id, trace_id, goal_id, kind, ts, payload_json FROM traces "
            "WHERE id > ?"
        )
        args: list[object] = [since_id]
        if goal_id:
            sql += " AND goal_id = ?"
            args.append(goal_id)
        if kind:
            sql += " AND kind = ?"
            args.append(kind)
        if since_ms is not None:
            sql += " AND ts >= ?"
            args.append(int(since_ms))
        if role:
            sql += " AND json_extract(payload_json, '$.role') = ?"
            args.append(role)
        if errors_only:
            sql += " AND COALESCE(json_extract(payload_json, '$.error'), '') != ''"
        sql += f" ORDER BY id {'DESC' if newest_first else 'ASC'} LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, tuple(args)).fetchall()
        return [
            {
                "id": r["id"],
                "trace_id": r["trace_id"],
                "goal_id": r["goal_id"],
                "kind": r["kind"],
                "ts": r["ts"],
                "payload": json.loads(r["payload_json"]),
            }
            for r in rows
        ]

    def trace_totals(self, *, goal_id: str) -> dict:
        """Aggregate stats over all trace events for a goal: counts per kind,
        cognition total latency, tokens, and cost. Cheap SQL — no LLM call.

        Token totals prefer REAL usage (recorded from the CLI's json
        envelope) per row; a row without one — stub cognition, an errored call,
        the raw-stdout degrade path — contributes its len/4 estimate, and
        ``cognition_rows_estimated`` says how many rows in the total are
        estimates."""
        with self._lock:
            counts = dict(
                self._db.execute(
                    "SELECT kind, COUNT(*) AS n FROM traces WHERE goal_id = ? GROUP BY kind",
                    (goal_id,),
                ).fetchall()
            )
            # cognition aggregates require unpacking payload_json
            cog_rows = self._db.execute(
                "SELECT payload_json FROM traces WHERE goal_id = ? AND kind = 'cognition'",
                (goal_id,),
            ).fetchall()
        latency_ms = 0
        tokens_in = 0
        tokens_out = 0
        rows_with_real = 0
        rows_estimated = 0
        cost_usd = 0.0
        for r in cog_rows:
            try:
                p = json.loads(r["payload_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            latency_ms += int(p.get("latency_ms") or 0)
            real_in, real_out = p.get("tokens_in"), p.get("tokens_out")
            if real_in is not None or real_out is not None:
                rows_with_real += 1
                tokens_in += int(real_in or 0)
                tokens_out += int(real_out or 0)
            else:
                rows_estimated += 1
                tokens_in += int(p.get("tokens_in_est") or 0)
                tokens_out += int(p.get("tokens_out_est") or 0)
            c = p.get("cost_usd")
            if isinstance(c, (int, float)) and not isinstance(c, bool):
                cost_usd += float(c)
        return {
            "events_by_kind": {k: int(v) for k, v in counts.items()},
            "cognition_total_latency_ms": latency_ms,
            "cognition_tokens_in": tokens_in,
            "cognition_tokens_out": tokens_out,
            "cognition_rows_with_real_usage": rows_with_real,
            "cognition_rows_estimated": rows_estimated,
            "cognition_cost_usd": round(cost_usd, 6),
        }

    def _prune_table_batch(self, *, table: str, older_than_ms: int, limit: int) -> int:
        """Delete up to ``limit`` of the OLDEST rows in ``table`` with ``ts``
        before ``older_than_ms``. Returns the number of rows deleted. One
        bounded batch — the caller loops across heartbeat ticks (via
        :meth:`_maybe_prune_table`) rather than holding the write lock long
        enough to wedge a tick on a 200k-row backlog.

        ``table`` is a fixed module-controlled literal (``traces`` / ``events``),
        never user input, and both tables share the ``id`` PK + monotonic ``ts``
        shape this query relies on (ordering by ``id`` reads the oldest rows off
        the front of the PK)."""
        with self._lock:
            cur = self._db.execute(
                f"DELETE FROM {table} WHERE id IN ("  # noqa: S608 — fixed literal, not user input
                f"SELECT id FROM {table} WHERE ts < ? ORDER BY id ASC LIMIT ?)",
                (older_than_ms, limit),
            )
            self._commit()
            return int(cur.rowcount)

    def _maybe_prune_table(
        self,
        *,
        table: str,
        meta_key: str,
        retention_days: int,
        now_ms: int,
        batch_limit: int,
    ) -> int:
        """Table-agnostic retention prune — the shared core behind
        :meth:`maybe_prune_traces` and :meth:`maybe_prune_events` (StateStore
        owns both logs' writes, so the prune lives here beside them, not in a
        second writer).

        Semantics:
          * disabled (``retention_days`` <= 0) → no-op, returns 0;
          * a new prune CYCLE starts at most once per ``_TRACE_PRUNE_INTERVAL_MS``
            (daily), gated by the per-table ``meta_key`` watermark;
          * each call deletes at most ``batch_limit`` rows; the watermark is
            advanced only when a batch comes back short (backlog drained), so
            an oversized first prune drains one bounded batch per tick instead
            of blocking a single tick for the whole 400MB table.

        Pure SQLite — zero LLM calls, safe on the zero-token idle path."""
        if retention_days <= 0:
            return 0
        raw = self.get_meta(meta_key)
        try:
            last = int(raw) if raw else 0
        except ValueError:
            last = 0
        if last and (now_ms - last) < _TRACE_PRUNE_INTERVAL_MS:
            return 0
        deleted = self._prune_table_batch(
            table=table, older_than_ms=now_ms - retention_days * 24 * 3600 * 1000,
            limit=batch_limit,
        )
        if deleted < batch_limit:
            # Drained — stamp the watermark so the next cycle waits a day.
            # A full batch leaves the watermark alone: more rows may remain,
            # and the next tick continues the drain.
            self.set_meta(meta_key, str(now_ms))
        return deleted

    def maybe_prune_traces(
        self,
        *,
        now_ms: Optional[int] = None,
        retention_days: Optional[int] = None,
        batch_limit: int = TRACE_PRUNE_BATCH,
    ) -> int:
        """Retention prune for the traces table — the heartbeat's cheap-path
        maintenance hook. Thin wrapper over :meth:`_maybe_prune_table`."""
        days = trace_retention_days() if retention_days is None else retention_days
        now = _now_ms() if now_ms is None else now_ms
        return self._maybe_prune_table(
            table="traces", meta_key=_TRACE_PRUNE_META_KEY,
            retention_days=days, now_ms=now, batch_limit=batch_limit,
        )

    def maybe_prune_events(
        self,
        *,
        now_ms: Optional[int] = None,
        retention_days: Optional[int] = None,
        batch_limit: int = TRACE_PRUNE_BATCH,
    ) -> int:
        """Retention prune for the events table (raw runner SDK events, one row
        per agent action — the highest-volume append-only log after traces).
        The heartbeat's cheap-path maintenance hook, beside the trace prune.
        Thin wrapper over :meth:`_maybe_prune_table`."""
        days = events_retention_days() if retention_days is None else retention_days
        now = _now_ms() if now_ms is None else now_ms
        return self._maybe_prune_table(
            table="events", meta_key=_EVENTS_PRUNE_META_KEY,
            retention_days=days, now_ms=now, batch_limit=batch_limit,
        )

    def maybe_compact_task_results(
        self,
        *,
        now_ms: Optional[int] = None,
        retention_days: Optional[int] = None,
        batch_limit: int = TRACE_PRUNE_BATCH,
    ) -> int:
        """Retention COMPACTION for settled tasks' ``result_json`` — the full
        worker transcript, the biggest payload in the DB (2026-08-30 audit:
        83.5MB of 277MB). Unlike the prunes this deletes no rows: it NULLs
        ``result_json`` on done/failed/cancelled tasks whose ``completed_at``
        is past retention, leaving status/error/pr_url/goal and every other
        column untouched (the settle summary and the ``eval_outcomes``
        projection are the permanent record). Running/pending rows are never
        touched regardless of age. Same operational envelope as the prunes:
        one cycle per day (own watermark), batched, pure SQLite, zero LLM,
        ``retention_days <= 0`` disables. Returns rows compacted this call."""
        days = task_result_retention_days() if retention_days is None else retention_days
        if days <= 0:
            return 0
        now = _now_ms() if now_ms is None else now_ms
        raw = self.get_meta(_TASK_RESULT_COMPACT_META_KEY)
        try:
            last = int(raw) if raw else 0
        except ValueError:
            last = 0
        if last and (now - last) < _TRACE_PRUNE_INTERVAL_MS:
            return 0
        cutoff = now - days * 24 * 3600 * 1000
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET result_json = NULL WHERE id IN ("
                "  SELECT id FROM tasks"
                "  WHERE status IN ('done', 'failed', 'cancelled')"
                "    AND result_json IS NOT NULL"
                "    AND completed_at IS NOT NULL AND completed_at < ?"
                "  LIMIT ?)",
                (cutoff, batch_limit),
            )
            self._commit()
        compacted = cur.rowcount if cur.rowcount is not None and cur.rowcount > 0 else 0
        if compacted < batch_limit:
            # Drained — stamp the watermark so the next cycle waits a day; a
            # full batch leaves it alone so the next tick continues the drain.
            self.set_meta(_TASK_RESULT_COMPACT_META_KEY, str(now))
        return compacted

    def list_events(
        self,
        *,
        task_id: str,
        since_id: Optional[int] = None,
        limit: int = 500,
    ) -> list[TaskEvent]:
        """List events for a task in id (emission) order. Pass ``since_id``
        to resume after a known cursor (exclusive)."""
        where: list[str] = ["task_id = ?"]
        args: list[object] = [task_id]
        if since_id is not None:
            where.append("id > ?")
            args.append(since_id)
        args.append(limit)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM events WHERE {' AND '.join(where)} ORDER BY id ASC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [_row_to_event(r) for r in rows]
