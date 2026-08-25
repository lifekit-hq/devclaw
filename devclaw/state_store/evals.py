"""Continuous-eval projections (ADR 0006) — the ``eval_outcomes`` rows
materialized when a task settles (plus the basket-ingest path) and the
per-cycle ``cycle_reports`` the heartbeat writes at window close.

Split out of ``StateStore`` as a mixin on the SAME instance — every method here
runs against the ``self._db`` / ``self._lock`` / ``self._commit`` the core store
owns, so the single-connection / single-writer semantics are byte-identical to
the pre-split monolith. ``_insert_live_outcome`` is called by the settle methods
in ``core.py`` inside their own lock/commit — the projection row and the settle
stay one atomic unit.
"""

from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING, Optional

from .rows import _now_ms, derive_failure_class

if TYPE_CHECKING:
    import sqlite3
    import threading

#: The retry loop's terminal-escalation suffix ("… (failed after N attempts)") —
#: the one place the attempt count survives into what the store sees at settle
#: time, so the eval_outcomes projection parses it back out. Best-effort: fail-
#: fast paths (timeout, review crash, worker block) never carry it → NULL.
_ATTEMPTS_SUFFIX_RE = re.compile(r"\(failed after (\d+) attempts\)\s*$")

#: Raw error text is truncated to this many chars in eval_outcomes rows — the
#: full text stays on the task row; the projection only needs enough to read.
_EVAL_ERROR_MAX_CHARS = 500


