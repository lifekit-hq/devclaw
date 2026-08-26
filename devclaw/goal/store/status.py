"""Status — the single-writer / CAS choke point.

:class:`GoalStatusMixin` carries every phase/lifecycle/in_flight write:
``load_status``, the CAS-guarded
:meth:`GoalStatusMixin.transition` (the choke point every production
transition routes through), the column-only ``update_status_fields`` fast
path, ``force_block`` (the illegal-transition escape hatch), and the STATUS.md
view renderer.

Split out of ``GoalStore`` as a mixin on the SAME instance — every method here
runs against the ``self._state`` / ``self._goal_state`` / ``self._now`` /
``self._pending_mirrors`` the base ``GoalStore`` owns, so the transaction /
single-writer / mirror-deferral semantics are byte-identical to the pre-split
monolith.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from typing import Callable

    from ...state_store import StateStore
    from ..state import GoalState

import yaml

from ..models import GoalStatus
from ..state import GoalState
from ..transitions import (
    Event,
    IllegalTransition,
    State,
    TransitionConflict,
    derive_state,
)
from ...state_store import _now_ms


class GoalStatusMixin:
    if TYPE_CHECKING:
        # The composing class owns these (its docstring names the same contract in
        # prose); declared under TYPE_CHECKING so the seam is checked, never run.
        _state: StateStore
        _goal_state: GoalState
        _now: Callable[[], datetime]

        def _write_atomic(self, goal_id: str, name: str, text: str) -> None: ...
        def _flush_or_defer_status_view(self, goal_id: str, status: GoalStatus) -> None: ...

    # ---- status (state) ----------------------------------------------------

    @staticmethod
    def _normalized_blocked_kind(status: GoalStatus) -> str:
        """``blocked_kind`` is only meaningful while ``phase == "blocked"`` —
        any write landing on a non-blocked phase clears it to ``""``. Enforced
        HERE, in the single-writer status layer (applied by every full-row
        write: save_status / transition / force_block), rather than at each
        unblock site: block-lifting paths build their new status via
        ``replace(...)`` of the blocked snapshot (steer_goal's UNBLOCK, the
        planner-family exits from BLOCKED), so a stale kind would silently
        ride along on every one of them. One choke point makes the invariant
        structural. ``update_status_fields`` never touches phase or
        blocked_kind, so the column-only path needs no counterpart."""
        return status.blocked_kind if status.phase == "blocked" else ""

    def load_status(self, goal_id: str) -> GoalStatus:
        # Source of truth is the goal_status table (Tranche 1/PR3), and since
        # #617 the ONLY source: STATUS.md is a generated view and is never
        # parsed back. Any STATUS.md that predates that rule was ingested once
        # by migrate_views_once at construction. A goal with no row yields the
        # default status and writes nothing — its first save_status creates it.
        if self._goal_state.has_status(goal_id):
            return self._goal_state.read_status(goal_id)
        return GoalStatus()

    def record_convergence(self, goal_id: str, outcome: str, workspace_dir: "str | None") -> None:
        """Write the goal's terminal convergence row (spec 018 US1) — call
        AFTER the terminal transition committed, so a CAS-rejected close
        never leaves a phantom row (a crash in between degrades to the
        scorecard's rounds-unknown bucket, never to a wrong count).

        The ONE definition of rounds: lifetime done-gate proposals, counted
        from the append-only phase history's ``verifying`` entries — NOT the
        ``donegate_rounds`` streak counter, which a human steer/resume
        legitimately resets mid-goal."""
        self._goal_state.record_convergence(
            goal_id,
            outcome=outcome,
            rounds=self._goal_state.count_verifying_rounds(goal_id),
            workspace_dir=workspace_dir,
            closed_at=self._now().isoformat(timespec="seconds"),
        )

    def save_status(self, goal_id: str, status: GoalStatus) -> None:
        # Source of truth is the goal_status table; STATUS.md is a generated
        # full-fidelity view rewritten on every save (the rollback path).
        with self._state.transaction():
            # phase_history is append-only. The table is now authoritative, so
            # the old stale-snapshot merge hack (re-reading the disk file) is
            # gone: append a {phase, at} entry only when the phase actually
            # changed from what's stored.
            prev_phase = self._goal_state.current_phase(goal_id)
            if status.phase and status.phase != prev_phase:
                self._goal_state.append_phase_history(
                    goal_id, status.phase, self._now().isoformat(timespec="seconds")
                )
            history = self._goal_state.read_phase_history(goal_id)
            # PR4: stamp the derived enum state on EVERY write so the column
            # can never go stale relative to phase/lifecycle/in_flight. This
            # is still the UNGUARDED write path — no CAS, no legality check
            # (production transition sites use .transition() instead) — but
            # the column itself must always be correct so a later
            # .transition() call has a trustworthy `cur_state` to CAS from.
            status = replace(
                status, phase_history=history, state=derive_state(status).value,
                blocked_kind=self._normalized_blocked_kind(status),
            )
            self._goal_state.write_status(goal_id, status)
        # STATUS.md view — YAML frontmatter + the human body, written via the
        # atomic tmp+os.replace. This is the
        # rollback path: reverting PR3 makes load_status read this file again
        # and recover the current state (a crash mid-write, container restart —
        # 2026-07-09 — left a truncated file that must not orphan in-flight work).
        # Deferred (not written here) when this call is nested inside a
        # caller-opened transaction() — see _flush_or_defer_status_view.
        self._flush_or_defer_status_view(goal_id, status)

    def _load_status_for_cas(self, goal_id: str) -> GoalStatus:
        """The current row as a GoalStatus, or bare defaults when no row
        exists yet — the read side of transition()'s / force_block()'s CAS.
        Deliberately does NOT fall
        back to STATUS.md — a status object built here only ever needs
        `.state`/`.version`, both of which are meaningless on a file that
        predates this table."""
        if self._goal_state.has_status(goal_id):
            return self._goal_state.read_status(goal_id)
        return GoalStatus()

    def transition(
        self, goal_id: str, event: "Event", new: GoalStatus, *, expect: GoalStatus,
        consume_steering: "list[int] | None" = None,
    ) -> GoalStatus:
        """The choke point every PRODUCTION phase/lifecycle/in_flight change
        routes through (see :mod:`devclaw.goal.transitions`). Two guards, in
        order:

        1. **CAS** — the row's CURRENTLY STORED ``(state, version)`` must
           match ``expect``'s (or the fresh defaults, when no row exists yet).
           A mismatch means another writer (steer_goal / cancel_goal / a
           parallel tick) committed between the caller's load and this call;
           raises :class:`~devclaw.goal.transitions.TransitionConflict` and
           writes NOTHING — the caller's decision was based on a snapshot
           that's no longer current, so honoring it would silently clobber
           whatever landed in between (the stale-snapshot un-cancel class this
           PR closes).
        2. **Legality** — ``event`` must permit landing on ``derive_state(new)``
           from the row's CURRENT state per
           :data:`~devclaw.goal.transitions.LEGAL`. A miss raises
           :class:`~devclaw.goal.transitions.IllegalTransition` — always a
           bug, never an expected race.

        Only past both does this write (same shape as save_status: phase_history
        append when phase changed, then write_status, then the STATUS.md view
        AFTER the transaction commits — or DEFERRED, when this call is itself
        nested inside a caller-opened ``transaction()`` (PR7's atomic dispatch/
        settle units): see :meth:`_flush_or_defer_status_view`). Returns the
        ACTUAL stored object (``new`` with ``state``/``version`` stamped) —
        callers MUST thread this forward instead of reusing their pre-call
        snapshot (see tick.py's "version threading rule").

        ``consume_steering`` (PR5): exact ``goal_steering`` row ids to mark
        consumed, INSIDE this same transaction, once past both guards. This
        is what makes "consume exactly the steering rows the planner just
        acted on" atomic with the decision write itself — a
        :class:`TransitionConflict`/:class:`IllegalTransition` raised above
        means this line never runs, so an abandoned tick's steering rides
        the rollback and stays unread (closes "steer-during-planner-await
        lost": the old model consumed by a count stamped AFTER the fact,
        which could sweep up a row the planner never saw).
        """
        # LEGAL is read off the package namespace (not the bare imported name)
        # so a test's ``monkeypatch.setattr(goal.store, "LEGAL", ...)`` — the
        # modeled-missing-edge regression — is honored exactly as it was when
        # ``transition`` lived in the monolith module the test patches.
        from devclaw.goal import store as _store_pkg

        with self._state.transaction():
            fresh = self._load_status_for_cas(goal_id)
            cur_state = State(fresh.state) if fresh.state else derive_state(fresh)
            expect_state = State(expect.state) if expect.state else derive_state(expect)
            if cur_state != expect_state or fresh.version != expect.version:
                raise TransitionConflict(
                    goal_id,
                    expected=(expect_state, expect.version),
                    found=(cur_state, fresh.version),
                )
            target = derive_state(new)
            if target not in _store_pkg.LEGAL.get((cur_state, event), frozenset()):
                raise IllegalTransition(goal_id, cur_state, event, target)
            if consume_steering:
                self._goal_state.consume_steering_rows(goal_id, consume_steering, _now_ms())
            prev_phase = self._goal_state.current_phase(goal_id)
            if new.phase and new.phase != prev_phase:
                self._goal_state.append_phase_history(
                    goal_id, new.phase, self._now().isoformat(timespec="seconds")
                )
            history = self._goal_state.read_phase_history(goal_id)
            written = replace(
                new, phase_history=history, state=target.value, version=fresh.version + 1,
                blocked_kind=self._normalized_blocked_kind(new),
            )
            self._goal_state.write_status(goal_id, written)
            # Observability: record the goal ENTERING a blocked state (deduped)
            # — a block is a problem devclaw hit whether or not it later
            # self-heals. Guarded on cur_state so a goal STAYING blocked (a
            # re-block that lands on the same blocked state) doesn't re-record;
            # the mechanical auto-heal + re-block cycle is thus counted once per
            # genuine entry, never per tick. kind = blocked_kind, message =
            # blocked_on. record_problem is best-effort (never raises), so this
            # is safe inside the transaction. See state_store/problems.py.
            if target is State.BLOCKED and cur_state is not State.BLOCKED:
                self._state.record_problem(
                    category="block",
                    kind=written.blocked_kind or "block",
                    message=written.blocked_on or "",
                    recovered=False,
                    goal_id=goal_id,
                )
        self._flush_or_defer_status_view(goal_id, written)
        return written

    def update_status_fields(self, goal_id: str, **fields) -> GoalStatus:
        """Column-only telemetry update — ``last_tick_at`` / ``last_plan_at`` /
        ``last_progress_at`` / ``no_progress_notified`` / ``last_eval_verdict``
        / ``last_eval_at`` / ``last_eval_note`` /
        ``heal_attempts`` / ``next_heal_at`` ONLY (see
        :data:`GoalState.STATUS_FIELD_COLUMNS`). NEVER a full-row
        rewrite, so it can never be the write that clobbers a concurrent
        phase/lifecycle/in_flight transition — this is the mechanism half of
        the fix .transition()'s CAS is the guard half of: bookkeeping writes
        (last-tick timestamps, eval verdicts) don't need to fight over the row
        at all when they physically cannot touch the columns a transition
        cares about. No CAS, by design — these fields never conflict with a
        concurrent transition.

        Raises ``ValueError`` on any key outside the allowed set (especially
        phase/lifecycle/in_flight/blocked_on/next — those MUST go through
        :meth:`transition`). Falls back to :meth:`save_status` when no row
        exists yet (first write for a goal). Returns the fresh, re-read
        ``GoalStatus``."""
        bad = set(fields) - set(GoalState.STATUS_FIELD_COLUMNS)
        if bad:
            raise ValueError(
                f"update_status_fields: disallowed field(s) {sorted(bad)} — only "
                f"{sorted(GoalState.STATUS_FIELD_COLUMNS)} may go through the "
                "column-only path; phase/lifecycle/in_flight/blocked_on/next "
                "must go through GoalStore.transition()"
            )
        if not self._goal_state.has_status(goal_id):
            self.save_status(goal_id, replace(GoalStatus(), **fields))
            return self.load_status(goal_id)
        with self._state.transaction():
            self._goal_state.update_columns(goal_id, fields)
        fresh = self.load_status(goal_id)
        self._flush_or_defer_status_view(goal_id, fresh)
        return fresh

    def force_block(self, goal_id: str, blocked_on: str) -> bool:
        """Unconditional block write — bypasses the LEGAL-table check on
        purpose. This is the ESCAPE HATCH used ONLY by tick_goal's
        ``IllegalTransition`` catch: BLOCK is legal from every non-terminal
        state, so no matter what a handler was mid-way through when it
        proposed an illegal transition (always a bug, not an expected race —
        see :class:`~devclaw.goal.transitions.IllegalTransition`), the goal
        can always land on BLOCKED and the owner gets a legible ping instead
        of the tick loop crash-retrying forever. Stamps ``blocked_kind="bug"``
        — the one block class that is neither mechanically re-checkable nor
        an owner question (see :class:`~devclaw.goal.models.GoalStatus`).

        Preserves ``in_flight`` AS-IS (same reasoning as
        ``_block_on_corrupt_doc``: blocking stops new cognition, it must not
        orphan a running action). No-op — returns ``False``, writes nothing —
        when the goal is already DONE/CANCELLED (terminal; nothing calls this
        on a happy path, but a belt-and-suspenders guard against blocking a
        finished goal). Returns ``True`` when it wrote."""
        with self._state.transaction():
            fresh = self._load_status_for_cas(goal_id)
            cur_state = State(fresh.state) if fresh.state else derive_state(fresh)
            if cur_state in (State.DONE, State.CANCELLED):
                return False
            new = replace(
                fresh, phase="blocked", lifecycle="executing", blocked_on=blocked_on,
                blocked_kind="bug", next="",
            )
            prev_phase = self._goal_state.current_phase(goal_id)
            if new.phase != prev_phase:
                self._goal_state.append_phase_history(
                    goal_id, new.phase, self._now().isoformat(timespec="seconds")
                )
            history = self._goal_state.read_phase_history(goal_id)
            written = replace(
                new, phase_history=history, state=State.BLOCKED.value, version=fresh.version + 1,
            )
            self._goal_state.write_status(goal_id, written)
            # Observability: the illegal-transition escape hatch is always a
            # blocked-kind="bug" entry — record it (deduped), guarded so a goal
            # already blocked isn't re-recorded. Best-effort; never raises.
            if cur_state is not State.BLOCKED:
                self._state.record_problem(
                    category="block",
                    kind="bug",
                    message=blocked_on or "",
                    recovered=False,
                    goal_id=goal_id,
                )
        self._flush_or_defer_status_view(goal_id, written)
        return True

    def _write_status_view(self, goal_id: str, status: GoalStatus) -> None:
        """Render + atomically write the STATUS.md view for ``status``. Full
        fidelity: same frontmatter shape + body a reader/rollback needs."""
        fm: dict = {
            "phase": status.phase,
            "lifecycle": status.lifecycle,
            "in_flight": (
                {
                    "engine": status.in_flight.engine,
                    "tool": status.in_flight.tool,
                    "id": status.in_flight.id,
                    "ref_kind": status.in_flight.ref_kind,
                    "goal": status.in_flight.goal,
                    "is_done_check": status.in_flight.is_done_check,
                }
                if status.in_flight
                else None
            ),
            "blocked_on": status.blocked_on,
            "blocked_kind": status.blocked_kind,
            "heal_attempts": status.heal_attempts,
            "envcap_redispatches": status.envcap_redispatches,
            "next_heal_at": status.next_heal_at,
            "next": status.next,
            "last_plan_at": status.last_plan_at,
            "last_tick_at": status.last_tick_at,
            "actions_dispatched": status.actions_dispatched,
            "last_eval_verdict": status.last_eval_verdict,
            "last_eval_at": status.last_eval_at,
            "last_eval_note": status.last_eval_note,
            "last_progress_at": status.last_progress_at,
            "no_progress_notified": status.no_progress_notified,
            "phase_history": [dict(e) for e in status.phase_history],
        }
        body = self._render_status_body(goal_id, status)
        text = "---\n" + yaml.safe_dump(fm, sort_keys=False).rstrip() + "\n---\n\n" + body
        self._write_atomic(goal_id, "STATUS.md", text)

    @staticmethod
    def _render_status_body(goal_id: str, s: GoalStatus) -> str:
        if s.phase in ("in_flight", "verifying") and s.in_flight:
            verb = "verifying done via" if s.phase == "verifying" else "running"
            head = f"{verb} `{s.in_flight.tool}` ({s.in_flight.id})"
        elif s.phase == "blocked":
            kind = f" [{s.blocked_kind}]" if s.blocked_kind else ""
            head = f"blocked{kind} — {s.blocked_on}"
        else:
            head = s.phase
        lines = [f"# {goal_id} — status", "", f"**phase:** {head}"]
        if s.next:
            lines.append(f"**next:** {s.next}")
        if s.last_eval_verdict:
            lines.append(f"**direction:** {s.last_eval_verdict} — {s.last_eval_note}")
        if s.last_tick_at:
            lines.append(f"\n_updated {s.last_tick_at}_")
        return "\n".join(lines) + "\n"
