"""The append-only ``events`` log (one row per agent action inside a task) and
its retention prune. A mixin on the same StateStore instance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .. import config as _config
from .rows import TaskEvent, _now_ms, _row_to_event

if TYPE_CHECKING:
    import sqlite3
    import threading

EVENTS_RETENTION_DAYS_DEFAULT = 30
EVENTS_PRUNE_BATCH = 5000
_PRUNE_INTERVAL_MS = 24 * 3600 * 1000
_EVENTS_PRUNE_META_KEY = "events_prune_last_ms"


def _parse_retention_days(raw: Optional[str], default: int) -> int:
    """unset → default; 0/negative/unparseable → 0 (disabled, never a crash)."""
    if raw is None or not raw.strip():
        return default
    try:
        days = int(raw.strip())
    except ValueError:
        return 0
    return days if days > 0 else 0


def events_retention_days() -> int:
    return _parse_retention_days(_config.events_retention_days_raw(), EVENTS_RETENTION_DAYS_DEFAULT)


class EventsMixin:
    if TYPE_CHECKING:
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...
        def get_meta(self, key: str) -> Optional[str]: ...
        def set_meta(self, key: str, value: str) -> None: ...

    def append_event(self, *, task_id: str, type: str, source: str,
                     payload_json: str, ts: Optional[int] = None) -> int:
        """Append one row; returns its monotonic id. ``ts`` is normalized to
        milliseconds (a seconds-scale value is scaled up)."""
        if ts is not None and 0 < ts < 10**12:
            ts = int(ts * 1000)
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO events (task_id, type, source, payload_json, ts) VALUES (?, ?, ?, ?, ?)",
                (task_id, type, source, payload_json, ts if ts is not None else _now_ms()),
            )
            self._commit()
            assert cur.lastrowid is not None
            return int(cur.lastrowid)

    def list_events(self, *, task_id: str, since_id: Optional[int] = None,
                    limit: int = 500) -> list[TaskEvent]:
        where = ["task_id = ?"]
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

    def maybe_prune_events(self, *, now_ms: Optional[int] = None,
                           retention_days: Optional[int] = None,
                           batch_limit: int = EVENTS_PRUNE_BATCH) -> int:
        """Daily, batched retention prune — pure SQLite, safe on the idle path."""
        days = events_retention_days() if retention_days is None else retention_days
        if days <= 0:
            return 0
        now = _now_ms() if now_ms is None else now_ms
        raw = self.get_meta(_EVENTS_PRUNE_META_KEY)
        try:
            last = int(raw) if raw else 0
        except ValueError:
            last = 0
        if last and (now - last) < _PRUNE_INTERVAL_MS:
            return 0
        with self._lock:
            cur = self._db.execute(
                "DELETE FROM events WHERE id IN (SELECT id FROM events WHERE ts < ? ORDER BY id ASC LIMIT ?)",
                (now - days * 24 * 3600 * 1000, batch_limit),
            )
            self._commit()
            deleted = int(cur.rowcount)
        if deleted < batch_limit:
            self.set_meta(_EVENTS_PRUNE_META_KEY, str(now))
        return deleted
