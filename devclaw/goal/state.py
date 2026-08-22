"""Tranche 1 substrate — the SQLite home for goal state.

Goal state used to be spread across per-goal files under ``DEVCLAW_GOALS_DIR``
(``goal.yaml`` / ``STATUS.md`` / ``log.md`` / ``inbox.md`` / ``deliveries.md`` …)
and linked to the task queue only by a string goal id. The approved Tranche 1
plan consolidates that state into the SAME ``devclaw.db`` SQLite database that
:class:`devclaw.state_store.StateStore` already owns (WAL, one shared
connection guarded by a single ``threading.RLock``), so a task row and its
owning goal's state can be written in one atomic :meth:`StateStore.transaction`.

**PR3 brought ``goal_status`` + ``goal_phase_history`` LIVE.** ``GoalState``
now owns the status read/write surface (:meth:`read_status` /
:meth:`write_status` / the phase-history methods); ``GoalStore.load_status`` /
``save_status`` are re-backed onto it, with ``STATUS.md`` demoted to a
generated full-fidelity view (the rollback path).

**PR5 brought ``goal_steering`` LIVE.** ``consumed_at IS NULL`` is now the
source of truth for "unread" — ``GoalStore.append_steering`` writes rows,
``GoalStore.transition(consume_steering=...)`` consumes them by exact id.
``inbox.md`` is a generated mirror only: since #617 nothing reads it back, and
steering enters exclusively through ``steer_goal``.

**PR6 brought ``goal_log`` / ``goal_deliveries`` LIVE.**
``goal_log`` and ``goal_deliveries`` are row-backed with ``log.md`` /
``deliveries.md`` as generated mirrors; ``goal_deliveries`` inserts are
idempotent, keyed on ``ref_id`` (``UNIQUE(goal_id, ref_id)`` + INSERT OR
IGNORE), closing a PR4-review nuance where a settle landing in a
``TransitionConflict`` retry window could append the same delivery twice. The
views these tables were seeded from are read exactly once, by
:func:`devclaw.goal.store.view_migration.migrate_views_once` (#617); after
that they are write-only projections.

**The #616 cutoff (2026-08-22) left exactly one shape per row.** ``lifecycle``
is non-optional, ``ref_id`` is NOT NULL, and ``goal_docs`` — whose every kind
died with the host-cognition chain in the spec 008 shrink — is dropped. See
:mod:`devclaw.goal.store.legacy_cutoff`; the compat branches that used to read
those shapes are deleted, not disabled.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import TYPE_CHECKING

from .models import GoalStatus, InFlight

if TYPE_CHECKING:
    from ..state_store import StateStore


def _now_ms() -> int:
    return int(time.time() * 1000)


class GoalState:
    """Owns the goal-state tables inside a shared :class:`StateStore`.

    Handed a ``StateStore``, it borrows that store's single sqlite connection,
    its ``RLock``, and its ``transaction()`` seam — so goal-state writes land in
    the same database and can join the same atomic unit as task/program writes.
    Construction bootstraps the tables idempotently (``CREATE TABLE IF NOT
    EXISTS`` + forward-compat ALTERs), mirroring the store's own ``_bootstrap``
    style. Since PR3 it also carries the status read/write surface
    (``goal_status`` + ``goal_phase_history``); the other tables stay unused
    until later Tranche 1 PRs migrate onto them.
    """

    def __init__(self, store: "StateStore") -> None:
        self._store = store
        self._bootstrap()

    def _bootstrap(self) -> None:
        # Idempotent — safe to run on every construction (matches
        # StateStore._bootstrap). Uses the shared connection + lock; commits via
        # the store's _commit(), a no-op inside an open transaction().
        with self._store._lock:
            self._store._db.executescript(
                """
                -- One row per goal: the machine state STATUS.md held in YAML
                -- frontmatter before Tranche 1/PR3. This table is now the
                -- source of truth for status; STATUS.md is a generated
                -- full-fidelity view written on every save (the rollback path).
                -- `phase`/`lifecycle` are the current GoalStatus fields;
                -- `state` (PR4) holds the consolidated devclaw.goal.transitions
                -- .State value, stamped by GoalStore on every write (nullable
                -- only for a pre-PR4 row that hasn't been re-saved yet).
                -- `version` (PR4) is the optimistic-concurrency counter
                -- GoalStore.transition() CAS's against, bumped by exactly 1 on
                -- every write; in_flight_* carry the durable pointer to the
                -- goal's running task/program (in_flight_json is the
                -- authoritative serialized InFlight, ref_id/kind denormalized
                -- for later indexing).
                CREATE TABLE IF NOT EXISTS goal_status (
                  goal_id               TEXT PRIMARY KEY,
                  version               INTEGER NOT NULL DEFAULT 0,
                  state                 TEXT,
                  phase                 TEXT,
                  lifecycle             TEXT,
                  blocked_on            TEXT,
                  blocked_kind          TEXT,
                  heal_attempts         INTEGER NOT NULL DEFAULT 0,
                  next_heal_at          TEXT,
                  next                  TEXT,
                  last_plan_at          TEXT,
                  last_tick_at          TEXT,
                  actions_dispatched    INTEGER,
                  donegate_rounds       INTEGER NOT NULL DEFAULT 0,
                  last_eval_verdict     TEXT,
                  last_eval_at          TEXT,
                  last_eval_note        TEXT,
                  last_progress_at      TEXT,
                  no_progress_notified  INTEGER,
                  open_unmerged_pr      TEXT,
                  in_flight_ref_id      TEXT,
                  in_flight_kind        TEXT,
                  in_flight_json        TEXT,
                  updated_at            INTEGER
                );

                -- Append-only phase transitions (STATUS.md phase_history today).
                CREATE TABLE IF NOT EXISTS goal_phase_history (
                  id         INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id    TEXT NOT NULL,
                  phase      TEXT NOT NULL,
                  at         TEXT NOT NULL
                );

                -- Steering lines (inbox.md is a generated mirror; since
                -- #617 nothing reads it back). consumed_at NULL == unread, the
                -- source of truth for what the planner hasn't seen yet;
                -- GoalStore.transition(consume_steering=[...]) stamps it,
                -- atomically with the decision the steering informed.
                CREATE TABLE IF NOT EXISTS goal_steering (
                  id          INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id     TEXT NOT NULL,
                  source      TEXT NOT NULL,
                  line        TEXT NOT NULL,
                  created_at  INTEGER NOT NULL,
                  consumed_at INTEGER
                );

                -- Append-only event log (log.md today).
                CREATE TABLE IF NOT EXISTS goal_log (
                  id       INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id  TEXT NOT NULL,
                  ts       INTEGER NOT NULL,
                  message  TEXT NOT NULL
                );

                -- Grounded record of what each action shipped (deliveries.md
                -- today, PR6). ref_id is the settle's dispatched-ref id and is
                -- REQUIRED (#616 cutoff): it is what makes the INSERT
                -- idempotent under UNIQUE(goal_id, ref_id), and while it could
                -- be NULL that dedupe silently did not apply — SQLite treats
                -- every NULL as distinct. Rows ingested from a pre-cutoff
                -- deliveries.md carry a deterministic 'pre-cutoff:<n>' id.
                CREATE TABLE IF NOT EXISTS goal_deliveries (
                  id          INTEGER PRIMARY KEY AUTOINCREMENT,
                  goal_id     TEXT NOT NULL,
                  ref_id      TEXT NOT NULL,
                  instruction TEXT,
                  body        TEXT,
                  created_at  INTEGER NOT NULL,
                  UNIQUE(goal_id, ref_id)
                );

                -- One row per settled in-flight ref — the dedupe key a later
                -- settle/reconcile pass uses to tell "settled and recorded"
                -- from "the in_flight ref was lost before the result was seen".
                CREATE TABLE IF NOT EXISTS goal_settlements (
                  goal_id    TEXT NOT NULL,
                  ref_id     TEXT NOT NULL,
                  ref_kind   TEXT,
                  status     TEXT,
                  settled_at INTEGER,
                  UNIQUE(goal_id, ref_id)
                );

                -- PROJECT-scoped documents (mission-control borrow item 3):
                -- A goal's own state dies with it, so every new goal on the
                -- same repo relearned build quirks from zero. scope_key is the
                -- NORMALIZED workspace_dir (project_registry._normalize_
                -- workspace — the same join key the registry uses), NOT a
                -- goal/project id: the brief must survive goal cancel+refile
                -- and project re-registration alike. Host-side on purpose —
                -- the sandbox workspace is `git clean -fdx`-wiped per
                -- dispatch, so nothing left in-repo survives between tasks.
                CREATE TABLE IF NOT EXISTS project_docs (
                  scope_key  TEXT NOT NULL,
                  kind       TEXT NOT NULL,
                  content    TEXT,
                  updated_at INTEGER,
                  PRIMARY KEY(scope_key, kind)
                );

                -- goal_id lookups on the append-only / multi-row tables.
                CREATE INDEX IF NOT EXISTS idx_goal_phase_history_goal
                  ON goal_phase_history(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_steering_goal
                  ON goal_steering(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_log_goal
                  ON goal_log(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_deliveries_goal
                  ON goal_deliveries(goal_id, id);
                CREATE INDEX IF NOT EXISTS idx_goal_settlements_goal
                  ON goal_settlements(goal_id, ref_id);
                """
            )

            # Forward-compat ALTERs for DBs bootstrapped by PR2 (which created
            # goal_status WITHOUT phase/lifecycle — those columns were only added
            # in PR3, when the table went live) and for pre-blocked_kind DBs
            # (the column landed with the F8-prerequisite block classification).
            # Idempotent: a fresh DB already has them from the CREATE above, so
            # the duplicate-column error is swallowed. Mirrors
            # StateStore._bootstrap's ALTER pattern.
            for sql in (
                "ALTER TABLE goal_status ADD COLUMN phase TEXT",
                "ALTER TABLE goal_status ADD COLUMN lifecycle TEXT",
                "ALTER TABLE goal_status ADD COLUMN blocked_kind TEXT",
                "ALTER TABLE goal_status ADD COLUMN heal_attempts INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE goal_status ADD COLUMN next_heal_at TEXT",
                "ALTER TABLE goal_status ADD COLUMN open_unmerged_pr TEXT",
                "ALTER TABLE goal_status ADD COLUMN donegate_rounds INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    self._store._db.execute(sql)
                except sqlite3.OperationalError:
                    pass  # column already exists

            self._store._commit()

    # ---- goal_status persistence (PR3: STATUS.md re-backed onto SQLite) ----
    #
    # The store orchestrates STATUS.md-as-view + lazy migration; these methods
    # are the pure DB surface. All borrow the shared connection + lock and use
    # the store's _commit() (a no-op inside an open transaction()), so a status
    # write can join the same atomic unit as a task write in a later PR.

    def has_status(self, goal_id: str) -> bool:
        """Whether a ``goal_status`` row exists — the lazy-migration guard."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT 1 FROM goal_status WHERE goal_id = ? LIMIT 1", (goal_id,)
            ).fetchone()
        return row is not None

    def current_phase(self, goal_id: str) -> "str | None":
        """The stored phase, or None when no row exists. Read by save_status to
        decide whether the phase changed (and a phase_history entry is due)."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT phase FROM goal_status WHERE goal_id = ?", (goal_id,)
            ).fetchone()
        return row["phase"] if row else None

    def read_status(self, goal_id: str) -> GoalStatus:
        """Rehydrate the full :class:`GoalStatus` (incl. in_flight + phase
        history). Caller must ensure a row exists (see :meth:`has_status`)."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT * FROM goal_status WHERE goal_id = ?", (goal_id,)
            ).fetchone()
        return _row_to_status(row, self.read_phase_history(goal_id))

    def write_status(self, goal_id: str, status: GoalStatus) -> None:
        """Upsert the status row. The InFlight is serialized to in_flight_json
        (authoritative) with id/kind denormalized for later indexing; the
        phase_history tuple is NOT written here — it lives in
        goal_phase_history (see :meth:`append_phase_history`).

        ``version`` is bumped by exactly 1 on EVERY write — 1 on the first
        INSERT, ``version + 1`` on every subsequent UPDATE — the counter
        GoalStore.transition() CAS's against and computes its return value
        from (``fresh.version + 1``) without a re-read. ``state`` is written
        verbatim from ``status.state``: this method is a pure DB write, not a
        projector — the CALLER (GoalStore.save_status / .transition /
        .force_block) is responsible for stamping the derived
        devclaw.goal.transitions.State value onto ``status`` first."""
        in_flight_json = None
        in_flight_ref_id = None
        in_flight_kind = None
        if status.in_flight is not None:
            f = status.in_flight
            in_flight_ref_id = f.id
            in_flight_kind = f.ref_kind
            in_flight_json = json.dumps(
                {
                    "engine": f.engine,
                    "tool": f.tool,
                    "id": f.id,
                    "ref_kind": f.ref_kind,
                    "goal": f.goal,
                    "is_done_check": f.is_done_check,
                }
            )
        with self._store._lock:
            self._store._db.execute(
                """
                INSERT INTO goal_status (
                  goal_id, version, state, phase, lifecycle, blocked_on, blocked_kind,
                  heal_attempts, next_heal_at, "next",
                  last_plan_at, last_tick_at, actions_dispatched,
                  donegate_rounds,
                  last_eval_verdict, last_eval_at, last_eval_note, last_progress_at,
                  no_progress_notified, open_unmerged_pr, in_flight_ref_id, in_flight_kind,
                  in_flight_json, updated_at
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(goal_id) DO UPDATE SET
                  version               = goal_status.version + 1,
                  state                 = excluded.state,
                  phase                 = excluded.phase,
                  lifecycle             = excluded.lifecycle,
                  blocked_on            = excluded.blocked_on,
                  blocked_kind          = excluded.blocked_kind,
                  heal_attempts         = excluded.heal_attempts,
                  next_heal_at          = excluded.next_heal_at,
                  "next"                = excluded."next",
                  last_plan_at          = excluded.last_plan_at,
                  last_tick_at          = excluded.last_tick_at,
                  actions_dispatched    = excluded.actions_dispatched,
                  donegate_rounds       = excluded.donegate_rounds,
                  last_eval_verdict     = excluded.last_eval_verdict,
                  last_eval_at          = excluded.last_eval_at,
                  last_eval_note        = excluded.last_eval_note,
                  last_progress_at      = excluded.last_progress_at,
                  no_progress_notified  = excluded.no_progress_notified,
                  open_unmerged_pr      = excluded.open_unmerged_pr,
                  in_flight_ref_id      = excluded.in_flight_ref_id,
                  in_flight_kind        = excluded.in_flight_kind,
                  in_flight_json        = excluded.in_flight_json,
                  updated_at            = excluded.updated_at
                """,
                (
                    goal_id,
                    status.state,
                    status.phase,
                    status.lifecycle,
                    status.blocked_on,
                    status.blocked_kind,
                    status.heal_attempts,
                    status.next_heal_at,
                    status.next,
                    status.last_plan_at,
                    status.last_tick_at,
                    status.actions_dispatched,
                    status.donegate_rounds,
                    status.last_eval_verdict,
                    status.last_eval_at,
                    status.last_eval_note,
                    status.last_progress_at,
                    1 if status.no_progress_notified else 0,
                    status.open_unmerged_pr,
                    in_flight_ref_id,
                    in_flight_kind,
                    in_flight_json,
                    _now_ms(),
                ),
            )
            self._store._commit()

    #: telemetry-only GoalStatus fields GoalStore.update_status_fields() may
    #: touch via :meth:`update_columns` — a column-only UPDATE, never a
    #: full-row rewrite (the mechanism that keeps a stale-snapshot bookkeeping
    #: write from ever clobbering a concurrent phase/lifecycle/in_flight
    #: transition). Keys are GoalStatus field names; values are the
    #: `goal_status` column name (identical today, kept as a mapping so a
    #: future rename only touches one side).
    STATUS_FIELD_COLUMNS: "dict[str, str]" = {
        "last_plan_at": "last_plan_at",
        "last_tick_at": "last_tick_at",
        "last_progress_at": "last_progress_at",
        "no_progress_notified": "no_progress_notified",
        "last_eval_verdict": "last_eval_verdict",
        "last_eval_at": "last_eval_at",
        "last_eval_note": "last_eval_note",
        "donegate_rounds": "donegate_rounds",
        # heal_attempts / next_heal_at are damping bookkeeping (never read by
        # derive_state) — the column-only path exists so the auto-heal's
        # gave-up marker and the prep-recheck backoff window can be stamped
        # on a still-BLOCKED goal without a full-row rewrite.
        "heal_attempts": "heal_attempts",
        "next_heal_at": "next_heal_at",
        # #430: the settle path stamps the unmerged-PR marker column-only (after
        # the atomic ACTION_SETTLED write, once the merge attempt's outcome is
        # known) — a telemetry field derive_state never reads.
        "open_unmerged_pr": "open_unmerged_pr",
    }

    def update_columns(self, goal_id: str, fields: dict) -> None:
        """Column-only ``UPDATE`` for telemetry fields — the mechanism behind
        :meth:`GoalStore.update_status_fields`. Bumps ``version`` by 1 like
        every other write, but touches ONLY the named columns (never phase/
        lifecycle/in_flight/blocked_on/next), so it can never be the write
        that clobbers a concurrent state transition. Caller has already
        validated ``fields`` keys against :data:`STATUS_FIELD_COLUMNS`; a
        no-op (no SQL issued) on an empty dict."""
        if not fields:
            return
        sets = []
        params: list = []
        for key, value in fields.items():
            col = self.STATUS_FIELD_COLUMNS[key]
            if key == "no_progress_notified":
                value = 1 if value else 0
            sets.append(f"{col} = ?")
            params.append(value)
        params.append(_now_ms())
        params.append(goal_id)
        with self._store._lock:
            self._store._db.execute(
                f"UPDATE goal_status SET {', '.join(sets)}, version = version + 1, "
                "updated_at = ? WHERE goal_id = ?",
                params,
            )
            self._store._commit()

    def read_inbox_ingest_cursor(self, goal_id: str) -> int:
        """How many ``inbox.md`` lines this goal had already turned into
        ``goal_steering`` rows before #617 deleted the ingest. READ-ONLY, with
        exactly one caller: :func:`~devclaw.goal.store.view_migration
        .migrate_views_once`, to tell historical (already-acted-on) steering
        from steering that was still unread at the cutoff.

        The column is dropped by the #616 cutoff, which runs immediately after
        that migration — and never exists at all on a database bootstrapped
        after it. Both cases mean the same thing (there is no pre-#617 ingest
        boundary to respect), so both answer 0, checked against the schema
        rather than guessed from an exception."""
        with self._store._lock:
            info = self._store._db.execute("PRAGMA table_info(goal_status)").fetchall()
            if not any(r["name"] == "inbox_ingest_cursor" for r in info):
                return 0
            row = self._store._db.execute(
                "SELECT inbox_ingest_cursor FROM goal_status WHERE goal_id = ?", (goal_id,)
            ).fetchone()
        return int(row["inbox_ingest_cursor"] or 0) if row else 0

    # ---- goal_steering (steering rows — PR5 consumed-at source of truth) ---
    #
    # ``consumed_at IS NULL`` == unread. Rows are the source of truth for
    # WHAT is unread. Consumption
    # (stamping ``consumed_at``) happens ONLY via :meth:`consume_steering_rows`,
    # called from :meth:`GoalStore.transition` so it rides the SAME CAS'd
    # transaction as the decision the steering informed.

    def has_steering_rows(self, goal_id: str) -> bool:
        """Whether ANY ``goal_steering`` row (consumed or not) exists yet —
        the resume guard the one-shot view migration uses: the pre-PR5
        history backfill may only run ONCE, for a goal that predates
        row-backed steering. Idempotent by construction — once any row
        exists, this returns True forever."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT 1 FROM goal_steering WHERE goal_id = ? LIMIT 1", (goal_id,)
            ).fetchone()
        return row is not None

    def bump_status_version(self, goal_id: str) -> None:
        """Bump ``goal_status.version`` by 1 without touching any other column.

        Called by :meth:`GoalStore.append_steering`. New steering invalidates
        every in-flight tick snapshot: a tick that read "no unread steering",
        then crossed an async seam (workspace prep, engine dispatch), must NOT
        commit its dispatch as though it had seen the whole picture. The bump
        makes that tick's own CAS go stale, so it abandons with
        ``TransitionConflict`` and the steer rides the immediate retry instead
        of waiting a full heartbeat.

        This used to happen as a SIDE EFFECT of ``set_inbox_ingest_cursor``,
        which #617 deleted along with the inbox ingest. The behaviour was
        load-bearing and the side effect was not the reason for it, so it is
        now its own named write. No-op when the goal has no status row yet
        (nothing has an in-flight snapshot of a row that does not exist)."""
        with self._store._lock:
            self._store._db.execute(
                "UPDATE goal_status SET version = version + 1, updated_at = ? "
                "WHERE goal_id = ?",
                (_now_ms(), goal_id),
            )
            self._store._commit()

    def steering_timestamps_by_goal(self, source: str) -> "dict[str, list[int]]":
        """``goal_id -> [created_at_ms, ...]`` for every ``goal_steering`` row
        with this ``source``, fleet-wide. One grouped read, no mutation.

        The H4 trend signal counts human corrections per goal per window. It
        used to get that by parsing each goal's ``inbox.md`` for the
        ``- [denys <ts>] `` prefix — reading a generated view back, which #617
        forbids and which also undercounted, since the file's timestamp is the
        rendered one rather than the row's. The ``source`` column has been the
        structured home for exactly this question since PR5."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT goal_id, created_at FROM goal_steering WHERE source = ? "
                "ORDER BY created_at ASC",
                (source,),
            ).fetchall()
        out: "dict[str, list[int]]" = {}
        for r in rows:
            out.setdefault(r["goal_id"], []).append(int(r["created_at"] or 0))
        return out

    def append_steering_rows(
        self, goal_id: str, lines: "list[str]", *, source: str,
        created_at_ms: "int | None" = None, consumed: bool = False,
    ) -> "list[int]":
        """INSERT one ``goal_steering`` row per line, in order. ``line`` is
        stored VERBATIM — the one-shot view migration passes raw historical
        ``inbox.md`` lines (which may carry an old ``[source ts]`` prefix;
        this method never parses one). ``consumed=True`` stamps
        ``consumed_at = created_at`` immediately — used ONLY by the lazy
        pre-PR5 migration to mark already-acted-on history so it's never
        re-fed to the planner; the steering-append default (``consumed=False``)
        leaves ``consumed_at`` NULL, per the new unread-by-row-id model.
        Returns the inserted rowids in insertion (== id) order."""
        if not lines:
            return []
        ts = created_at_ms if created_at_ms is not None else _now_ms()
        ids: list[int] = []
        with self._store._lock:
            for line in lines:
                cur = self._store._db.execute(
                    "INSERT INTO goal_steering (goal_id, source, line, created_at, consumed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (goal_id, source, line, ts, ts if consumed else None),
                )
                ids.append(cur.lastrowid)
            self._store._commit()
        return ids

    def unread_steering_rows(self, goal_id: str) -> "list[sqlite3.Row]":
        """Unconsumed ``goal_steering`` rows, oldest first — ``consumed_at IS
        NULL`` is the unread marker PR5 makes the source of truth. Each row
        carries ``id`` / ``source`` / ``line`` (plus the sqlite defaults);
        callers needing exact-id consumption read ``row["id"]`` and thread it
        into :meth:`GoalStore.transition`'s ``consume_steering=``."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT id, source, line FROM goal_steering "
                "WHERE goal_id = ? AND consumed_at IS NULL ORDER BY id ASC",
                (goal_id,),
            ).fetchall()
        return rows

    def consume_steering_rows(self, goal_id: str, ids: "list[int]", consumed_at_ms: int) -> None:
        """Stamp ``consumed_at`` on EXACTLY the given row ids — the exact-id
        consumption :meth:`GoalStore.transition`'s ``consume_steering=``
        threads through, so a row inserted mid-plan (not among ``ids``) keeps
        ``consumed_at`` NULL and is seen next tick — the fix for
        "steer-during-planner-await lost" (the old count-based cursor
        consumed EVERYTHING that existed at write time, including rows the
        planner never saw). No-op on empty ``ids`` (also avoids an ``IN ()``
        empty-tuple SQL error). The ``AND consumed_at IS NULL`` guard makes a
        double-consume of the same id a no-op rather than clobbering an
        earlier (real) consumed_at timestamp."""
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        with self._store._lock:
            self._store._db.execute(
                f"UPDATE goal_steering SET consumed_at = ? "
                f"WHERE goal_id = ? AND id IN ({placeholders}) AND consumed_at IS NULL",
                (consumed_at_ms, goal_id, *ids),
            )
            self._store._commit()

    # ---- goal_phase_history (append-only phase transitions) ----------------

    def read_phase_history(self, goal_id: str) -> "tuple[dict, ...]":
        """The goal's phase transitions in append order — the tuple that lands
        on ``GoalStatus.phase_history`` and in the STATUS.md view."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT phase, at FROM goal_phase_history WHERE goal_id = ? ORDER BY id ASC",
                (goal_id,),
            ).fetchall()
        return tuple({"phase": r["phase"], "at": r["at"]} for r in rows)

    def append_phase_history(self, goal_id: str, phase: str, at: str) -> None:
        """Append one phase transition. Called by save_status when the phase
        differs from what's stored (one entry per entry-to-a-new-phase)."""
        with self._store._lock:
            self._store._db.execute(
                "INSERT INTO goal_phase_history (goal_id, phase, at) VALUES (?, ?, ?)",
                (goal_id, phase, at),
            )
            self._store._commit()

    def seed_phase_history(self, goal_id: str, entries: "tuple[dict, ...]") -> None:
        """Bulk-insert existing phase entries verbatim — the lazy migration path
        that carries a pre-cutoff STATUS.md's phase_history onto the table."""
        if not entries:
            return
        with self._store._lock:
            self._store._db.executemany(
                "INSERT INTO goal_phase_history (goal_id, phase, at) VALUES (?, ?, ?)",
                [(goal_id, str(e["phase"]), str(e["at"])) for e in entries],
            )
            self._store._commit()

    # ---- goal_log (append-only event log — log.md today, PR6) --------------
    #
    # log.md is a generated OUTPUT view — written, never read back (#617).
    # Rows store the MIRROR-FORMATTED line verbatim (the PR5 rule — see
    # ``append_steering``), so ``recent_log`` reads back byte-identical text
    # to the pre-PR6 file-tail read.

    def has_log_rows(self, goal_id: str) -> bool:
        """Whether ANY ``goal_log`` row exists yet — the resume guard the
        one-shot view migration uses so a log.md is ingested at most once."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT 1 FROM goal_log WHERE goal_id = ? LIMIT 1", (goal_id,)
            ).fetchone()
        return row is not None

    def append_log_row(self, goal_id: str, line: str, ts_ms: int) -> None:
        """INSERT one ``goal_log`` row. ``line`` is the FULL formatted mirror
        line (``- [<iso>] <message>``), stored verbatim in ``message`` — the
        same mirror-formatted-text rule PR5's steering rows follow. ``ts`` is
        ordering-only (the ms clock; nothing parses it back out for display)."""
        with self._store._lock:
            self._store._db.execute(
                "INSERT INTO goal_log (goal_id, ts, message) VALUES (?, ?, ?)",
                (goal_id, ts_ms, line),
            )
            self._store._commit()

    def append_log_rows(self, goal_id: str, lines: "list[str]", ts_ms: int) -> None:
        """Bulk INSERT, in order — the one-shot lazy-migration path that
        carries a pre-cutoff log.md's lines onto rows verbatim, in file order.
        No-op on an empty list (skips a pointless commit)."""
        if not lines:
            return
        with self._store._lock:
            self._store._db.executemany(
                "INSERT INTO goal_log (goal_id, ts, message) VALUES (?, ?, ?)",
                [(goal_id, ts_ms, line) for line in lines],
            )
            self._store._commit()

    def recent_log_rows(self, goal_id: str, n: int) -> "list[str]":
        """The last ``n`` ``message`` values, in natural (ascending) order —
        mirrors the pre-PR6 file read's ``lines[-n:]`` slice. Queried
        ``ORDER BY id DESC LIMIT n`` (cheap on the goal_id+id index) then
        reversed in Python, since SQL has no "last n in original order" in
        one direction."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT message FROM goal_log WHERE goal_id = ? ORDER BY id DESC LIMIT ?",
                (goal_id, n),
            ).fetchall()
        return [r["message"] for r in reversed(rows)]

    def all_log_rows(self, goal_id: str) -> "list[str]":
        """Every ``message`` for ``goal_id``, in natural (ascending) order —
        unlike :meth:`recent_log_rows` (bounded tail), this reads the FULL
        history. Used by the one-shot view migration's settlement seed, which
        needs every historical settle line, not just the recent tail, to seed
        ``goal_settlements`` identically to what the old
        ``log_contains(f" {id} → ")`` guard used to answer."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT message FROM goal_log WHERE goal_id = ? ORDER BY id ASC",
                (goal_id,),
            ).fetchall()
        return [r["message"] for r in rows]

    # ---- goal_deliveries (grounded evidence — deliveries.md today, PR6) ----
    #
    # Same mirror-formatted-text rule as goal_log. ``ref_id`` is the
    # idempotency key a settle passes (the in-flight ref's id) and is REQUIRED
    # since the #616 cutoff: every insert goes through ``INSERT OR IGNORE``
    # against ``UNIQUE(goal_id, ref_id)``, closing the PR4-review nuance where
    # a ``TransitionConflict`` landing in the settle-retry window could append
    # the SAME delivery twice. It used to be nullable, which read as "dedupe
    # is optional" when the truth was worse: SQLite treats every NULL as
    # distinct, so the constraint silently did not apply to those rows.

    def has_delivery_rows(self, goal_id: str) -> bool:
        """Whether ANY ``goal_deliveries`` row exists yet — the resume guard
        the one-shot view migration uses."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT 1 FROM goal_deliveries WHERE goal_id = ? LIMIT 1", (goal_id,)
            ).fetchone()
        return row is not None

    def append_delivery_row(
        self, goal_id: str, ref_id: str, block: str, ts_ms: int,
        *, instruction: str = "",
    ) -> bool:
        """INSERT one ``goal_deliveries`` row. ``block`` is the FULL rendered
        mirror section (``## [<iso>] <instruction>\\n\\n<body>\\n\\n``), stored
        verbatim in ``body`` so :meth:`GoalStore.recent_deliveries`'s
        reconstruction is byte-identical to the pre-PR6 file-tail read.
        ``instruction`` is denormalized into its own column for future
        queries (never parsed back out of ``block``).

        ``ref_id`` is REQUIRED (#616 cutoff). It is the idempotency key: the
        insert is ``INSERT OR IGNORE`` against ``UNIQUE(goal_id, ref_id)``, so
        a duplicate ref_id for this goal is silently dropped (the retry-window
        fix — see the section docstring above). It used to be nullable, and a
        NULL took an unconditional-INSERT branch instead — which read as
        "idempotency is optional" when the truth was that SQLite treats every
        NULL as distinct, so the UNIQUE constraint silently did not apply.
        Returns True iff a row was actually inserted, so the caller
        (``GoalStore.append_delivery``) can skip the file mirror too on the
        ignored path — a duplicate ref_id must never produce a duplicate
        section in deliveries.md."""
        with self._store._lock:
            cur = self._store._db.execute(
                "INSERT OR IGNORE INTO goal_deliveries "
                "(goal_id, ref_id, instruction, body, created_at) VALUES (?, ?, ?, ?, ?)",
                (goal_id, ref_id, instruction, block, ts_ms),
            )
            inserted = cur.rowcount == 1
            self._store._commit()
        return inserted

    def goal_created_at_ms_map(self) -> "dict[str, int]":
        """``goal_id -> creation timestamp (ms)`` for every goal with log rows.

        The age source for the derived project hold (spec 010 FR-005, amended):
        goals carry no ``created_at`` column, but ``GoalService.create_goal``
        writes a "goal created" log row as its first act, so the earliest
        ``goal_log`` timestamp IS the creation moment. One grouped query for
        the whole fleet — the holder map is computed once per heartbeat sweep,
        so this must not become a per-goal round trip.

        A goal with no log rows (only reachable for a hand-seeded fixture) is
        simply absent; callers fall back to a stable ordering key so the holder
        stays deterministic rather than arrival-dependent."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT goal_id, MIN(ts) AS created_at FROM goal_log GROUP BY goal_id"
            ).fetchall()
        return {r["goal_id"]: r["created_at"] for r in rows if r["created_at"] is not None}

    def recent_delivery_blocks(self, goal_id: str) -> "list[str]":
        """Every delivery ``body`` for ``goal_id``, oldest first.
        :meth:`GoalStore.recent_deliveries` char-tails the joined text, so it
        needs the FULL sequence — goals carry at most a few dozen deliveries,
        so reading them all is fine."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT body FROM goal_deliveries WHERE goal_id = ? ORDER BY id ASC",
                (goal_id,),
            ).fetchall()
        return [r["body"] for r in rows]

    def delivery_records(self, goal_id: str) -> "list[tuple[str, str, str]]":
        """``(ref_id, instruction, body)`` per delivery, oldest first — the
        structured form of :meth:`recent_delivery_blocks`.

        The saga feed-forward (spec 012 US1) needs ``ref_id`` to join each
        delivery to its settlement status, and ``instruction`` (the #550
        display objective) separately from the body it is rendered into."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT ref_id, instruction, body FROM goal_deliveries "
                "WHERE goal_id = ? ORDER BY id ASC",
                (goal_id,),
            ).fetchall()
        return [(r["ref_id"], r["instruction"] or "", r["body"] or "") for r in rows]

    def settlement_statuses(self, goal_id: str) -> "dict[str, str]":
        """``ref_id -> status`` for every recorded settlement of ``goal_id``.

        The authoritative terminal verdict (``done`` / ``failed``) lives here,
        not in the delivery body — the saga feed-forward joins the two rather
        than inferring failure from the shape of a text blob."""
        with self._store._lock:
            rows = self._store._db.execute(
                "SELECT ref_id, status FROM goal_settlements WHERE goal_id = ?",
                (goal_id,),
            ).fetchall()
        return {r["ref_id"]: r["status"] for r in rows if r["ref_id"] and r["status"]}

    # ---- project_docs (repo-scoped, outlives any one goal) ----------------
    #
    # An atomic per-(scope_key, kind) upsert, keyed by normalized workspace
    # path rather than goal_id. One kind today: ``repo_brief`` — the durable
    # repo facts workers hand back (build quirks, test gotchas) that get
    # prepended to future dispatches on the same repo.

    PROJECT_DOC_KINDS = frozenset({"repo_brief"})

    def write_project_doc(self, scope_key: str, kind: str, content: str, ts_ms: int) -> None:
        """Upsert the current project-scoped document for ``(scope_key, kind)``."""
        assert kind in self.PROJECT_DOC_KINDS, f"write_project_doc: unknown kind {kind!r}"
        with self._store._lock:
            self._store._db.execute(
                "INSERT INTO project_docs (scope_key, kind, content, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(scope_key, kind) DO UPDATE SET "
                "content = excluded.content, updated_at = excluded.updated_at",
                (scope_key, kind, content, ts_ms),
            )
            self._store._commit()

    def read_project_doc(self, scope_key: str, kind: str) -> "str | None":
        """The current project-scoped document, or None when no worker has
        handed back notes for this repo yet."""
        assert kind in self.PROJECT_DOC_KINDS, f"read_project_doc: unknown kind {kind!r}"
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT content FROM project_docs WHERE scope_key = ? AND kind = ?",
                (scope_key, kind),
            ).fetchone()
        return row["content"] if row else None

    # ---- goal_settlements (settled-and-recorded truth — PR7) --------------
    #
    # One row per settled in-flight ref. Table exists since PR2 (created
    # empty); PR7 is the first thing that reads/writes it. No file mirror —
    # there's no settlements.md view, so these are plain row writes that
    # simply join whichever transaction() (if any) is open, same as every
    # other GoalState write.

    def has_settlement(self, goal_id: str, ref_id: str) -> bool:
        """Whether ``ref_id`` has a recorded settlement for ``goal_id`` — the
        row-backed replacement for the old ``log_contains(f" {id} → ")``
        string-match guard."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT 1 FROM goal_settlements WHERE goal_id = ? AND ref_id = ? LIMIT 1",
                (goal_id, ref_id),
            ).fetchone()
        return row is not None

    def has_any_settlements(self, goal_id: str) -> bool:
        """Whether ANY settlement row exists yet for ``goal_id`` — the resume
        guard the one-shot view migration's seed uses so the historical-log
        scan runs at most once per goal."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT 1 FROM goal_settlements WHERE goal_id = ? LIMIT 1", (goal_id,)
            ).fetchone()
        return row is not None

    def record_settlement(
        self, goal_id: str, ref_id: str, ref_kind: "str | None", status: "str | None",
        settled_at_ms: int,
    ) -> bool:
        """INSERT OR IGNORE one settlement row. Idempotent against
        ``UNIQUE(goal_id, ref_id)`` — a settle txn retried after a
        TransitionConflict rollback (or the lazy-seed re-scanning a line
        whose token collides with a real settlement already recorded) is a
        silent no-op, same dedup shape as :meth:`append_delivery_row`.
        Returns True iff a row was actually inserted."""
        with self._store._lock:
            cur = self._store._db.execute(
                "INSERT OR IGNORE INTO goal_settlements "
                "(goal_id, ref_id, ref_kind, status, settled_at) VALUES (?, ?, ?, ?, ?)",
                (goal_id, ref_id, ref_kind, status, settled_at_ms),
            )
            inserted = cur.rowcount == 1
            self._store._commit()
        return inserted


def _row_to_status(row, phase_history: "tuple[dict, ...]") -> GoalStatus:
    """Reconstruct a :class:`GoalStatus` from a ``goal_status`` row + its phase
    history. Mirrors the field-by-field degrade of the old STATUS.md reader so a
    migrated goal loads identically."""
    in_flight = None
    if row["in_flight_json"]:
        f = json.loads(row["in_flight_json"])
        in_flight = InFlight(
            engine=f["engine"],
            tool=f["tool"],
            id=f["id"],
            ref_kind=f["ref_kind"],
            goal=f.get("goal", ""),
            is_done_check=bool(f.get("is_done_check", False)),
        )
    return GoalStatus(
        phase=row["phase"] or "idle",
        lifecycle=row["lifecycle"] or None,
        in_flight=in_flight,
        blocked_on=row["blocked_on"] or None,
        # NULL on a row that predates the column (pre-blocked_kind DB, lazily
        # ALTERed by _bootstrap) reads as "" — unclassified, same as the default.
        blocked_kind=row["blocked_kind"] or "",
        heal_attempts=int(row["heal_attempts"] or 0),
        next_heal_at=row["next_heal_at"] or None,
        next=row["next"] or "",
        last_plan_at=row["last_plan_at"] or None,
        last_tick_at=row["last_tick_at"] or None,
        actions_dispatched=int(row["actions_dispatched"] or 0),
        donegate_rounds=int(row["donegate_rounds"] or 0),
        last_eval_verdict=row["last_eval_verdict"] or None,
        last_eval_at=row["last_eval_at"] or None,
        last_eval_note=row["last_eval_note"] or "",
        last_progress_at=row["last_progress_at"] or None,
        no_progress_notified=bool(row["no_progress_notified"]),
        open_unmerged_pr=row["open_unmerged_pr"] or None,
        phase_history=phase_history,
        state=row["state"] or None,
        version=int(row["version"] or 0),
    )
