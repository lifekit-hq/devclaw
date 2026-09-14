"""SQLite state store — the single writer to task rows, the goal rows, and
the control-plane flags.

``sqlite3`` is sync; a re-entrant lock serializes access because the server
touches the store from the event loop and from background tasks. WAL mode
gives concurrent reads with a single writer. :meth:`transaction` groups
several writes into one atomic unit.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from .control import ControlPlaneMixin
from .events import EventsMixin
from .goals import GoalsMixin
from .rows import SQLITE_BUSY_TIMEOUT_MS, Task, TaskKind, TaskStatus, _now_ms, _row_to_task
from .schema import bootstrap as _schema_bootstrap


class StateStore(ControlPlaneMixin, EventsMixin, GoalsMixin):
    def __init__(self, db_path: str) -> None:
        Path(db_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(Path(db_path).expanduser())
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
        self._lock = threading.RLock()
        self._txn_depth = 0
        self._txn_failed = False
        _schema_bootstrap(self._db, self._lock, self._commit)

    @property
    def db_path(self) -> str:
        return self._db_path

    # ---- transactions -------------------------------------------------

    @contextmanager
    def transaction(self) -> "Iterator[StateStore]":
        """One atomic unit across several store methods; nested calls join the
        outermost one; any exception rolls the whole unit back."""
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
        if self._txn_depth == 0:
            self._db.commit()

    # ---- tasks --------------------------------------------------------

    def create_task(self, *, id: str, kind: TaskKind, workspace_dir: str, goal: str,
                    verify_cmd: Optional[str] = None, deliver: bool = False,
                    parent_goal_id: Optional[str] = None,
                    target_branch: Optional[str] = None,
                    project_id: Optional[str] = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO tasks (id, kind, status, workspace_dir, goal, created_at, verify_cmd, "
                "deliver, parent_goal_id, target_branch, project_id) "
                "VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?)",
                (id, kind, workspace_dir, goal, _now_ms(), verify_cmd, 1 if deliver else 0,
                 parent_goal_id, target_branch, project_id),
            )
            self._commit()

    def claim_pending(self, task_id: str) -> bool:
        """pending → running, atomically; True iff this call won."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'running', started_at = ? WHERE id = ? AND status = 'pending'",
                (_now_ms(), task_id),
            )
            self._commit()
            return cur.rowcount == 1

    def mark_done(self, task_id: str, result_json: str, *, pr_url: Optional[str] = None,
                  exit: Optional[str] = None, exit_detail: Optional[str] = None) -> None:
        """Settle ``done`` with the delivery artifact and the exit in ONE write —
        done is never observable before its PR or its exit."""
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status = 'done', result_json = ?, pr_url = COALESCE(?, pr_url), "
                "exit = COALESCE(?, exit), exit_detail = COALESCE(?, exit_detail), completed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (result_json, pr_url, exit, exit_detail, _now_ms(), task_id),
            )
            self._commit()

    def mark_failed(self, task_id: str, error: str, *, exit: Optional[str] = None,
                    exit_detail: Optional[str] = None, result_json: Optional[str] = None) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE tasks SET status = 'failed', error = ?, result_json = COALESCE(?, result_json), "
                "exit = COALESCE(?, exit), exit_detail = COALESCE(?, exit_detail), completed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (error, result_json, exit, exit_detail, _now_ms(), task_id),
            )
            self._commit()

    def mark_task_cancelled(self, task_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'cancelled', completed_at = ? "
                "WHERE id = ? AND status IN ('pending', 'running')",
                (_now_ms(), task_id),
            )
            self._commit()
            return cur.rowcount == 1

    def mark_task_notified(self, task_id: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET notified = 1 WHERE id = ?", (task_id,))
            self._commit()

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            row = self._db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return _row_to_task(row) if row else None

    def list_tasks(self, *, status: Optional[TaskStatus] = None,
                   parent_goal_id: Optional[str] = None, exit: Optional[str] = None,
                   limit: int = 100) -> list[Task]:
        where: list[str] = []
        args: list[object] = []
        if status:
            where.append("status = ?")
            args.append(status)
        if parent_goal_id is not None:
            where.append("parent_goal_id = ?")
            args.append(parent_goal_id)
        if exit is not None:
            where.append("exit = ?")
            args.append(exit)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        args.append(limit)
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM tasks {where_sql} ORDER BY created_at DESC LIMIT ?", tuple(args)
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    #: the four token kinds the runner's usage block carries (spec 039 US3)
    USAGE_KINDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")

    def usage_totals(self, *, parent_goal_id: Optional[str] = None,
                     project_id: Optional[str] = None) -> dict:
        """Tokens summed over the sessions that reported a usage block, plus how
        many sessions there were and how many reported — ONE pass with json1,
        never a stored aggregate (spec 047 US3, constitution IV). A row whose
        result is not JSON counts as unreported, never as zero."""
        where: list[str] = []
        args: list[object] = []
        if parent_goal_id is not None:
            where.append("parent_goal_id = ?")
            args.append(parent_goal_id)
        if project_id is not None:
            where.append("project_id = ?")
            args.append(project_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        valid = "(result_json IS NOT NULL AND json_valid(result_json) AND json_type(result_json, '$.usage') = 'object')"
        sums = ", ".join(
            f"COALESCE(SUM(CASE WHEN {valid} THEN json_extract(result_json, '$.usage.{k}') END), 0) AS {k}"
            for k in self.USAGE_KINDS
        )
        with self._lock:
            row = self._db.execute(
                f"SELECT {sums}, COUNT(*) AS sessions_total, "
                f"COALESCE(SUM(CASE WHEN {valid} THEN 1 ELSE 0 END), 0) AS sessions_reported "
                f"FROM tasks {where_sql}", tuple(args),
            ).fetchone()
        out = {k: int(row[k] or 0) for k in self.USAGE_KINDS}
        out["sessions_total"] = int(row["sessions_total"])
        out["sessions_reported"] = int(row["sessions_reported"])
        return out

    def latest_task_for_goal(self, goal_id: str) -> Optional[Task]:
        with self._lock:
            row = self._db.execute(
                "SELECT * FROM tasks WHERE parent_goal_id = ? ORDER BY created_at DESC LIMIT 1",
                (goal_id,),
            ).fetchone()
        return _row_to_task(row) if row else None

    def goal_has_live_task(self, goal_id: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM tasks WHERE parent_goal_id = ? AND status IN ('pending', 'running') LIMIT 1",
                (goal_id,),
            ).fetchone()
        return row is not None

    def count_goal_tasks_since(self, goal_id: str, since_ms: int) -> int:
        """Sessions a goal has started since ``since_ms`` — the daily money cap
        is read from the rows, never from a counter."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE parent_goal_id = ? AND created_at >= ?",
                (goal_id, since_ms),
            ).fetchone()
        return int(row["n"])

    # ---- scheduling / recovery ----------------------------------------

    def count_running(self) -> int:
        with self._lock:
            row = self._db.execute("SELECT COUNT(*) AS n FROM tasks WHERE status = 'running'").fetchone()
        return int(row["n"])

    def list_pending(self, *, limit: int = 100) -> list[Task]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?", (limit,)
            ).fetchall()
        return [_row_to_task(r) for r in rows]

    def has_active_work(self) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM tasks WHERE status IN ('pending', 'running') LIMIT 1"
            ).fetchone()
        return row is not None

    def reset_running_to_pending(self) -> list[str]:
        """Crash recovery — once at startup, before any scheduling."""
        with self._lock:
            ids = [r["id"] for r in self._db.execute(
                "SELECT id FROM tasks WHERE status = 'running'").fetchall()]
            if ids:
                self._db.execute("UPDATE tasks SET status = 'pending', started_at = NULL WHERE status = 'running'")
                self._commit()
        return ids

    def requeue_task(self, task_id: str) -> bool:
        """running → pending after a usage-limit pause; ``pause_count`` counts it."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE tasks SET status = 'pending', started_at = NULL, pause_count = pause_count + 1 "
                "WHERE id = ? AND status = 'running'",
                (task_id,),
            )
            self._commit()
            return cur.rowcount > 0

    def set_task_pre_run_sha(self, task_id: str, sha: str) -> None:
        with self._lock:
            self._db.execute("UPDATE tasks SET pre_run_sha = ? WHERE id = ?", (sha, task_id))
            self._commit()

    def db_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += os.path.getsize(self._db_path + suffix)
            except OSError:
                pass
        return total

    def close(self) -> None:
        with self._lock:
            self._db.close()
