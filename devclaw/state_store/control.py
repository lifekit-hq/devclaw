"""Control-plane flags over the ``meta`` table: the account-wide quota pause,
the operator hold, the run window, the concurrency dial, the self-deploy
intent, the per-workspace circuit breaker. A mixin on the StateStore instance.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Optional

from .. import config as _config

if TYPE_CHECKING:
    import sqlite3
    import threading


class ControlPlaneMixin:
    if TYPE_CHECKING:
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...

    # ---- meta ---------------------------------------------------------

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            self._commit()

    def get_meta(self, key: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def delete_meta(self, key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM meta WHERE key = ?", (key,))
            self._commit()

    # ---- the quota / auth / outage pause (money) ----------------------

    def set_global_pause(self, until_ms: int, reason: str) -> None:
        """Pause ALL dispatch until ``until_ms`` — the OAuth quota is
        account-wide. Persisted so a restart honours it."""
        self.set_meta("pause_until_ms", str(int(until_ms)))
        self.set_meta("pause_reason", reason or "")

    def global_pause(self) -> tuple[int, str]:
        """``(until_ms, reason)``; ``until_ms`` is 0 when no pause is set."""
        raw = self.get_meta("pause_until_ms")
        try:
            until = int(raw) if raw else 0
        except ValueError:
            until = 0
        return until, (self.get_meta("pause_reason") or "")

    def clear_global_pause(self) -> None:
        self.delete_meta("pause_until_ms")
        self.delete_meta("pause_reason")

    def set_pause_notified(self, on: bool, kind: str = "") -> None:
        """The owner was told about the current pause (one ping per pause)."""
        if on:
            self.set_meta("pause_notified", kind or "1")
        else:
            self.delete_meta("pause_notified")

    def pause_notified(self) -> bool:
        return self.get_meta("pause_notified") is not None

    # ---- operator hold + run window -----------------------------------

    def set_operator_hold(self, on: bool, reason: str = "") -> None:
        if on:
            self.set_meta("operator_hold", json.dumps({"on": True, "reason": reason or ""}))
        else:
            self.delete_meta("operator_hold")

    def operator_hold(self) -> tuple[bool, str]:
        raw = self.get_meta("operator_hold")
        if not raw:
            return False, ""
        try:
            data = json.loads(raw)
            return bool(data.get("on")), str(data.get("reason") or "")
        except (ValueError, TypeError):
            return False, ""

    def set_run_schedule(self, enabled: bool, start: str, end: str, tz: str) -> None:
        self.set_meta("run_schedule", json.dumps(
            {"enabled": bool(enabled), "start": start, "end": end, "tz": tz}
        ))

    def get_run_schedule(self) -> dict:
        raw = self.get_meta("run_schedule")
        if not raw:
            return dict(_config.DEFAULT_RUN_SCHEDULE)
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return dict(_config.DEFAULT_RUN_SCHEDULE)
        return {
            "enabled": bool(data.get("enabled")),
            "start": str(data.get("start") or _config.DEFAULT_RUN_SCHEDULE["start"]),
            "end": str(data.get("end") or _config.DEFAULT_RUN_SCHEDULE["end"]),
            "tz": str(data.get("tz") or _config.DEFAULT_RUN_SCHEDULE["tz"]),
        }

    # ---- concurrency dial ---------------------------------------------

    def set_max_concurrent(self, n: "int | None") -> None:
        if n is None:
            self.delete_meta("max_concurrent")
            return
        if isinstance(n, bool) or not isinstance(n, int) or n < 1:
            raise ValueError("max_concurrent must be a whole number >= 1 (None clears the override)")
        self.set_meta("max_concurrent", str(int(n)))

    def max_concurrent(self) -> "int | None":
        raw = self.get_meta("max_concurrent")
        if raw is None:
            return None
        try:
            n = int(str(raw).strip())
        except (TypeError, ValueError):
            return None
        return n if n >= 1 else None

    # ---- self-deploy intent -------------------------------------------

    def set_deploy_pending(self, sha: str, goal_id: str, since_ms: int) -> None:
        self.set_meta("deploy_pending", json.dumps(
            {"sha": sha or "", "goal_id": goal_id, "since_ms": int(since_ms)}
        ))

    def deploy_pending(self) -> "tuple[str, str, int] | None":
        raw = self.get_meta("deploy_pending")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return (str(data.get("sha") or ""), str(data.get("goal_id") or ""),
                    int(data.get("since_ms") or 0))
        except (ValueError, TypeError):
            return None

    def clear_deploy_pending(self) -> None:
        self.delete_meta("deploy_pending")

    def record_deploy_last(self, *, sha: str, goal_id: str, outcome: str,
                           at_ms: int, detail: str = "") -> None:
        self.set_meta("deploy_last", json.dumps({
            "sha": sha or "", "goal_id": goal_id, "outcome": outcome,
            "at_ms": int(at_ms), "detail": (detail or "")[:400],
        }))

    def deploy_last(self) -> "dict | None":
        raw = self.get_meta("deploy_last")
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else None
        except (ValueError, TypeError):
            return None

    # ---- per-workspace circuit breaker --------------------------------

    def count_recent_task_failures(self, workspace_dir: str, since_ms: int) -> int:
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE workspace_dir = ? AND status = 'failed' "
                "AND completed_at IS NOT NULL AND completed_at >= ?",
                (workspace_dir, since_ms),
            ).fetchone()
        return int(row["n"])

    def set_workspace_break(self, workspace_dir: str, until_ms: int, reason: str) -> None:
        self.set_meta(f"workspace_break:{workspace_dir}",
                      json.dumps({"until_ms": int(until_ms), "reason": reason or ""}))

    def get_workspace_break(self, workspace_dir: str) -> tuple[int, str]:
        raw = self.get_meta(f"workspace_break:{workspace_dir}")
        if not raw:
            return 0, ""
        try:
            data = json.loads(raw)
            return int(data.get("until_ms") or 0), str(data.get("reason") or "")
        except (ValueError, TypeError):
            return 0, ""

    def clear_workspace_break(self, workspace_dir: str) -> None:
        self.delete_meta(f"workspace_break:{workspace_dir}")
