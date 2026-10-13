"""Per-user console appearance over the ``user_preferences`` table: one row per
signed-in identity (the oauth2-proxy email), written only by the console's own
``PATCH /api/me/preferences``. It is presentation, never control state - no
gate, tick or session reads it. A mixin on the StateStore instance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .rows import _now_ms

if TYPE_CHECKING:
    import sqlite3
    import threading


class PreferencesMixin:
    if TYPE_CHECKING:
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...

    def get_appearance(self, subject: str) -> Optional[dict]:
        """``{theme, seed, intensity}`` (seed/intensity may be None), or None
        when this identity never saved one."""
        with self._lock:
            row = self._db.execute(
                "SELECT theme, seed, intensity FROM user_preferences WHERE subject = ?",
                (subject,),
            ).fetchone()
        if row is None:
            return None
        return {"theme": row["theme"], "seed": row["seed"], "intensity": row["intensity"]}

    def set_appearance(self, subject: str, theme: str, seed: Optional[str],
                       intensity: Optional[float]) -> None:
        """Replace the identity's appearance wholesale (an omitted seed clears it)."""
        with self._lock:
            self._db.execute(
                "INSERT INTO user_preferences (subject, theme, seed, intensity, updated_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(subject) DO UPDATE SET "
                "theme = excluded.theme, seed = excluded.seed, "
                "intensity = excluded.intensity, updated_at = excluded.updated_at",
                (subject, theme, seed, intensity, _now_ms()),
            )
            self._commit()
