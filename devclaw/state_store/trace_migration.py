"""One migration, one cutoff — cognition trace rows carry ``response_text`` only.

**Cutoff: 2026-07-10** — the T0.5 commit (`451953e`, #193) that put the
`claude --print --output-format json` envelope behind every cognition call.
From that commit on, :class:`~devclaw.loom.trace.CognitionEvent` stores the
FULL model response as ``response_text``. Rows written *before* it carried
only ``response_preview``, a 240-char truncation that regularly cut a verdict
in half — which is why telemetry grew a second read path
(``response_text or response_preview``) and kept it as permanent production
code.

That second path guarded rows the database no longer holds: ``traces`` is
pruned daily to :data:`~devclaw.state_store.core.TRACE_RETENTION_DAYS_DEFAULT`
(30) days, so a pre-cutoff row survives only in a database whose retention was
switched off. This module is what makes the deletion safe anyway — it
backfills ``response_text`` from the preview on any such row and drops the
now-unread ``response_preview`` key, so exactly one field answers "what did
the model say". Rows older than the cutoff with neither field are not
supported: they read as an empty response, the same as any errored call.

Runs ONCE per database, at :class:`~devclaw.state_store.StateStore`
construction, before any read path can observe the rows — then stamps
:data:`MIGRATION_META_KEY` and never runs again. The sweep is batched and
walks a monotonic ``id`` cursor, so a crash part-way leaves the marker unset
and the next start resumes over what is left; a torn (non-JSON) payload is
skipped rather than raising, because the migration must never be the reason an
instance fails to start.
"""

from __future__ import annotations

import json
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .core import StateStore

#: Stamped in the ``meta`` table once the sweep completes. Its presence — not
#: any per-row condition — is what makes the migration one-shot.
MIGRATION_META_KEY = "trace_response_text_migration_done_at_ms"

#: Rows read (and at most rewritten) per batch. Bounded so the lock is never
#: held across a whole 200k-row trace table.
_BATCH = 500


def migrate_cognition_response_text_once(store: "StateStore", *, now_ms: int) -> int:
    """Fold every pre-cutoff ``response_preview`` into ``response_text``, then
    stamp the database migrated. Returns the number of rows rewritten (0 when
    the marker is already set, which is every call after the first)."""
    if store.get_meta(MIGRATION_META_KEY):
        return 0
    rewritten = 0
    cursor = 0
    while True:
        with store._lock:
            try:
                rows = store._db.execute(
                    "SELECT id, payload_json FROM traces "
                    "WHERE kind = 'cognition' AND id > ? "
                    "AND payload_json LIKE '%\"response_preview\"%' "
                    "ORDER BY id LIMIT ?",
                    (cursor, _BATCH),
                ).fetchall()
            except sqlite3.OperationalError:
                break  # no traces table yet (fresh DB) — nothing to migrate
        if not rows:
            break
        for row in rows:
            # Advance the cursor for EVERY row, parseable or not: a torn
            # payload that stays matching the LIKE would otherwise re-select
            # forever.
            cursor = int(row["id"])
            updated = _fold_preview(row["payload_json"])
            if updated is None:
                continue
            with store._lock:
                store._db.execute(
                    "UPDATE traces SET payload_json = ? WHERE id = ?",
                    (updated, cursor),
                )
                store._commit()
            rewritten += 1
    store.set_meta(MIGRATION_META_KEY, str(int(now_ms)))
    return rewritten


def _fold_preview(payload_json: str) -> "str | None":
    """The row's payload with ``response_preview`` folded into
    ``response_text`` and removed, or None when there is nothing to do (no
    preview key, or the payload is not a JSON object)."""
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or "response_preview" not in payload:
        return None
    preview = payload.pop("response_preview")
    if not payload.get("response_text") and isinstance(preview, str):
        payload["response_text"] = preview
    return json.dumps(payload, default=str)
