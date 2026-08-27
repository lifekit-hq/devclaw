"""Content rows — everything ``GoalState`` persists that ISN'T the status row.

:class:`GoalStateContentMixin` carries the pure-DB surface for the content-side
tables: ``goal_steering``, ``goal_log``, ``goal_deliveries``, ``project_docs``
and ``goal_settlements`` — the rows the store's
:class:`~devclaw.goal.store.content.GoalContentMixin` reads and writes through.

Split out of :class:`~devclaw.goal.state.GoalState` as a mixin on the SAME
instance — every method here runs against the ``self._store`` the base
``GoalState`` borrows (the shared sqlite connection, its ``RLock``, its
``_commit()`` no-op-inside-``transaction()`` seam), so the transaction /
single-writer semantics are byte-identical to the pre-split monolith.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from ..state_store import _now_ms

if TYPE_CHECKING:
    from ..state_store import StateStore


class GoalStateContentMixin:
    if TYPE_CHECKING:
        # The composing class owns this (its docstring names the same contract in
        # prose); declared under TYPE_CHECKING so the seam is checked, never run.
        _store: StateStore

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
                assert cur.lastrowid is not None  # INSERT always assigns a rowid
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

    # ---- goal_issue_identity (spec 022 US1) --------------------------------
    #
    # The store-level enforcement of (project_id, issue_key) uniqueness for
    # one_shot companion goals. PRIMARY KEY on (project_id, issue_key) implies
    # NOT NULL on both — the spec 012 lesson: a nullable ref silently disables
    # its own uniqueness constraint.

    def _claim_issue_identity(
        self, project_id: str, issue_key: str, goal_id: str, now_ms: int
    ) -> "tuple[bool, str]":
        """Try to register (project_id, issue_key) → goal_id.

        Returns ``(True, goal_id)`` when this call wins the race and creates
        the row. Returns ``(False, existing_goal_id)`` when the row already
        exists — another caller won the race or there's an active goal. The
        PRIMARY KEY constraint makes this atomic: two concurrent callers cannot
        both return True.
        """
        with self._store._lock:
            try:
                self._store._db.execute(
                    "INSERT INTO goal_issue_identity"
                    "(project_id, issue_key, goal_id, created_at) VALUES(?,?,?,?)",
                    (project_id, issue_key, goal_id, now_ms),
                )
                self._store._commit()
                return (True, goal_id)
            except sqlite3.IntegrityError:
                row = self._store._db.execute(
                    "SELECT goal_id FROM goal_issue_identity "
                    "WHERE project_id=? AND issue_key=?",
                    (project_id, issue_key),
                ).fetchone()
                existing = row[0] if row else goal_id
                return (False, existing)

    def _rearm_issue_identity(
        self,
        project_id: str,
        issue_key: str,
        old_goal_id: str,
        new_goal_id: str,
        now_ms: int,
    ) -> bool:
        """CAS-update the identity row from a completed goal to a fresh one.

        The UPDATE only fires when the row still points at ``old_goal_id`` — so
        at most one concurrent re-arm caller updates and returns True; any
        other concurrent caller gets rowcount==0 and returns False (must then
        re-read the winner's goal_id and return "attached").
        """
        with self._store._lock:
            cur = self._store._db.execute(
                "UPDATE goal_issue_identity SET goal_id=?, created_at=? "
                "WHERE project_id=? AND issue_key=? AND goal_id=?",
                (new_goal_id, now_ms, project_id, issue_key, old_goal_id),
            )
            updated = cur.rowcount == 1
            self._store._commit()
        return updated

    def _lookup_issue_identity(self, project_id: str, issue_key: str) -> "str | None":
        """Return the goal_id for (project_id, issue_key), or None."""
        with self._store._lock:
            row = self._store._db.execute(
                "SELECT goal_id FROM goal_issue_identity "
                "WHERE project_id=? AND issue_key=?",
                (project_id, issue_key),
            ).fetchone()
        return row[0] if row else None
