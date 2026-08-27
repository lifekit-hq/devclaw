"""Content surfaces — everything the goal accretes that ISN'T the status row.

:class:`GoalContentMixin` carries the log, settlements, deliveries, spec, and
inbox/steering surfaces — each row-first (SQLite is the source of truth since
Tranche 1/PR5–PR7) with the ``.md`` files as generated mirrors. (The
checklist/firmed-draft/discovery contract docs died with the host-cognition
chain, spec 008 shrink.)

**Nothing here reads a view back (#617).** Every ``.md`` this module writes is
a write-only projection; the rows are the only input to a decision. The
markdown that existed before that rule was enforced is ingested exactly once,
by :func:`~devclaw.goal.store.view_migration.migrate_views_once` at store
construction. Re-adding a read of ``log.md`` / ``deliveries.md`` /
``inbox.md`` / ``STATUS.md`` re-opens a second writer to goal state that
``GoalStore.transition()``'s CAS choke point does not cover — see
``tests/test_views_never_read_back.py``.

Split out of ``GoalStore`` as a mixin on the SAME instance — every method here
runs against the ``self._state`` / ``self._goal_state`` / ``self._now`` /
``self._dir`` / ``self._pending_mirrors`` the base ``GoalStore`` owns, so the
row-first ordering, mirror-deferral, and lazy-ingest semantics are byte-identical
to the pre-split monolith.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...state_store import _now_ms

if TYPE_CHECKING:
    from datetime import datetime
    from pathlib import Path
    from typing import Callable

    from ...state_store import StateStore
    from ..state import GoalState


class GoalContentMixin:
    if TYPE_CHECKING:
        # The composing class owns these (its docstring names the same contract in
        # prose); declared under TYPE_CHECKING so the seam is checked, never run.
        _state: StateStore
        _goal_state: GoalState
        _now: Callable[[], datetime]
        _pending_mirrors: dict[str, list]

        def _dir(self, goal_id: str) -> Path: ...
        def _write_atomic(self, goal_id: str, name: str, text: str) -> None: ...

    # ---- log (events) — PR6: goal_log rows are the source of truth --------
    #
    # log.md is a generated OUTPUT view: written on every append, never read.

    def append_log(self, goal_id: str, message: str, *, mirror: bool = True) -> None:
        """Append one log line. Row-first, then the log.md mirror — the
        OPPOSITE order from ``append_steering``'s file-first, and
        deliberately so: inbox.md is a hand-append INPUT that self-heals via
        re-ingestion on the next read, so PR5 protected against losing a
        steering line by writing the file first. log.md is a pure OUTPUT
        view — a mirror line
        without a row would be silently invisible to every DECISION reader
        (``recent_log``) forever, while a row without a
        mirror line is merely a cosmetically stale (but harmless) log.md
        after a crash between the two writes. Rows are truth, so the row
        write must never be the one left dangling.

        ``mirror=False`` (PR7): skip the file append and remember the
        rendered line in ``self._pending_mirrors[goal_id]`` instead — for
        callers writing INSIDE an open ``transaction()`` (the atomic
        dispatch/settle units), where a file write must never race a
        possible rollback. The caller flushes via ``render_mirrors()`` after
        its transaction commits, or drops via ``discard_pending_mirrors()``
        on the exception path."""
        line = f"- [{self._now().isoformat(timespec='seconds')}] {message}"
        self._goal_state.append_log_row(goal_id, line, _now_ms())
        if not mirror:
            self._pending_mirrors.setdefault(goal_id, []).append(("log", line))
            return
        d = self._dir(goal_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "log.md"
        if not path.exists():
            path.write_text(f"# {goal_id} — log\n\n")
        with path.open("a") as fh:
            fh.write(f"{line}\n")

    def recent_log(self, goal_id: str, n: int = 20) -> str:
        """The last ``n`` log lines, newline-joined, oldest-of-the-tail
        first — read from ``goal_log`` rows; ``log.md`` is never consulted."""
        return "\n".join(self._goal_state.recent_log_rows(goal_id, n))

    # ---- settlements (settled-and-recorded truth — PR7) --------------------
    #
    # goal_settlements has no corresponding .md view — these are plain row
    # writes/reads. record_settlement joins whichever transaction() (if any)
    # is open. Goals that settled work before PR7 existed were seeded from
    # their historical goal_log rows by the one-shot view migration.

    def record_settlement(
        self, goal_id: str, *, ref_id: str, ref_kind: "str | None", status: "str | None",
    ) -> bool:
        """Record ONE settled ref. INSERT OR IGNORE against
        ``UNIQUE(goal_id, ref_id)`` — a settle retried after a
        ``TransitionConflict`` rollback re-records the identical row, no
        duplicate. Row-only; no file mirror to defer."""
        return self._goal_state.record_settlement(goal_id, ref_id, ref_kind, status, _now_ms())

    def is_settled(self, goal_id: str, ref_id: str) -> bool:
        """Whether ``ref_id`` has a recorded settlement for ``goal_id`` — the
        orphan sweep's "settled and recorded" vs. "lost mid-flight" guard.
        Rows only: goals that settled work before ``goal_settlements`` existed
        were seeded from their historical log rows by the one-shot view
        migration, so this answers identically without re-scanning anything."""
        return self._goal_state.has_settlement(goal_id, ref_id)

    # ---- deliveries (grounded evidence for the evaluator) — PR6: rows are
    # the source of truth, deliveries.md the mirror. Same shape as log.

    def append_delivery(
        self, goal_id: str, instruction: str, body: str, *,
        ref_id: str, mirror: bool = True,
    ) -> None:
        """Append a grounded record of what one action actually shipped — the
        agent's own summary + the gate verdict + the PR url, captured in-process
        from the full task row (not the old over-the-wire blob). This is the
        substrate the direction evaluator reads to judge shipped-vs-correct.

        ``ref_id`` (PR6) is the in-flight ref's id, threaded through by the
        settle call site so a duplicate settle of the SAME ref (e.g. a
        ``TransitionConflict`` retry landing after the first settle already
        recorded the delivery) is a no-op: no second row, no second section
        in deliveries.md. REQUIRED since the #616 cutoff — it used to default
        to ``None``, which took an unconditional-insert path and quietly
        turned the idempotency guarantee off for every caller that forgot it.
        Row-first, then the file mirror, ONLY when a row
        was actually inserted — a duplicate ref_id must never produce a
        duplicate section in the view (see ``GoalState.append_delivery_row``).

        ``mirror=False`` (PR7): once a row IS inserted, skip the file append
        and remember the rendered section in ``self._pending_mirrors`` —
        same deferral contract as :meth:`append_log`, for callers writing
        inside an open ``transaction()``."""
        ts = self._now().isoformat(timespec="seconds")
        block = f"## [{ts}] {instruction}\n\n{body.strip()}\n\n"
        inserted = self._goal_state.append_delivery_row(
            goal_id, ref_id, block, _now_ms(), instruction=instruction,
        )
        if not inserted:
            return  # duplicate ref_id — silent idempotency is the point
        if not mirror:
            self._pending_mirrors.setdefault(goal_id, []).append(("delivery", block))
            return
        d = self._dir(goal_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "deliveries.md"
        if not path.exists():
            path.write_text(f"# {goal_id} — deliveries (what each action shipped)\n\n")
        with path.open("a") as fh:
            fh.write(block)

    def write_repo_brief(self, scope_key: str, content: str) -> None:
        """Upsert the accumulated repo brief for a workspace scope key."""
        self._goal_state.write_project_doc(scope_key, "repo_brief", content, _now_ms())

    def read_repo_brief(self, scope_key: str) -> str:
        """The accumulated repo brief, or '' when no worker on this repo has
        handed back notes yet."""
        return self._goal_state.read_project_doc(scope_key, "repo_brief") or ""

    def write_spec(self, goal_id: str, spec: str) -> None:
        """Persist the agreed scope spec — what to build, what's out, constraints.
        Produced by the OpenClaw waiter's scope_grill conversation BEFORE the goal
        is created, passed in through create_goal, and read by the evaluator so
        done is judged against the shared contract."""
        ts = self._now().isoformat(timespec="seconds")
        self._write_atomic(
            goal_id, "spec.md", f"# {goal_id} — spec\n\n_agreed {ts}_\n\n{spec.strip()}\n"
        )

    def read_spec(self, goal_id: str) -> str:
        path = self._dir(goal_id) / "spec.md"
        return path.read_text() if path.exists() else ""

    # ---- executing-feature reference (spec 008 US1, D6) --------------------

    def write_executing_feature(self, goal_id: str, feature_dir: str) -> None:
        """Record which speckit feature directory (workspace-relative, e.g.
        ``specs/012-widget``) the goal is currently executing — best-effort,
        set at dispatch so the done-gate can ground on the right ``spec.md``.
        A blank value clears it (absent ⇒ done-gate falls back to
        ``done_when``). Plain file doc, like :meth:`write_spec`; no devclaw.db
        table (data-model entity 4)."""
        self._write_atomic(goal_id, "executing-feature.txt", (feature_dir or "").strip() + "\n")

    def read_executing_feature(self, goal_id: str) -> str:
        """The recorded executing-feature directory, or ``""`` when none is
        recorded (transition-safe: absent ⇒ the done-gate falls back to
        ``done_when``)."""
        path = self._dir(goal_id) / "executing-feature.txt"
        return path.read_text().strip() if path.exists() else ""

    def increment_records(self, goal_id: str) -> "list":
        """Every settled increment of ``goal_id`` as an
        :class:`~devclaw.goal.prior_increments.IncrementRecord`, oldest first —
        the saga feed-forward's input (spec 012 US1).

        Joins ``goal_deliveries`` (objective + devclaw's own outcome lines) with
        ``goal_settlements`` (the authoritative terminal status) by ``ref_id``.
        Only devclaw-generated facts survive the parse; the worker's own
        ``Agent summary:`` prose is dropped, because one worker's unverified
        self-report must not become the next worker's premise (#358).

        Read-only and transaction-free: safe on the tick's dispatch path, which
        calls it only AFTER the ``should_plan`` gate (the zero-token idle
        guard). Rows are the source of truth; ``deliveries.md`` is a generated
        mirror and is never read back for a decision (constitution IV)."""
        from ..prior_increments import parse_record  # local: avoids an import cycle

        statuses = self._goal_state.settlement_statuses(goal_id)
        return [
            parse_record(instruction, body, statuses.get(ref_id) if ref_id else None)
            for ref_id, instruction, body in self._goal_state.delivery_records(goal_id)
        ]
    def goal_created_at_map(self) -> "dict[str, int]":
        """``goal_id -> creation timestamp (ms)`` for the whole fleet — the age
        source the derived project hold orders by (spec 010 FR-005, amended).
        Read-only, one grouped query, safe on the tick path."""
        return self._goal_state.goal_created_at_ms_map()

    def recent_deliveries(self, goal_id: str, chars: int = 8000) -> str:
        """The tail of the deliveries record (bounded — the evaluator's
        grounding context). Reconstructs ``header + "".join(blocks)`` from
        ``goal_deliveries`` rows — byte-identical to the pre-PR6
        ``deliveries.md`` file-tail read, since the header format
        (``# {goal_id} — deliveries (what each action shipped)\\n\\n``) is
        the one constant :meth:`append_delivery` has ever written."""
        blocks = self._goal_state.recent_delivery_blocks(goal_id)
        if not blocks:
            return ""
        text = f"# {goal_id} — deliveries (what each action shipped)\n\n" + "".join(blocks)
        return text[-chars:] if len(text) > chars else text

    # ---- inbox (steering) — PR5: goal_steering rows are the source of truth
    #
    # ``inbox.md`` is a generated MIRROR and nothing more (#617). Before that
    # ruling it was also a hand-append INPUT: a line typed straight into the
    # file was lazily ingested into a row on the next steering read, which
    # made whoever last touched the file a second writer to goal state — one
    # ``GoalStore.transition()``'s CAS choke point does not cover. Steering
    # now enters through exactly one door, the ``steer_goal`` verb, which is
    # the same rule recovery already follows: recovery is a verb, not a fake
    # steer. Historical hand-typed lines were ingested once by the one-shot
    # view migration; consumption is still by exact row id, never by counting
    # lines — that count-based model is what let a steer landing during the
    # planner's cognition await get silently swallowed.

    def unread_steering_rows(self, goal_id: str) -> "list[tuple[int, str]]":
        """Unread steering — the exact-id source of truth PR5 introduced.
        Returns ``[(id, line), ...]`` for every ``goal_steering`` row with
        ``consumed_at IS NULL``, oldest first; ``inbox.md`` is not consulted
        (#617). Callers that
        need to consume EXACTLY what they read (the tick's post-plan
        transition) thread the ids into ``GoalStore.transition(...,
        consume_steering=[...])`` — that call, not this read, is what marks
        them consumed."""
        rows = self._goal_state.unread_steering_rows(goal_id)
        return [(r["id"], r["line"]) for r in rows]

    def unread_steering(self, goal_id: str) -> str:
        """Unread steering as one newline-joined string — the display read,
        for callers that render steering rather than consuming it. Built on
        :meth:`unread_steering_rows` (the row-backed source of truth PR5
        introduced). Consumption is by exact row id via
        ``GoalStore.transition(consume_steering=...)``, never by a cursor —
        this read-only helper has nothing to do with that; it never took a
        ``status`` argument to consume from (PR8 dropped the long-dead
        parameter)."""
        return "\n".join(line for _, line in self.unread_steering_rows(goal_id)).strip()

    def has_unread_human_steering(self, goal_id: str) -> bool:
        """Whether any unread steering row was written by a human — any
        ``source`` not prefixed ``auto-`` (machine appenders, e.g. the
        done-gate's ``auto-eval`` corrections, use that prefix). The
        blocked branch of the advance gate unblocks a parked goal only on
        this, never on plain unread-row presence: the ``donegate_churn``
        brake parks a goal AND records its corrections as steering, so
        counting machine rows as unblock-work lets the brake un-park
        itself. Read-only; consumption stays with ``transition()``."""
        rows = self._goal_state.unread_steering_rows(goal_id)
        return any(
            not str(r["source"] or "").startswith("auto-") for r in rows
        )

    def append_steering(self, goal_id: str, lines: list[str], *, source: str = "denys") -> None:
        """Append steering lines. Writes UNCONSUMED ``goal_steering`` rows
        (the source of truth the planner reads) AND mirrors the same lines
        into ``inbox.md`` in the historical ``- [{source} {ts}] {line}``
        format, so the human-readable and rollback views keep their shape.

        The row stores the SAME formatted line as the file, not the bare
        text: the row's ``line`` is what ``unread_steering`` feeds into the
        worker's advance brief, and evaluator corrections stay "marked
        [auto-eval]" — that marker must survive as part of the line itself.
        The structured ``source`` column exists separately for queries.

        Ordering — ROW first, then the file mirror, the same way
        :meth:`append_log` orders its two writes and the reverse of what this
        method did before #617. The old file-first order existed to protect a
        hand-typed line from being stranded below an advanced ingest cursor:
        with the ingest gone there is no cursor and no re-ingestion, so a
        crash between the two writes must never be able to leave a line in
        ``inbox.md`` that no row backs — that line would be invisible to
        every decision reader while looking, to a human reading the file,
        exactly like real steering. A row without its mirror line is merely a
        cosmetically stale view. Rows are truth, so the row write is the one
        that must not be left dangling.

        The row write also bumps ``goal_status.version`` (see
        :meth:`GoalState.bump_status_version`) so a tick already mid-flight
        past its steering read CAS-fails rather than dispatching without this
        line."""
        clean = [ln.strip() for ln in lines if ln.strip()]
        if not clean:
            return
        ts = self._now().isoformat(timespec="seconds")
        formatted = [f"- [{source} {ts}] {ln}" for ln in clean]
        with self._state.transaction():
            self._goal_state.append_steering_rows(goal_id, formatted, source=source)
            # New steering invalidates any tick snapshot taken before it — see
            # GoalState.bump_status_version. In the SAME transaction as the
            # rows, so a reader can never see the bump without the steering it
            # is warning about.
            self._goal_state.bump_status_version(goal_id)
        d = self._dir(goal_id)
        d.mkdir(parents=True, exist_ok=True)
        path = d / "inbox.md"
        if not path.exists():
            path.write_text(f"# {goal_id} — inbox (steering)\n\n")
        with path.open("a") as fh:
            for ln in formatted:
                fh.write(f"{ln}\n")

    # ---- goal_issue_identity (spec 022 US1) ---------------------------------

    def claim_issue_identity(
        self, project_id: str, issue_key: str, goal_id: str, now_ms: int
    ) -> "tuple[bool, str]":
        """Try to register (project_id, issue_key) → goal_id atomically.

        Returns ``(True, goal_id)`` when this call wins; ``(False, existing_goal_id)``
        when another caller already holds the row. Delegates to the raw SQLite
        method so the PRIMARY KEY constraint is the only enforcement mechanism —
        no read-then-write race.
        """
        return self._goal_state._claim_issue_identity(project_id, issue_key, goal_id, now_ms)

    def rearm_issue_identity(
        self,
        project_id: str,
        issue_key: str,
        old_goal_id: str,
        new_goal_id: str,
        now_ms: int,
    ) -> bool:
        """CAS-replace the identity row when the prior goal is complete.

        Returns True iff this call performed the update (one concurrent re-arm
        wins; others get False and must re-read the winner's goal_id).
        """
        return self._goal_state._rearm_issue_identity(
            project_id, issue_key, old_goal_id, new_goal_id, now_ms
        )

    def lookup_issue_identity(self, project_id: str, issue_key: str) -> "str | None":
        """Return the goal_id registered for (project_id, issue_key), or None."""
        return self._goal_state._lookup_issue_identity(project_id, issue_key)
