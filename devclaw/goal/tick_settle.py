"""Settle & recover in-flight work — the goal-tick polling resolvers.

Where dispatched work comes back: the atomic settle of a regular action
(delivery row + auto-merge / goal-branch reconcile), the done-gate poll
resolver, and the once-per-service-start orphaned-ref sweep. This is the top
of the tick_* import graph — it consumes tick_donegate (_resolve_done_gate)
plus tick_guards + tick_context, and is re-exported from tick.py
(tick._tick_goal_impl chains through _resolve_polling_action).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Tuple, Union

from .tick_context import (
    NotifyLevel,
    Outcome,
    Phase,
    TickContext,
    _action_label,
    _classify,
    _notify,
)
from .tick_guards import _block_on_lost_ref, _block_on_prep_failure
from .tick_donegate import _resolve_done_gate
from . import delivery_strategy as _delivery
from . import reconcile as _reconcile
from . import repo_brief as _repo_brief
from . import slice_guard as _slice_guard
from .engine import GoalEngine, GoalEngineError
from .models import Goal, GoalStatus, InFlight
from .store import GoalStore
from .transitions import Event
from ..loom import trace as _trace
from ..engine.workspace import WorkspaceError
from ..quality.gate_policy import Consequence, gate_consequence
from ..state_store import derive_failure_class


async def _resolve_polling_done_gate(
    goal_id: str, goal: Goal, status: GoalStatus, ctx: TickContext,
) -> Outcome:
    """Settle an in-flight done-gate review. Still running → IN_FLIGHT. Else
    record the review outcome, clear in_flight, and judge the repo against
    ``done_when`` via :func:`_resolve_done_gate` (PR7 "light settle" shape:
    settlement + log + transition as one unit; mirrors flush after commit)."""
    ref = status.in_flight
    try:
        poll = await ctx.engine.poll(ref)
    except GoalEngineError as exc:
        return await _block_on_lost_ref(goal_id, status, exc, ctx)
    if poll.running:
        ctx.store.update_status_fields(goal_id, last_tick_at=ctx.store.now_iso())
        return Outcome.IN_FLIGHT
    review_report = poll.detail or f"review {poll.status} (no report captured)"
    try:
        with ctx.store.transaction():
            ctx.store.record_settlement(goal_id, ref_id=ref.id, ref_kind=ref.ref_kind, status=poll.status)
            ctx.store.append_log(goal_id, f"done-check review {ref.id} → {poll.status}", mirror=False)
            new_status = ctx.store.transition(
                goal_id, Event.DONE_GATE_SETTLED,
                replace(status, in_flight=None, phase="idle"),
                expect=status,
            )
    except Exception:
        ctx.store.discard_pending_mirrors(goal_id)
        raise
    ctx.store.render_mirrors(goal_id)
    return await _resolve_done_gate(
        goal_id, goal, new_status, review_report,
        store=ctx.store, evaluator_caller=ctx.evaluator_caller, notifier=ctx.notifier,
        summarize=ctx.summary_caller, remote_checker=ctx.remote_checker,
        autodeploy=ctx.autodeploy,
    )


def _readopt_orphaned_ref(
    goal_id: str, status: GoalStatus, store: GoalStore, engine: GoalEngine,
) -> "str | None":
    """Rediscover + re-adopt ONE goal's lost in-flight ref — a TASK or a
    PROGRAM this goal dispatched whose in-flight ref was lost (STATUS.md
    truncated by a crash mid-write, a restart racing the status write).
    Formerly the per-tick ``_readopt_orphaned_program`` (2026-07-09
    incident); PR7 extends it to tasks and demotes it to a once-per-service-
    start sweep (see :func:`sweep_orphaned_refs`) — atomic dispatch means a
    ref can no longer be lost MID-FLIGHT, so a per-tick check is no longer
    load-bearing; a startup sweep still catches refs lost by an OLDER build,
    or a restart landing in the (now much narrower) commit-to-kick window.

    "Orphan" = the goal's most recent task/program by ``parent_goal_id``
    with no recorded settlement (:meth:`GoalStore.is_settled`, PR7's
    replacement for the old ``log_contains(f" {id} → ")`` string match) —
    running OR already-terminal both qualify; the normal POLLING_ACTION path
    then polls/settles it exactly as if the ref had never been lost.

    Checks the TASK finder first, then the PROGRAM finder: in a healthy
    system ``in_flight`` is a single slot, so a goal essentially never has
    BOTH an orphaned task and an orphaned program at once. When it
    theoretically does, task-first is a pragmatic simplification — comparing
    ``created_at`` precisely would need both finders to expose it, for a
    benefit that in practice never matters (the brief this PR implements
    sanctions this choice explicitly). Engines without a finder (fakes,
    remote) opt out silently via getattr, same as the pre-PR7 program-only
    version.

    Returns a short description of what was re-adopted (``"task <id>"`` /
    ``"program <id>"``), or None if nothing needed re-adopting."""
    task_finder = getattr(engine, "latest_task_for_goal", None)
    if task_finder is not None:
        found_task = task_finder(goal_id)
        if found_task is not None:
            task_id, task_goal, task_kind = found_task
            if not store.is_settled(goal_id, task_id):
                _readopt_ref(store, goal_id, status, ref_id=task_id, ref_kind="task", tool=task_kind, ref_goal=task_goal)
                return f"task {task_id}"
    program_finder = getattr(engine, "latest_program_for_goal", None)
    if program_finder is not None:
        found_program = program_finder(goal_id)
        if found_program is not None:
            program_id, program_goal = found_program
            if not store.is_settled(goal_id, program_id):
                _readopt_ref(store, goal_id, status, ref_id=program_id, ref_kind="program", tool="start_program", ref_goal=program_goal)
                return f"program {program_id}"
    return None


def _readopt_ref(
    store: GoalStore, goal_id: str, status: GoalStatus,
    *, ref_id: str, ref_kind: str, tool: str, ref_goal: str,
) -> None:
    """Write the actual re-adoption: restore ``in_flight`` (DISPATCH_ACTION)
    + a log line, as ONE transaction; mirrors flush after commit. A lost
    done-check ref is deliberately re-adopted as a PLAIN action ref —
    WITHOUT its ``is_done_check`` flag, since that flag lived only on the
    lost ref and cannot be recovered from the task/program row alone. This
    is conservative by construction: the settle just records a delivery
    (instead of re-entering the done-gate resolution path directly), and
    the worker naturally re-proposes done on its own next advance if
    warranted."""
    ref = InFlight("devclaw", tool, ref_id, ref_kind, ref_goal)
    try:
        with store.transaction():
            store.transition(
                goal_id, Event.DISPATCH_ACTION,
                replace(status, in_flight=ref, phase="in_flight"),
                expect=status,
            )
            store.append_log(
                goal_id,
                f"re-adopted orphaned {ref_kind} {ref_id} — its in-flight ref was "
                "missing from STATUS.md (lost state, e.g. a restart mid-write); "
                "settling it now instead of waiting on a result that would never arrive",
                mirror=False,
            )
    except Exception:
        store.discard_pending_mirrors(goal_id)
        raise
    store.render_mirrors(goal_id)


async def sweep_orphaned_refs(store: GoalStore, engine: GoalEngine) -> "dict[str, str]":
    """Once-per-service-start sweep: for every goal, re-adopt a lost
    in-flight ref if one is found (see :func:`_readopt_orphaned_ref`).
    Returns ``{goal_id: description}`` for every goal that was re-adopted —
    empty when nothing needed it.

    Guard mirrors the condition the OLD per-tick readopt effectively had:
    EXECUTING classification (terminal/investigating/firming goals, and any
    goal that already has a fresh in_flight ref, are skipped) with no ref.
    A single goal's bad state (a corrupt status row, a raised finder) is
    isolated — logged where possible, never allowed to sink the whole sweep,
    matching ``tick_all``'s per-goal isolation.

    Does NOT take :func:`_tick_lock` (PR8): this runs once, before the
    heartbeat loop starts (see ``GoalService._loop``) — single-threaded at
    that point, nothing else can be ticking any goal yet, so there is no
    same-goal concurrency for the lock to guard against here."""
    result: "dict[str, str]" = {}
    for goal_id in store.list_goal_ids():
        try:
            status = store.load_status(goal_id)
            if status.in_flight is not None:
                continue
            if _classify(status) is not Phase.EXECUTING:
                continue
            outcome = _readopt_orphaned_ref(goal_id, status, store, engine)
        except Exception as exc:  # noqa: BLE001 — one goal's trouble must not sink the sweep
            try:
                store.append_log(goal_id, f"startup sweep error (isolated): {exc}")
            except Exception:  # noqa: BLE001 — even the log write must not propagate
                pass
            continue
        if outcome:
            result[goal_id] = outcome
    return result


async def _resolve_polling_action(
    goal_id: str, goal: Goal, status: GoalStatus, ctx: TickContext,
) -> "Union[Outcome, Tuple[GoalStatus, str]]":
    """Settle an in-flight regular action. Still running → IN_FLIGHT.
    Otherwise: record the delivery (grounded evidence for the evaluator),
    update the no-progress watchdog, and commit the settlement + delivery +
    log + checklist rows + the ACTION_SETTLED transition as ONE transaction
    (PR7) — protects against the duplicate-merge loop dogfooded 2026-06-21
    AND closes a PR4-review nuance: a TransitionConflict landing in this
    window now rolls EVERYTHING back (no partial artifacts, no duplicate log
    line), where before only the transition itself was guarded. Auto-merge /
    program-reconcile move to AFTER the commit — see the comment at that
    call site for the observable-order note. Returns ``(new_status,
    finished_detail)`` so the EXECUTING handler can plan the next action on
    the same tick with the just-finished detail in hand."""
    ref = status.in_flight
    try:
        poll = await ctx.engine.poll(ref)
    except GoalEngineError as exc:
        return await _block_on_lost_ref(goal_id, status, exc, ctx)
    if poll.running:
        ctx.store.update_status_fields(goal_id, last_tick_at=ctx.store.now_iso())
        return Outcome.IN_FLIGHT

    # ---- compute everything the settle transaction will write, BEFORE ------
    # ---- opening it (no cognition, no I/O below — pure computation) --------
    evidence = []
    if poll.pr_url:
        evidence.append(f"PR {poll.pr_url}")
    if poll.gate_passed is not None:
        # Say WHICH gate: devclaw's sandbox verify_cmd, not the target repo's
        # CI. The bare "gate=passed" wording let the closeloop-bench 2026-07-05
        # planner treat sandbox-green as CI-green while every real GitHub
        # Actions run was failing at startup.
        evidence.append("sandbox gate=passed" if poll.gate_passed else "sandbox gate=FAILED")
    ev_str = (" — " + ", ".join(evidence)) if evidence else ""
    settle_line = f"{ref.tool} {ref.id} → {poll.status}{ev_str}"

    delivered = 1 if poll.status == "done" else 0
    # Any SUCCESSFUL settle hands back its dispatch-cap budget: the cap exists
    # to stop a planner that spins without producing, not to ration healthy
    # throughput. That includes gateless settles (reviews, programs) — a
    # mission goal that grounds every delivery in a read-only verification
    # review was structurally re-tripping the cap every ~6 cycles while every
    # verdict was on_track (live-found 2026-07-09, closeloop-mission-v2, one
    # night after the #172 refund shipped). Only failures and gate-FAILED work
    # accumulate; churn on successful-but-aimless dispatches is the direction
    # evaluator's and no-progress watchdog's job, not this counter's.
    productive = 1 if (poll.status == "done" and poll.gate_passed is not False) else 0
    new_status = replace(
        status, in_flight=None, phase="idle",
        actions_dispatched=max(0, status.actions_dispatched - productive),
        # A productive settle also earns the mechanical auto-heal budget back
        # (tick_guards._autoheal_corrupt_doc) — the SAME stability signal as
        # the cap refund above, riding the same ACTION_SETTLED write (no extra
        # write, atomic with the settle): a goal that ships real work again is
        # stable, so a later mechanical block starts with a fresh heal budget
        # instead of a stale flap count from a long-resolved incident.
        heal_attempts=(0 if productive else status.heal_attempts),
        # a delivery is forward progress → reset the no-progress watchdog.
        last_progress_at=(ctx.store.now_iso() if delivered else status.last_progress_at),
        no_progress_notified=(False if delivered else status.no_progress_notified),
    )

    # ---- the atomic settle ---------------------------------------------
    # settlement row + delivery row + log row + checklist update + the
    # ACTION_SETTLED transition, as ONE unit. A TransitionConflict here rolls
    # ALL of it back — settlement, delivery, log, checklist — so the retry
    # tick re-settles this same terminal ref identically: no partial
    # artifacts, no duplicate log line. ref_id=ref.id on record_settlement +
    # append_delivery is the idempotency key (PR6/PR7): a retry re-running
    # this settle for the same ref is a no-op INSERT, not a duplicate.
    try:
        with ctx.store.transaction():
            ctx.store.record_settlement(goal_id, ref_id=ref.id, ref_kind=ref.ref_kind, status=poll.status)
            ctx.store.append_delivery(goal_id, ref.goal or ref.tool, poll.detail or "", ref_id=ref.id, mirror=False)
            ctx.store.append_log(goal_id, settle_line, mirror=False)
            # Persist IMMEDIATELY (within this same atomic unit) — the
            # next-action planner can raise on a usage limit; if the cleared
            # state isn't durable first the tick aborts with in_flight still
            # pointing at the just-finished action and the next tick
            # re-ships it (duplicate-merge loop, dogfood 2026-06-21). Thread
            # the RETURNED (fresh-versioned) status onward — _handle_long_lived_advance's
            # `expect=` calls CAS against THIS version, not the pre-settle
            # snapshot.
            new_status = ctx.store.transition(goal_id, Event.ACTION_SETTLED, new_status, expect=status)
    except Exception:
        ctx.store.discard_pending_mirrors(goal_id)
        raise
    ctx.store.render_mirrors(goal_id)

    # Repo-scoped worker brief writeback (MC borrow item 3): fold the worker's
    # REPO NOTES hand-back into this repo's accumulated brief so FUTURE goals
    # on the same workspace start informed. Plain line-dedupe merge, zero LLM.
    # Deliberately OUTSIDE the settle transaction and best-effort: the brief is
    # cross-goal telemetry-grade context — a hiccup here must never re-settle
    # or wedge the goal. Idempotent by construction (duplicate lines drop), so
    # a settle retry re-applying the same notes is a no-op.
    if poll.repo_notes:
        try:
            scope = _repo_brief.scope_key_for(goal.workspace_dir)
            if scope:
                merged = _repo_brief.merge_repo_notes(
                    ctx.store.read_repo_brief(scope), poll.repo_notes
                )
                ctx.store.write_repo_brief(scope, merged)
        except Exception:  # noqa: BLE001 — notes are hints, never a settle gate
            pass

    _trace.record_delivery(
        goal_id=goal_id, action_label=_action_label(ref),
        gate_passed=poll.gate_passed, pr_url=poll.pr_url or "",
        diff_stats=poll.diff_stats,
    )

    # ---- mechanical-setup failure → damped mechanical:prep breaker (#379) ---
    # A toolchain-not-provisioned / git clone-fetch-clean / target-branch-prep
    # failure is something the worker CANNOT fix by re-running the same
    # instruction. Left as a plain settled-``failed`` it re-dispatches every
    # tick — the finance-sentry-ui goal re-hit the identical trust/prep failure
    # 119× purely because it wore a ``subprocess``/``engine_error`` label
    # instead of ``mechanical:prep``. Route it to the SAME host-side prep block
    # used at dispatch time so the existing damped auto-heal engages
    # (PREP_HEAL_CAP + persisted backoff, then a human park via _autoheal_prep)
    # instead of an amnesiac storm. This blocks the WHOLE goal — a setup failure
    # is not an item-specific defect. Fail-CLOSED (the settle above already
    # committed, so the ref can't re-adopt) and zero-token (pure mechanical
    # bucketing + one owner ping). Rides a SEPARATE transition CAS'd against the
    # just-settled ``new_status``, exactly like the item breaker below; checked
    # BEFORE the item breaker so a mechanical failure takes the DAMPED
    # (auto-healing) path, not the ``needs_answer`` human park.
    if poll.status == "failed" and derive_failure_class(poll.detail) == "mechanical_setup":
        return await _block_on_prep_failure(
            goal_id, new_status,
            WorkspaceError(poll.detail or "mechanical setup failure"),
            store=ctx.store, notifier=ctx.notifier, summarize=ctx.summary_caller,
        )

    # ---- speckit tasks.md build-ahead guardrail (SDLC pipeline) -------------
    # A well-sliced nightly increment advances ONE story-slice; an increment that
    # advances >1 ``specs/*/tasks.md`` story-slice (a ``[US<n>]`` whose tasks it
    # checks off) built ahead into later stories instead of shipping one
    # reviewable slice (the "Ledger" 17k-line-PR class). The UNIT is the
    # story-slice, NOT the raw checkbox — closing five ``T00x [US1]`` rows is one
    # slice, not five (see :func:`slice_guard.count_slice_advances`). Detection is
    # a pure git-diff + string parse — ZERO token and best-effort/fail-OPEN: an
    # absent or garbled tasks.md ⇒ 0 ⇒ never trips (:mod:`slice_guard`; a repo
    # with no speckit contract has no build-ahead unit to police). The
    # VERDICT rides the EXISTING strictness dial (:func:`gate_consequence`, a
    # dial-able "slice" gate): under ``trust`` it ADVISES (loud log, ship anyway —
    # the done-gate + human review are the backstop), under ``strict`` it BLOCKS
    # the goal for a re-slice. Scoped to a delivered increment (poll ``done``)
    # whose topology is a goal branch — the per-action topology has no
    # accumulating plan to reason about, and this runs only on the POLLING_ACTION
    # settle path (never idle/blocked), so the ``FakeClaude.calls == 0`` guards
    # stay green. The fail-closed-under-strict consequence is unchanged.
    if (
        poll.status == "done"
        and _delivery.resolve_strategy(ctx.store, goal_id).goal_branch(goal_id) is not None
    ):
        flips = await asyncio.to_thread(
            _slice_guard.tasks_flips_sync, goal.workspace_dir
        )
        if flips > 1:
            note = (
                f"slice guardrail: this increment advanced {flips} story-slices "
                f"in one delivery — a coherent increment advances ONE story-slice "
                f"([US<n>]) and ships as one reviewable PR, not a build-ahead "
                f"through later stories"
            )
            if gate_consequence("slice", goal.strictness) is Consequence.BLOCK:
                reason = (
                    note + ". Parked for a re-slice (strict mode): steer a smaller "
                    "next step (one story-slice), or set trust to ship-and-advise."
                )
                ctx.store.transition(
                    goal_id, Event.BLOCK,
                    replace(new_status, phase="blocked", blocked_on=reason,
                            blocked_kind="needs_answer"),
                    expect=new_status,
                )
                ctx.store.append_log(goal_id, reason)
                await _notify(
                    ctx.notifier, NotifyLevel.OWNER, f"🛑 [{goal_id}] {reason}",
                    summarize=ctx.summary_caller,
                )
                return Outcome.BLOCKED
            # ADVISE — loud in the goal log, ship anyway (trust mode). The
            # done-gate's grounded review + the human merge are the backstop.
            ctx.store.append_log(goal_id, "⚠️ " + note + " — shipped anyway (trust mode)")

    # ---- post-commit tail: auto-merge / program-reconcile (real awaits) ----
    # Moved here (from before the status write, pre-PR7) so both now run
    # STRICTLY AFTER the settle has committed — shrinking the 2026-06-21
    # duplicate-merge window and killing the remaining conflict-retry
    # artifacts. Two observable differences from pre-PR7, both intentional:
    # (a) the "auto-merged …" / "reconcile: …" log lines now land AFTER the
    # settle line in log.md (same content, slightly different file order);
    # (b) a crash after commit but before the merge attempt now leaves the
    # task settled-but-unmerged (a PR left for review — the SAFE direction:
    # pre-PR7, a crash there lost the settle too and the whole thing re-ran).
    #
    # Hands-off auto-merge: a delivered change whose verify gate passed is
    # merged by devclaw itself, with a plain owner ping. Best-effort + gated —
    # a failed merge just leaves the PR for review.
    #
    # ``ctx.merger`` being non-None IS the enabled decision — GoalService
    # resolves it per-goal (project automerge override, else the devclaw-wide
    # DEVCLAW_GOAL_AUTOMERGE default; see devclaw.goal.merge.resolve_automerge)
    # BEFORE it ever reaches this tick. This function must not re-check the
    # raw global flag itself — doing so would mean a project's explicit
    # override could never turn merging ON when the fleet-wide default is off
    # (or off when the default is on), defeating the whole point of a
    # per-project override.
    #
    # #394 totality: every gate-green delivery that produced a PR resolves to
    # exactly ONE of merged | left-for-owner(reason) | skipped(reason), and the
    # resolution is always visible in the goal log. The two skip paths below
    # used to fall through in silence — a goal whose skip path never said so
    # forced the owner to infer "it silently didn't engage" from an open PR
    # and an empty log.
    # #486: the auto-merge SKIP keys on the delivery TOPOLOGY. A goal-branch
    # delivery's PR is the cumulative goal-branch PR — auto-merging it
    # mid-flight deletes the goal branch and forces the next night to re-fork
    # from main, wiping the accumulated PR. The cumulative PR stays OPEN for
    # the done-gate; only a per-action (``goal_branch(...) is None``) delivery
    # auto-merges per action.
    on_goal_branch = (
        _delivery.resolve_strategy(ctx.store, goal_id).goal_branch(goal_id) is not None
    )
    merged_now = False
    merge_failed_pinged = False  # an OWNER ping already fired for this PR this settle
    merge_skip = ""  # non-empty ⇒ this green delivery's PR was deliberately not merged
    green_pr = bool(poll.status == "done" and poll.gate_passed and poll.pr_url)
    if green_pr and on_goal_branch:
        merge_skip = "goal-branch: cumulative PR stays open for the done-gate"
    elif green_pr and ctx.merger is None:
        merge_skip = "auto-merge is off for this repo"
    elif green_pr:
        if await ctx.merger(poll.pr_url):
            merged_now = True
            ctx.store.append_log(goal_id, f"auto-merged {poll.pr_url}")
            await _notify(
                ctx.notifier, NotifyLevel.TASK,
                f"✅ [{goal_id}] shipped + merged — {_action_label(ref)} ({poll.pr_url})",
                summarize=ctx.summary_caller,
            )
        else:
            merge_failed_pinged = True
            ctx.store.append_log(goal_id, f"auto-merge failed, left for review: {poll.pr_url}")
            # Loud, not silent (2026-07-17): automerge is ENABLED for this goal
            # but the merge did not land (failing/pending checks, a conflict, a gh
            # hiccup). The best-effort merger swallows the reason and returns
            # False, so WITHOUT this the owner never learns it was attempted — and
            # is later paged to "please merge PR X" as if nothing tried (the
            # finance-sentry "automerge never fired" confusion, 2026-07-17). A PR
            # that needs a manual merge IS a needs-you event → OWNER altitude.
            await _notify(
                ctx.notifier, NotifyLevel.OWNER,
                f"⚠️ [{goal_id}] auto-merge failed — {_action_label(ref)} shipped "
                f"but its PR did not merge automatically (check its CI/mergeability). "
                f"Please merge it by hand: {poll.pr_url}",
                summarize=ctx.summary_caller,
            )
    if merge_skip:
        # skipped(reason) — one legible line, log-only (no ping: a skip is
        # configured behavior, not a needs-you event; the conflict probe below
        # is what escalates when the skipped PR also cannot land).
        ctx.store.append_log(goal_id, f"auto-merge skipped ({merge_skip}): {poll.pr_url}")

    # #430: remember a green PR we shipped but did NOT land (auto-merge
    # off/failed), so the done-gate can tell it is reviewing a ref (the default
    # branch) that cannot see the fix — and block for a merge instead of
    # re-dispatching the same work forever. Cleared the moment such a PR
    # merges. A column-only write AFTER the atomic settle (the merge attempt
    # above is async, outside the transaction) — and its returned
    # fresh-versioned status is threaded onward so the advance handler's
    # `expect=` still CAS's against the current version. The done-gate consumer
    # of this marker (tick_donegate) is guarded on ``goal_branch(...) is
    # None``, so a goal-branch PR that lands here sets a marker the goal-branch
    # done-gate never reads — benign.
    if green_pr:
        new_status = ctx.store.update_status_fields(
            goal_id, open_unmerged_pr=(None if merged_now else poll.pr_url)
        )

    # ---- mergeability probe (#394) -----------------------------------------
    # A delivery whose PR is CONFLICTING at settle is a degraded delivery and
    # must be loud — today it settles `done` indistinguishably from a landable
    # one (both live 2026-07-28 instances: closeloop-bench PR #8 accumulated
    # three gate-green deliveries on a branch that structurally conflicted
    # with main after PR #7's squash-merge; fs PR #329 was CONFLICTING at
    # open and reported success anyway). One cheap gh read per settled PR
    # that is still open; best-effort — an unknown verdict (probe None, gh
    # hiccup) stays silent rather than crying wolf. Programs are excluded:
    # their PR stacks were just reconciled above, per-PR, with reasons.
    conflicting: "bool | None" = None
    if (
        ctx.mergeability_probe is not None
        and poll.status == "done" and poll.pr_url and not merged_now
        and ref.ref_kind != "program"
    ):
        conflicting = await ctx.mergeability_probe(poll.pr_url)
        if conflicting:
            ctx.store.append_log(
                goal_id, f"PR is CONFLICTING with its base — cannot land as-is: {poll.pr_url}"
            )
            # One page per event: the failed-merge branch above already sent an
            # OWNER ping for this same PR ("merge it by hand") — the conflict
            # fact still lands in the log + planner detail, but a second page
            # for the same delivery would just be noise.
            if not merge_failed_pinged:
                await _notify(
                    ctx.notifier, NotifyLevel.OWNER,
                    f"⚠️ [{goal_id}] delivered PR cannot land — {_action_label(ref)} "
                    f"shipped, but its PR conflicts with the base branch and will not "
                    f"merge as-is. It needs a rebase or hand-resolution: {poll.pr_url}",
                    summarize=ctx.summary_caller,
                )

    # Program settle: a finished program leaves a STACK of PRs the single-PR
    # auto-merge above can't touch (no single gate verdict). Reconcile the
    # stack mechanically — close superseded, merge green in order, leave red
    # with a reason — so the goal stops burning follow-up dispatches
    # shepherding its own PRs to main and stops leaving zombies behind
    # (live-found 2026-07-09: five open superseded closeloop PRs). Same
    # merger gate as auto-merge: no merger resolved → owner reviews by hand.
    reconcile_summary: list[str] = []
    if (
        ctx.merger is not None
        and ref.ref_kind == "program" and poll.status == "done" and poll.pr_url
    ):
        stack = [u.strip() for u in poll.pr_url.split(";") if u.strip()]
        reconcile_summary = await _reconcile.reconcile_stack(
            stack, workspace_dir=goal.workspace_dir, merger=ctx.merger,
        )
        for line in reconcile_summary:
            ctx.store.append_log(goal_id, f"reconcile: {line}")

    # Built AFTER the auto-merge attempt so the planner is told the PR's real
    # state instead of inferring it. "open (unmerged — owner review pending)"
    # is the closeloop-bench 2026-07-05 fix: the planner's done-proposal prose
    # claimed "PR merged (gate passed)" for a PR nothing had merged, because
    # the detail string never said otherwise.
    pr_state = ""
    if reconcile_summary:
        pr_state = " pr_stack reconciled:\n" + "\n".join(f"  - {line}" for line in reconcile_summary)
    elif poll.pr_url:
        if merged_now:
            pr_state = " pr_state=merged"
        else:
            pr_state = " pr_state=open (unmerged — owner review pending)"
            if merge_skip:
                pr_state += f" · auto-merge skipped ({merge_skip})"
            if conflicting:
                # Ground the planner in the PR's real landability, not just its
                # openness — a conflicting PR must not read as shippable work.
                pr_state += (
                    " · mergeable=CONFLICTING — cannot land without a rebase/"
                    "conflict resolution"
                )
    finished_detail = f"tool={ref.tool} id={ref.id} status={poll.status}{ev_str}{pr_state}\n{poll.detail}"

    return new_status, finished_detail