class EvalOutcomesMixin:
    if TYPE_CHECKING:
        # The composing class owns these (its docstring names the same contract in
        # prose); declared under TYPE_CHECKING so the seam is checked, never run.
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...

    # ---- eval outcomes (continuous-eval projection, ADR 0006) -----------

    def _insert_live_outcome(
        self,
        task_id: str,
        *,
        status: str,
        result_json: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        """Materialize the ``eval_outcomes`` projection row for a task that just
        settled. Called by mark_done/mark_failed/mark_task_cancelled INSIDE their
        lock, AFTER the settle UPDATE (so the row read back already carries the
        final pr_url/completed_at) and BEFORE their ``_commit`` — the insert
        shares the settle's commit, so a settle and its projection row are one
        atomic unit. Exactly-once: callers only invoke this when the settle
        UPDATE moved a row, and the partial unique index on (task_id) makes a
        duplicate structurally an IGNORE.

        Everything is derived from what the store already knows at settle time,
        mechanically (zero LLM):
          * ``verify_passed`` — the result's verify block (done), or 0 when the
            error buckets as ``verify_failed``; NULL = no gate produced a verdict;
          * ``failure_class`` — :func:`rows.derive_failure_class` string bucketing;
          * ``attempts`` — parsed from the retry loop's terminal "(failed after
            N attempts)" suffix; fail-fast paths carry no count → NULL;
          * ``wall_ms`` — completed_at − started_at from the row itself.

        Best-effort: a projection hiccup logs and is dropped — it must never
        unsettle the task (the settle UPDATE still commits)."""
        try:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return
            verify_passed: Optional[int] = None
            if result_json:
                try:
                    verify = (json.loads(result_json) or {}).get("verify") or {}
                    if verify.get("ran"):
                        verify_passed = 1 if verify.get("passed") else 0
                except (TypeError, ValueError):
                    pass
            failure_class: Optional[str] = None
            if status == "failed":
                failure_class = derive_failure_class(error)
                if failure_class == "verify_failed" and verify_passed is None:
                    verify_passed = 0
            attempts: Optional[int] = None
            if error:
                m = _ATTEMPTS_SUFFIX_RE.search(error)
                if m:
                    attempts = int(m.group(1))
            completed, started = row["completed_at"], row["started_at"]
            wall_ms = (completed - started) if (completed and started) else None
            self._db.execute(
                "INSERT OR IGNORE INTO eval_outcomes "
                "(source, task_id, goal_id, program_id, kind, workspace_dir, "
                " status, verify_passed, pr_url, attempts, wall_ms, "
                " failure_class, error, settled_at) "
                "VALUES ('live', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    row["parent_goal_id"],
                    row["program_id"],
                    row["kind"],
                    row["workspace_dir"],
                    status,
                    verify_passed,
                    row["pr_url"],
                    attempts,
                    wall_ms,
                    failure_class,
                    (error or "")[:_EVAL_ERROR_MAX_CHARS] or None,
                    completed if completed else _now_ms(),
                ),
            )
            if row["pr_url"]:
                # PR ledger (spec 018 US2): first sight of a delivered PR URL
                # creates its one row — INSERT OR IGNORE, so every later
                # increment sharing the cumulative goal-branch PR is a no-op.
                # Shares the settle's commit like the outcome row above.
                self._db.execute(
                    "INSERT OR IGNORE INTO pr_ledger "
                    "(pr_url, workspace_dir, opened_at_ms) VALUES (?, ?, ?)",
                    (row["pr_url"], row["workspace_dir"], completed if completed else _now_ms()),
                )
        except Exception as err:  # noqa: BLE001 — telemetry must never unsettle
            sys.stderr.write(
                f"state-store: eval_outcomes projection failed task={task_id}: {err}\n"
            )

    def record_basket_outcome(
        self,
        *,
        report_ref: str,
        ticket: str,
        status: str,
        task_id: Optional[str] = None,
        kind: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        verify_passed: Optional[bool] = None,
        pr_url: Optional[str] = None,
        wall_ms: Optional[int] = None,
        error: Optional[str] = None,
        settled_at: Optional[int] = None,
    ) -> bool:
        """Insert one ``source='basket'`` eval_outcomes row from a
        measure_passrate report record. Idempotent on (source, report_ref,
        ticket) via the partial unique index — re-ingesting the same report is
        a no-op. Returns True iff a NEW row was inserted. ``failure_class`` is
        derived here with the same mechanical bucketing live rows use."""
        failure_class = derive_failure_class(error) if status == "failed" else None
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO eval_outcomes "
                "(source, task_id, ticket, kind, workspace_dir, status, "
                " verify_passed, pr_url, wall_ms, failure_class, error, "
                " report_ref, settled_at) "
                "VALUES ('basket', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    ticket,
                    kind,
                    workspace_dir,
                    status,
                    None if verify_passed is None else (1 if verify_passed else 0),
                    pr_url,
                    wall_ms,
                    failure_class,
                    (error or "")[:_EVAL_ERROR_MAX_CHARS] or None,
                    report_ref,
                    settled_at if settled_at is not None else _now_ms(),
                ),
            )
            self._commit()
            return cur.rowcount == 1

    def list_eval_outcomes(
        self, *, source: Optional[str] = None, limit: int = 100
    ) -> list[dict]:
        """Recent eval_outcomes rows, newest settle first — the read surface
        the console/cycle-report layers (PR2/PR3) and tests project from.
        Plain dicts, pure SELECT."""
        where = "WHERE source = ?" if source else ""
        args: tuple = (source, limit) if source else (limit,)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM eval_outcomes {where} "
                "ORDER BY settled_at DESC, id DESC LIMIT ?",
                args,
            ).fetchall()
        return [dict(r) for r in rows]

    def get_eval_outcome(self, id: int) -> Optional[dict]:
        """One eval_outcomes row by integer primary key. Returns None when the
        id is unknown — the detail endpoint 404s rather than 500ing."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM eval_outcomes WHERE id = ?", (id,)
            ).fetchone()
        return dict(row) if row else None

    # ---- PR ledger (spec 018 US2) ---------------------------------------

    #: env-free constants for the once-per-cycle refresh: only PRs opened in
    #: the last WINDOW days are worth a platform read (older undecided rows
    #: stay `unknown`/`open` as last stamped), and the CAP bounds the gh
    #: subprocess count per cycle — a hit is reported loudly, never silent
    #: (Principle VI). US4 wires the window to the ratchet config.
    PR_REFRESH_WINDOW_DAYS = 30
    PR_REFRESH_CAP = 50
    PR_REFRESH_META_KEY = "pr_ledger_refresh"

    def undecided_pr_urls(self, *, since_ms: int, limit: int) -> "tuple[list[str], bool]":
        """URLs still worth a platform read — everything except ``merged``
        (the one truly terminal state: a rejected PR can be REOPENED, so it
        stays in the refresh set and re-enters ``open`` when that happens) —
        opened in the window, newest first, capped. Returns
        ``(urls, truncated)``."""
        with self._lock:
            rows = self._db.execute(
                "SELECT pr_url FROM pr_ledger "
                "WHERE state != 'merged' AND opened_at_ms >= ? "
                "ORDER BY opened_at_ms DESC LIMIT ?",
                (since_ms, limit + 1),
            ).fetchall()
        urls = [r["pr_url"] for r in rows]
        return urls[:limit], len(urls) > limit

    def upsert_pr_states(self, states: "dict[str, str]", *, as_of_ms: int, truncated: bool) -> None:
        """The refresh step's single write: stamp each successfully-read state
        (+ its as-of), and persist the refresh summary in meta so the
        scorecard can report staleness and cap-truncation honestly. A URL
        whose read failed is NOT stamped — its previous state and as-of stand,
        which is exactly what the staleness stamp then shows."""
        with self._lock:
            for url, state in states.items():
                self._db.execute(
                    "UPDATE pr_ledger SET state = ?, state_as_of_ms = ? WHERE pr_url = ?",
                    (state, as_of_ms, url),
                )
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (self.PR_REFRESH_META_KEY,
                 json.dumps({"at_ms": as_of_ms, "truncated": bool(truncated)})),
            )
            self._commit()

    # ---- cycle reports (continuous-eval PR2, ADR 0006) ------------------

    def cycle_report_exists(self, cycle_key: str) -> bool:
        """Whether a cycle_reports row already exists for ``cycle_key`` (the
        YYYY-MM-DD of the window OPEN). The heartbeat's idempotency guard: the
        window-close edge checks this before assembling anything, so the report
        fires exactly once per cycle no matter how many wakeups land after the
        window closes. Pure SELECT — zero LLM, cheap enough for the idle path."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM cycle_reports WHERE cycle_key = ? LIMIT 1",
                (cycle_key,),
            ).fetchone()
        return row is not None

    def record_cycle_report(
        self,
        *,
        cycle_key: str,
        window_start_ms: int,
        window_end_ms: int,
        clean: bool,
        wedges_json: str,
        pauses_json: str,
        summary: str,
        sent_at: Optional[int] = None,
        idle: bool = False,
    ) -> bool:
        """Persist ONE cycle-window report (the layer-2 heartbeat calls this —
        single-writer: cycle_reports is only ever written here). Idempotent on
        ``cycle_key`` (PRIMARY KEY) via INSERT OR IGNORE — a second write for
        the same cycle is a no-op, so a racing/duplicate window-close edge can't
        double-report. Returns True iff a NEW row was inserted. ``sent_at`` NULL
        means the notifier didn't confirm the push (unconfigured / failed) — a
        log-only report, never an error. ``idle`` True means the loop did no work
        this cycle (off/held/all-cancelled) — the row is excluded from the
        clean-cycle rate; defaulted False so older callers stay byte-identical."""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO cycle_reports "
                "(cycle_key, window_start_ms, window_end_ms, clean, "
                " wedges_json, pauses_json, summary, sent_at, created_at, idle) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cycle_key,
                    window_start_ms,
                    window_end_ms,
                    1 if clean else 0,
                    wedges_json,
                    pauses_json,
                    summary,
                    sent_at,
                    _now_ms(),
                    1 if idle else 0,
                ),
            )
            self._commit()
            return cur.rowcount == 1

    def list_cycle_reports(self, *, limit: int = 30) -> list[dict]:
        """Recent cycle_reports rows, newest window first — the read surface the
        console Evals tab (PR3) projects the clean-cycle headline + history from.
        Plain dicts, pure SELECT. Rows carry ``idle`` (1 = no work that cycle);
        readers exclude idle rows from the clean-cycle rate."""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM cycle_reports "
                "ORDER BY window_end_ms DESC, created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_cycle_report(self, cycle_key: str) -> Optional[dict]:
        """One cycle_reports row by cycle_key (the YYYY-MM-DD window-open date).
        Returns None when the key is unknown — the detail endpoint 404s."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM cycle_reports WHERE cycle_key = ?", (cycle_key,)
            ).fetchone()
        return dict(row) if row else None
