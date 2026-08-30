"""SQLite state store for DevClaw tasks — the core append-only event log +
single-writer engine.

Tracks every task DevClaw has been asked to run, its current status, and the
result (or error) once it terminates. ``sqlite3`` is sync; a re-entrant lock
serializes access because FastMCP may touch the store from the event loop and
from background tasks. WAL mode gives concurrent reads with a single writer.

The thin typed ``meta`` wrappers (quota pause, operator hold, run windows,
workspace breaker, trend cooldowns) live on :class:`ControlPlaneMixin` in
``control.py``; the problems catalog on :class:`ProblemsMixin` in
``problems.py``; the machine-filed issue ledger (spec 014) on
:class:`MachineIssuesMixin` in ``machine_issues.py``; the events/traces logs +
their retention prunes on
:class:`ObservabilityMixin` in ``observability.py``; the continuous-eval
projections on :class:`EvalOutcomesMixin` in ``evals.py``; the pure data
(dataclasses + row mappers + literals) lives in ``rows.py``. This module holds
the connection, the transaction machinery, the task CRUD,
scheduling/recovery, and the whole-file maintenance (VACUUM + DB-size alarm).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .. import config as _config
from .control import ControlPlaneMixin
from .evals import EvalOutcomesMixin
from .observability import ObservabilityMixin
from .observability import (  # noqa: F401 — compat re-exports: the retention
    # helpers moved to observability.py with the prune methods; external
    # importers (goal/triage.py, tests) still read them from here.
    events_retention_days,
    trace_retention_days,
)
from .machine_issues import MachineIssuesMixin
from .problems import ProblemsMixin
from .schema import bootstrap as _schema_bootstrap
from .rows import (
    SQLITE_BUSY_TIMEOUT_MS,
    Task,
    TaskKind,
    TaskStatus,
    _now_ms,
    _row_to_task,
)

# ---- VACUUM (reclaim disk the prunes free, 2026-07-18) ----------------------
# The retention prunes DELETE rows but SQLite never returns freed pages to the
# OS on its own — the .db file only ever grows, freed pages are merely reused.
# A periodic VACUUM rebuilds the file so the space the prunes reclaim actually
# comes back. VACUUM rewrites the whole DB (holds the write lock, needs free
# disk ~= file size), so it runs RARELY (weekly) and only when there's real
# reclaim to be had (freelist past a threshold) — never on a healthy DB. Pure
# SQLite, zero LLM, on the heartbeat cheap path beside the prunes.
#: A VACUUM runs at most once per week (watermark in ``meta``).
_VACUUM_INTERVAL_MS = 7 * 24 * 3600 * 1000
#: Only VACUUM when at least this many free pages are reclaimable — at the 4KB
#: default page size, ~40MB. Below it the rewrite cost isn't worth the reclaim.
_VACUUM_MIN_FREELIST_PAGES = 10_000
#: meta key holding the epoch-ms of the last VACUUM cycle CHECK (vacuumed or not).
_VACUUM_META_KEY = "vacuum_last_ms"


# ---- DB-size alarm (loud, not silent, 2026-07-18) ---------------------------
# Retention + VACUUM keep a healthy devclaw.db small, but if something writes
# faster than it prunes (or a prune is misconfigured off), the file grows until
# the VPS disk fills and the whole loop wedges — SILENTLY, because nothing
# watches size. This converts that silent wedge into ONE loud owner ping when
# the .db crosses a threshold (and re-arms when it drops back under). Pure stat,
# zero LLM, on the heartbeat cheap path beside the prunes/VACUUM.
#: Default alert threshold in MB when ``DEVCLAW_DB_SIZE_ALERT_MB`` is unset. The
#: 2026-07 incident that motivated retention was 402MB; 2GB is a clear "this is
#: wrong" line well above any healthy steady state.
DB_SIZE_ALERT_MB_DEFAULT = 2000
#: meta flag: "1" once the owner has been pinged about the current over-threshold
#: episode — cleared when size drops back under, so each crossing pings once.
_DB_SIZE_ALERTED_META_KEY = "db_size_alerted"


def db_size_alert_bytes() -> int:
    """Alert threshold in BYTES from ``DEVCLAW_DB_SIZE_ALERT_MB``. Unset/blank →
    the 2000MB default; ``0``, negative, or unparseable → ``0`` (alarm disabled,
    gracefully — a typo must never crash the heartbeat). The env read is a
    literal so the doc-sync test (test_env_vars_doc_sync.py) sees it."""
    raw = _config.db_size_alert_mb_raw()
    if raw is None or not raw.strip():
        mb = DB_SIZE_ALERT_MB_DEFAULT
    else:
        try:
            mb = int(raw.strip())
        except ValueError:
            return 0
    return mb * 1024 * 1024 if mb > 0 else 0


class StateStore(
    ControlPlaneMixin, ProblemsMixin, MachineIssuesMixin, ObservabilityMixin, EvalOutcomesMixin
):
    def __init__(self, db_path: str) -> None:
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        #: resolved path to the .db file, kept for VACUUM / on-disk size checks.
        self._db_path = str(Path(db_path).expanduser())
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")  # concurrent reads, single writer
        self._db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")  # wait, don't fail-fast
        self._db.execute("PRAGMA foreign_keys = ON")
        self._lock = threading.RLock()
        #: transaction()-nesting depth. 0 == no open transaction, so _commit()
        #: writes immediately (every existing single-write method behaves exactly
        #: as before). > 0 == inside a transaction(): _commit() is a no-op and the
        #: OUTERMOST transaction() issues the single real commit/rollback.
        self._txn_depth = 0
        #: set True when any exception passes through an open transaction() level,
        #: so the outermost level rolls the whole unit back — even if an inner
        #: exception was caught between nested levels.
        self._txn_failed = False
        self._bootstrap()

    @property
    def db_path(self) -> str:
        """Resolved path of the backing SQLite file — doubles as the durable
        identity of this devclaw instance (the sandbox owner-label seed)."""
        return self._db_path

    # ---- transactions ---------------------------------------------------
    #
    # Group several writes into ONE atomic unit spanning multiple store methods
    # (e.g. "create a task row AND stamp the goal's in_flight ref" — the
    # atomicity a later dispatch/orphan-recovery PR needs). Single writes called
    # OUTSIDE a transaction() keep committing immediately, unchanged.

    @contextmanager
    def transaction(self) -> "Iterator[StateStore]":
        """Open an atomic unit. Acquires the store lock for the WHOLE block (so
        no other thread can commit or write while it is open), and defers the
        commit until the OUTERMOST ``transaction()`` exits — nested
        ``transaction()`` calls join the outer one (a depth counter), yielding a
        single commit at depth 0. Any exception at any depth rolls the whole
        unit back.

        Existing single-write methods call :meth:`_commit`, which is a no-op
        while a transaction is open, so a ``create_task`` (or any other write)
        run inside ``transaction()`` becomes part of the atomic unit instead of
        committing on its own.
        """
        with self._lock:
            if self._txn_depth == 0:
                self._txn_failed = False
            self._txn_depth += 1
            try:
                yield self
            except BaseException:
                self._txn_failed = True
                raise
            finally:
                self._txn_depth -= 1
                if self._txn_depth == 0:
                    if self._txn_failed:
                        self._db.rollback()
                    else:
                        self._db.commit()
                    self._txn_failed = False

    def _commit(self) -> None:
        """Commit now, unless a :meth:`transaction` is open. Inside a
        transaction (depth > 0) this is a no-op — the write joins the atomic
        unit and the outermost ``transaction()`` commits once. Outside one
        (depth 0) it commits immediately, so every single-write method keeps its
        original commit-per-call behavior."""
        if self._txn_depth == 0:
            self._db.commit()

    def _bootstrap(self) -> None:
        # The DDL lives in schema.py — one module of schema, this one of behavior.
        _schema_bootstrap(self._db, self._lock, self._commit)

    def create_task(
        self,
        *,
        id: str,
        kind: TaskKind,
        workspace_dir: str,
        goal: str,
        notify_url: Optional[str] = None,
        verify_cmd: Optional[str] = None,
        deliver: bool = False,
        title: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
        scaffold: bool = False,
        strictness: str = "trust",
        base_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO tasks
                     (id, kind, status, workspace_dir, goal, notify_url, created_at,
                      verify_cmd, deliver,
                      title, parent_goal_id, scaffold, strictness,
                      base_branch, target_branch, project_id)
                   VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    id,
                    kind,
                    workspace_dir,
                    goal,
                    notify_url,
                    _now_ms(),
                    verify_cmd,
                    1 if deliver else 0,
                    title,
                    parent_goal_id,
                    1 if scaffold else 0,
                    strictness if strictness in ("trust", "strict") else "trust",
                    base_branch,
                    target_branch,
                    project_id,
                ),
            )
            self._commit()

    def claim_pending(self, task_id: str) -> bool:
        """Atomically transition pending -> running. Returns True if THIS call
        won the race (caller must execute the task), False otherwise."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'running', started_at = ? "
                "WHERE id = ? AND status = 'pending'",
                (_now_ms(), task_id),
            )
            self._commit()
            return cur.rowcount == 1

    def mark_done(self, task_id: str, result_json: str, pr_url: Optional[str] = None) -> None:
        """Settle a task 'done'. ``pr_url`` is written in the SAME statement as the
        status flip so 'done' is never observable before its delivery artifact —
        a poller (goalclaw) can't see done-without-PR and re-dispatch. COALESCE
        keeps an already-recorded pr_url when None is passed (program/plain path)."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'done', result_json = ?, "
                "pr_url = COALESCE(?, pr_url), completed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (result_json, pr_url, _now_ms(), task_id),
            )
            if cur.rowcount == 1:
                # eval_outcomes projection (ADR 0006): materialized inside the
                # settle's own commit, only when a row actually moved — a no-op
                # re-settle writes nothing (exactly-once).
                self._insert_live_outcome(task_id, status="done", result_json=result_json)
            self._commit()

    def mark_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'failed', error = ?, completed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (error, _now_ms(), task_id),
            )
            moved = cur.rowcount == 1
            if moved:
                # eval_outcomes projection — same commit as the settle itself.
                self._insert_live_outcome(task_id, status="failed", error=error)
            self._commit()
        # Observability: a task settling FAILED is a problem devclaw hit — record
        # it (deduped) at this single choke point so every failure site
        # (timeout, review-crash, pause-bound, all-attempts-exhausted) is
        # covered by one call. Only when a row actually moved, so a no-op
        # re-settle can't inflate the count. `kind` is the error's first line
        # (the normalized full message is the fingerprint). Best-effort inside
        # record_problem — never raises back into the settle.
        if moved:
            first_line = (error or "").strip().splitlines()[0] if (error or "").strip() else ""
            self.record_problem(
                category="task_fail",
                kind=first_line[:120],
                message=error or "",
                recovered=False,
                task_id=task_id,
            )

    def mark_task_cancelled(self, task_id: str) -> bool:
        """Abort a task. Transitions pending/running -> cancelled (terminal).
        Returns True iff a row moved — False if the task was already terminal
        (done/failed/cancelled), so a settle that lands a hair later can't
        clobber it (mark_done/mark_failed also guard on pending/running). Used
        by the queue's cancel path; the live execution is torn down separately."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'cancelled', completed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (_now_ms(), task_id),
            )
            if cur.rowcount == 1:
                # eval_outcomes projection — same commit as the settle itself.
                self._insert_live_outcome(task_id, status="cancelled")
            self._commit()
            return cur.rowcount == 1

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(
        self,
        *,
        status: Optional[TaskStatus] = None,
        kind: Optional[TaskKind] = None,
        parent_goal_id: Optional[str] = None,
        workspace_dir: Optional[str] = None,
        parent_goal_id_is_null: bool = False,
        limit: int = 100,
    ) -> list[Task]:
        """Recent tasks, newest first. Extra filters:

        - ``parent_goal_id`` — only tasks owned by this goal (GoalDetail's
          Dispatched Tasks section).
        - ``workspace_dir`` — tasks whose workspace matches (ProjectDetail
          Recent Tasks strip).
        - ``parent_goal_id_is_null`` — restrict to standalone tasks (no goal
          owns them). Combine with workspace_dir to get the "loose tasks in
          this project" set — avoids double-counting tasks already visible
          inside a goal.
        """
        where: list[str] = []
        args: list[object] = []
        if status:
            where.append("status = ?")
            args.append(status)
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if parent_goal_id is not None:
            where.append("parent_goal_id = ?")
            args.append(parent_goal_id)
        if parent_goal_id_is_null:
            where.append("parent_goal_id IS NULL")
        if workspace_dir is not None:
            where.append("workspace_dir = ?")
            args.append(workspace_dir)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        args.append(limit)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM tasks {where_sql} ORDER BY created_at DESC LIMIT ?",
                tuple(args),
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def latest_task_for_goal(self, goal_id: str) -> Optional[Task]:
        """The most recent task dispatched by ``goal_id`` (any status), or None.
        The startup orphan-recovery sweep
        reads it to re-adopt a task whose goal-side in_flight ref was lost."""
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE parent_goal_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (goal_id,),
            ).fetchone()
        return _row_to_task(row) if row else None

    def maybe_vacuum(
        self,
        *,
        now_ms: Optional[int] = None,
        interval_ms: int = _VACUUM_INTERVAL_MS,
        min_freelist_pages: int = _VACUUM_MIN_FREELIST_PAGES,
    ) -> bool:
        """Weekly VACUUM that returns the disk the retention prunes free back to
        the OS (SQLite reuses freed pages but never shrinks the .db file on its
        own). The heartbeat's cheap-path maintenance hook, beside the prunes.

        Semantics:
          * checked at most once per ``interval_ms`` (weekly), gated by the
            ``vacuum_last_ms`` meta watermark — stamped on every CHECK (whether
            or not it vacuumed) so a healthy DB isn't re-inspected every tick;
          * only actually VACUUMs when the freelist is at least
            ``min_freelist_pages`` (real reclaim to be had) — a rewrite of a
            near-full DB for a few free pages isn't worth the write-lock cost;
          * never runs inside an open ``transaction()`` (VACUUM cannot run in a
            transaction) — defers to a later tick.

        Returns True iff it actually VACUUMed. Pure SQLite — zero LLM calls,
        safe on the zero-token idle path (the rare weekly rewrite aside)."""
        now = _now_ms() if now_ms is None else now_ms
        raw = self.get_meta(_VACUUM_META_KEY)
        try:
            last = int(raw) if raw else 0
        except ValueError:
            last = 0
        if last and (now - last) < interval_ms:
            return False
        with self._lock:
            if self._txn_depth > 0:
                # Mid atomic unit — VACUUM would raise. Try again next tick
                # (do NOT stamp the watermark: this cycle never happened).
                return False
            free = int(self._db.execute("PRAGMA freelist_count").fetchone()[0])
            if free < min_freelist_pages:
                # Inspected, nothing worth reclaiming — stamp so we wait a full
                # interval rather than re-checking every tick.
                self.set_meta(_VACUUM_META_KEY, str(now))
                return False
            self._db.commit()  # VACUUM requires no open transaction
            self._db.execute("VACUUM")
            self._db.commit()
            # Stamp only AFTER a successful rewrite: a VACUUM that raises (e.g.
            # not enough scratch disk — it needs ~file-size free) leaves the
            # watermark alone, so the next tick retries rather than deferring a
            # full week. Symmetric with the transaction-defer path above.
            self.set_meta(_VACUUM_META_KEY, str(now))
            return True

    def db_size_bytes(self) -> int:
        """On-disk size of the SQLite database, INCLUDING the WAL sidecar (an
        un-checkpointed WAL can itself be large). Best-effort: a missing file
        (e.g. an in-memory DB) counts as 0, never raises."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self._db_path + suffix)
            except OSError:
                pass
        return total

    def check_db_size_alert(
        self, *, threshold_bytes: Optional[int] = None, now_ms: Optional[int] = None
    ) -> Optional[str]:
        """One-shot DB-size alarm — the loud-not-silent guard against the .db
        quietly growing until the disk fills and the loop wedges.

        Returns a plain owner-facing message the FIRST tick the file crosses
        ``threshold_bytes`` (deduped via the ``db_size_alerted`` meta flag), and
        ``None`` on every tick after that until the size drops back under, which
        RE-ARMS the alarm (clears the flag) so a later re-crossing pings again.
        Disabled (``threshold_bytes`` resolves to 0) → always ``None``.

        Pure stat + a meta read/write — zero LLM, safe on the zero-token idle
        path. The ``now_ms`` arg is accepted for signature symmetry with the
        prunes/VACUUM and to keep the message deterministic in tests."""
        threshold = db_size_alert_bytes() if threshold_bytes is None else threshold_bytes
        if threshold <= 0:
            return None
        size = self.db_size_bytes()
        alerted = self.get_meta(_DB_SIZE_ALERTED_META_KEY) == "1"
        if size < threshold:
            if alerted:
                self.delete_meta(_DB_SIZE_ALERTED_META_KEY)  # re-arm for next crossing
            return None
        if alerted:
            return None  # already pinged for this episode
        self.set_meta(_DB_SIZE_ALERTED_META_KEY, "1")
        gb = size / (1024 * 1024 * 1024)
        thr_gb = threshold / (1024 * 1024 * 1024)
        return (
            f"⚠️ devclaw.db has grown to {gb:.2f} GB (alarm threshold "
            f"{thr_gb:.2f} GB). Retention/VACUUM may be falling behind, a "
            f"retention env var may be disabled, or something is writing faster "
            f"than it prunes. Check the VPS disk and DEVCLAW_*_RETENTION_DAYS."
        )

    # ---- scheduling / recovery ------------------------------------------

    def count_running(self) -> int:
        """Global count of tasks currently 'running' — the in-flight count.
        Single-writer + recover-on-startup means every 'running' row really is
        in flight in this process, so concurrency caps derive straight from it."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE status = 'running'"
            ).fetchone()
        return int(row["n"])

    def list_pending_standalone(self, *, limit: int = 100) -> list[Task]:
        """Pending tasks, oldest first so a backlog drains in submission order.
        (The name's "standalone" qualifier predates the 022 program-lane
        demolition — with the lane gone, every pending task is standalone; the
        demolition migration deleted the lane's zombie pending rows before
        dropping the program_id column this method used to filter on.)"""
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE status = 'pending' "
                "ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def has_active_work(self) -> bool:
        """Cheap-idle guard: True iff anything needs scheduling. One COUNT
        so an idle tick costs ~nothing (don't spend the engine on empty ticks)."""
        with self._lock:
            task = self._db.execute(
                "SELECT 1 FROM tasks WHERE status IN ('pending', 'running') LIMIT 1"
            ).fetchone()
        return task is not None

    def reset_running_to_pending(self) -> list[str]:
        """Crash recovery — call ONCE at startup, before any scheduling. A task
        left 'running' by a dead process has no live execution behind it, so
        reset it to 'pending' to be re-run. Returns the reaped task ids (for the
        audit log). Safe only when nothing is in flight in THIS process yet."""
        with self._lock:
            ids = [
                r["id"]
                for r in self._db.execute(
                    "SELECT id FROM tasks WHERE status = 'running'"
                ).fetchall()
            ]
            if ids:
                self._db.execute(
                    "UPDATE tasks SET status = 'pending', started_at = NULL "
                    "WHERE status = 'running'"
                )
                self._commit()
        return ids

    def requeue_task(self, task_id: str) -> bool:
        """Put a single in-flight task back to 'pending' (when paused for a
        quota limit rather than failed). Increments ``pause_count`` in the same
        statement so the pause→requeue loop is countable (and thus boundable) —
        read it back via :meth:`get_task`. Returns True if a running row was
        reset."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'pending', started_at = NULL, "
                "pause_count = pause_count + 1 "
                "WHERE id = ? AND status = 'running'",
                (task_id,),
            )
            self._commit()
            return cur.rowcount > 0

    def set_task_pre_run_sha(self, task_id: str, sha: str) -> None:
        """Persist the gate-baseline sha captured at the task's first run.
        Written once by the queue (single-writer) before the attempt loop; a
        pause→requeue re-run reads it back instead of re-capturing HEAD (which
        by then is the wip snapshot commit — the work itself, not the base)."""
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET pre_run_sha = ? WHERE id = ?", (sha, task_id)
            )
            self._commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
