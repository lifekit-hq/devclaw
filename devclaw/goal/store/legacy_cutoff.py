"""One migration, one cutoff — the second half (#616).

#617 stopped the generated views being read back. This is the other tax the
same root cause left behind: **every migration since T0 removed the mechanism
but kept the vocabulary, the fallback, and the compat branch**, so the code
reconciled three overlapping histories on every read.

There is ONE database, on ONE VPS, with ONE user. Every branch this module
retires was permanent production code guarding rows that a single migration
makes current. Nothing here protected a customer, a fleet, or a rollback path
anyone would take.

**The cutoff is 2026-08-22.** Rows older than it are MIGRATED by this module,
once. After it runs there is no legacy shape left to read, so the readers are
deleted rather than kept "just in case": a fallback with no reachable input is
not safety, it is a second history the next reader has to reason about.

What the sweep does, in order:

1. ``goal_status.lifecycle`` — NULL (pre-lifecycle rows) and the pre-shrink
   ``investigating``/``firming`` strings become ``executing``. Every reader
   already coerced them to that; the difference is that now the column says
   so, ``Lifecycle`` is non-optional, and ``heal_legacy_lifecycle`` — which
   existed TWICE, plus a branch on the hot tick path — is gone.
2. ``goal_deliveries.ref_id`` — NULL rows (sections ingested from a
   ``deliveries.md`` that predates the idempotency key) get a deterministic
   ``pre-cutoff:<rowid>`` id, and the table is rebuilt with the column NOT
   NULL. This is the tax spec 012 was paying: reconstructing "what did earlier
   increments deliver" was hard precisely because ``ref_id`` could be NULL.
3. ``goal_docs`` — dropped entirely. Its kinds (``checklist``,
   ``firmed_draft``, ``repo_analysis``, ``block_options``) all died with the
   host-cognition chain in the spec 008 shrink. Nothing has written one since,
   nothing reads one, and no test covers the surface: it survived only as
   "pre-shrink rows stay readable".
4. ``goal_status.inbox_ingest_cursor`` — the ingest boundary #617 deleted the
   last reader for.

Crash-safety follows the shape :mod:`.view_migration` established: every step
is idempotent on its own, and the marker is stamped only after the whole sweep
completes, so a crash part-way through resumes rather than half-applying.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..state import GoalState
    from ...state_store import StateStore

#: Stamped in ``meta`` once the sweep completes; its presence makes it one-shot.
CUTOFF_META_KEY = "goal_legacy_cutoff_done_at_ms"

#: The date rows are supported back to. Anything shaped older than this is
#: migrated by the sweep below, not read at runtime.
CUTOFF_DATE = "2026-08-22"

#: ``goal_docs`` kinds, all retired by the spec 008 shrink. Named here so the
#: DROP is legible in a diff rather than a bare table name.
RETIRED_DOC_KINDS = ("checklist", "firmed_draft", "repo_analysis", "block_options")


def apply_legacy_cutoff(
    state: "StateStore", goal_state: "GoalState", now_ms: int,
) -> "dict[str, int]":
    """Run the cutoff ONCE per database. Returns per-step row counts (empty
    when the marker is already set, which is every call after the first).

    Must run AFTER :func:`~devclaw.goal.store.view_migration.migrate_views_once`:
    that sweep is what puts the pre-cutoff markdown into rows, and step 2 here
    is what gives those rows their ``ref_id``.
    """
    if state.get_meta(CUTOFF_META_KEY):
        return {}
    counts = {
        "lifecycle_healed": _heal_lifecycle(state),
        "delivery_ref_ids_backfilled": _backfill_delivery_ref_ids(state),
        "goal_docs_dropped": _drop_goal_docs(state),
    }
    _rebuild_deliveries_with_not_null_ref_id(state)
    _drop_inbox_ingest_cursor(state)
    state.set_meta(CUTOFF_META_KEY, str(int(now_ms)))
    return counts


def _heal_lifecycle(state: "StateStore") -> int:
    """Every non-``executing`` lifecycle becomes ``executing`` — NULL from
    before the column existed, and the pre-shrink ``investigating``/``firming``
    strings. Deliberately a whitelist (``!= 'executing'``) rather than a list of
    the values we happen to remember: an unknown string is exactly the case a
    denylist would miss and leave for a reader that no longer exists."""
    with state._lock:
        cur = state._db.execute(
            "UPDATE goal_status SET lifecycle = 'executing' "
            "WHERE lifecycle IS NULL OR lifecycle != 'executing'"
        )
        state._commit()
        return cur.rowcount


def _backfill_delivery_ref_ids(state: "StateStore") -> int:
    """Give every NULL ``ref_id`` a deterministic, unique one. Derived from the
    rowid so it is stable across a re-run and cannot collide with a real ref."""
    with state._lock:
        cur = state._db.execute(
            "UPDATE goal_deliveries SET ref_id = 'pre-cutoff:' || id WHERE ref_id IS NULL"
        )
        state._commit()
        return cur.rowcount


def _rebuild_deliveries_with_not_null_ref_id(state: "StateStore") -> None:
    """Rebuild ``goal_deliveries`` with ``ref_id TEXT NOT NULL``.

    A pointed instance of the class this issue is about: PR2 created this table
    ``NOT NULL``, PR6 rebuilt it NULLABLE for legacy-ingested sections, and the
    cutoff makes it NOT NULL again. The right number of shapes is one. Guarded
    on ``PRAGMA table_info`` so it is a no-op on a database that already has
    the corrected schema."""
    with state._lock:
        info = state._db.execute("PRAGMA table_info(goal_deliveries)").fetchall()
        ref_id_col = next((r for r in info if r["name"] == "ref_id"), None)
        if ref_id_col is None or ref_id_col["notnull"]:
            return
        state._db.executescript(
            """
            CREATE TABLE goal_deliveries__cutoff (
              id          INTEGER PRIMARY KEY AUTOINCREMENT,
              goal_id     TEXT NOT NULL,
              ref_id      TEXT NOT NULL,
              instruction TEXT,
              body        TEXT,
              created_at  INTEGER NOT NULL,
              UNIQUE(goal_id, ref_id)
            );
            INSERT INTO goal_deliveries__cutoff
              (id, goal_id, ref_id, instruction, body, created_at)
              SELECT id, goal_id, ref_id, instruction, body, created_at
              FROM goal_deliveries;
            DROP TABLE goal_deliveries;
            ALTER TABLE goal_deliveries__cutoff RENAME TO goal_deliveries;
            CREATE INDEX IF NOT EXISTS idx_goal_deliveries_goal
              ON goal_deliveries(goal_id, id);
            """
        )
        state._commit()


def _drop_goal_docs(state: "StateStore") -> int:
    """Drop the ``goal_docs`` table. Every kind it held died with the spec 008
    shrink; nothing writes one, nothing reads one, no test covers it. Returns
    the row count discarded, so the sweep can say out loud what it deleted."""
    with state._lock:
        exists = state._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'goal_docs'"
        ).fetchone()
        if not exists:
            return 0
        n = state._db.execute("SELECT COUNT(*) AS n FROM goal_docs").fetchone()["n"]
        state._db.executescript("DROP TABLE goal_docs;")
        state._commit()
        return int(n)


def _drop_inbox_ingest_cursor(state: "StateStore") -> None:
    """Drop ``goal_status.inbox_ingest_cursor`` — #617 deleted its last reader
    (the inbox ingest) and nothing has written it since. Guarded on ``PRAGMA
    table_info``; ``ALTER TABLE ... DROP COLUMN`` needs SQLite 3.35+, and on an
    older engine the column is simply left in place, unread and unwritten,
    rather than failing the sweep."""
    import sqlite3

    with state._lock:
        info = state._db.execute("PRAGMA table_info(goal_status)").fetchall()
        if not any(r["name"] == "inbox_ingest_cursor" for r in info):
            return
        try:
            state._db.execute("ALTER TABLE goal_status DROP COLUMN inbox_ingest_cursor")
        except sqlite3.OperationalError:
            return
        state._commit()
