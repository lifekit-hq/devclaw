"""The ``machine_issues`` ledger — the issue doorway's dedup record (spec 014).

One row per ``(repo, fingerprint)`` a machine finding has ever been filed for.
The ledger — not GitHub search — is the dedup source of truth (research.md D3),
and it rides the SAME single-writer :class:`~devclaw.state_store.StateStore`
instance as every other table, so lock/commit semantics are identical.

Unlike the best-effort ``problems`` recorders, these methods raise on failure:
a ledger write that silently vanished would let the next filing of the same
fingerprint open a duplicate issue — exactly what SC-002 forbids — so a broken
ledger must surface as a failed filing (fail loud), never as quiet dedup loss.
"""

from __future__ import annotations

import sqlite3
import threading
from typing import TYPE_CHECKING, Optional


class MachineIssuesMixin:
    if TYPE_CHECKING:
        # Owned by the composing class; declared under TYPE_CHECKING so the
        # seam is checked, never run (same shape as ProblemsMixin).
        _db: sqlite3.Connection
        _lock: threading.RLock

        def _commit(self) -> None: ...

    _MACHINE_ISSUE_COLS = (
        "repo, fingerprint, issue_number, issue_state, schema_version, "
        "source, occurrence_count, first_seen_ms, last_seen_ms"
    )

    def machine_issue_get(self, repo: str, fingerprint: str) -> Optional[dict]:
        """The ledger row for ``(repo, fingerprint)``, or ``None`` if this
        fingerprint has never been filed on this repo."""
        with self._lock:
            row = self._db.execute(
                f"SELECT {self._MACHINE_ISSUE_COLS} FROM machine_issues "
                "WHERE repo = ? AND fingerprint = ?",
                (repo, fingerprint),
            ).fetchone()
        if row is None:
            return None
        return dict(zip(self._MACHINE_ISSUE_COLS.replace(" ", "").split(","), row))

    def machine_issue_record(
        self,
        repo: str,
        fingerprint: str,
        *,
        issue_number: int,
        issue_state: str,
        source: str,
        schema_version: int,
        now_ms: int,
    ) -> None:
        """Insert the first filing, or bump the occurrence on a repeat: a
        conflict on ``(repo, fingerprint)`` increments ``occurrence_count``,
        refreshes ``last_seen_ms``, and adopts the new state/number (a reopen
        flips ``closed`` back to ``open`` through here)."""
        with self._lock:
            self._db.execute(
                """INSERT INTO machine_issues
                     (repo, fingerprint, issue_number, issue_state,
                      schema_version, source, occurrence_count,
                      first_seen_ms, last_seen_ms)
                   VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                   ON CONFLICT(repo, fingerprint) DO UPDATE SET
                     occurrence_count = occurrence_count + 1,
                     issue_number = ?,
                     issue_state = ?,
                     schema_version = ?,
                     last_seen_ms = ?""",
                (
                    repo, fingerprint, issue_number, issue_state,
                    schema_version, source, now_ms, now_ms,
                    # ON CONFLICT bind params:
                    issue_number, issue_state, schema_version, now_ms,
                ),
            )
            self._commit()

    def machine_issue_set_state(
        self, repo: str, fingerprint: str, issue_state: str
    ) -> None:
        """Flip just the state (e.g. an age-out close) without touching the
        occurrence record."""
        with self._lock:
            self._db.execute(
                "UPDATE machine_issues SET issue_state = ? "
                "WHERE repo = ? AND fingerprint = ?",
                (issue_state, repo, fingerprint),
            )
            self._commit()
