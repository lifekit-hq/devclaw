"""Settle & recover in-flight work — the goal-tick polling resolvers.

Where dispatched work comes back: the atomic settle of a regular action
(delivery row + mergeability advisory), the done-gate poll
resolver, and the once-per-service-start orphaned-ref sweep. This is the top
of the tick_* import graph — it consumes tick_donegate (_resolve_done_gate)
plus tick_guards + tick_context, and is re-exported from tick.py
(tick._tick_goal_impl chains through _resolve_polling_action).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Literal, Tuple, Union

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
from . import repo_brief as _repo_brief
from . import slice_guard as _slice_guard
from .. import project_manifest as _manifest
from .engine import GoalEngine, GoalEngineError
from .models import Goal, GoalStatus, InFlight
from . import problems as _problems
from ..queue.settle import WORKER_BLOCKED_MARKER
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
    assert ref is not None  # phase invariant: settle only runs on an in-flight ref
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
            # last_plan_at=None reopens the re-plan cadence BEFORE the
            # evaluator runs (#784): the goal now sits idle with a close
            # resolution still owed, and if the evaluator dies mid-flight
            # (quota pause, OOM, restart) nothing re-drives it — without this
            # the next planning opportunity is a full cadence away (fs-479
            # lost 24h and its whole project lane to a 24-second-unlucky
            # quota pause). A completed resolution supersedes this: achieved
            # closes the goal, a refusal steers work in, and the next
            # dispatch stamps a fresh last_plan_at either way.
            new_status = ctx.store.transition(
                goal_id, Event.DONE_GATE_SETTLED,
                replace(status, in_flight=None, phase="idle", last_plan_at=None),
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
        autodeploy=ctx.autodeploy, issue_fetcher=ctx.issue_fetcher,
    )


def _readopt_orphaned_ref(
    goal_id: str, status: GoalStatus, store: GoalStore, engine: GoalEngine,
) -> "str | None":
    """Rediscover + re-adopt ONE goal's lost in-flight ref — a TASK this goal
    dispatched whose in-flight ref was lost (STATUS.md
    truncated by a crash mid-write, a restart racing the status write).
    Formerly the per-tick ``_readopt_orphaned_program`` (2026-07-09
    incident); PR7 extends it to tasks and demotes it to a once-per-service-
    start sweep (see :func:`sweep_orphaned_refs`) — atomic dispatch means a
    ref can no longer be lost MID-FLIGHT, so a per-tick check is no longer
    load-bearing; a startup sweep still catches refs lost by an OLDER build,
    or a restart landing in the (now much narrower) commit-to-kick window.
    (The PROGRAM half of the sweep died with the program/DAG lane, spec 022
    US3.)

    "Orphan" = the goal's most recent task by ``parent_goal_id``
    with no recorded settlement (:meth:`GoalStore.is_settled`, PR7's
    replacement for the old ``log_contains(f" {id} → ")`` string match) —
    running OR already-terminal both qualify; the normal POLLING_ACTION path
    then polls/settles it exactly as if the ref had never been lost.
    Engines without a finder (fakes, remote) opt out silently via getattr.

    Returns a short description of what was re-adopted (``"task <id>"``),
    or None if nothing needed re-adopting."""
    task_finder = getattr(engine, "latest_task_for_goal", None)
    if task_finder is not None:
        found_task = task_finder(goal_id)
        if found_task is not None:
            task_id, task_goal, task_kind = found_task
            if not store.is_settled(goal_id, task_id):
                _readopt_ref(store, goal_id, status, ref_id=task_id, ref_kind="task", tool=task_kind, ref_goal=task_goal)
                return f"task {task_id}"
    return None


def _readopt_ref(
    store: GoalStore, goal_id: str, status: GoalStatus,
    *, ref_id: str, ref_kind: 'Literal["task", "program"]', tool: str, ref_goal: str,
) -> None:
    """Write the actual re-adoption: restore ``in_flight`` (DISPATCH_ACTION)
    + a log line, as ONE transaction; mirrors flush after commit. A lost
    done-check ref is deliberately re-adopted as a PLAIN action ref —
    WITHOUT its ``is_done_check`` flag, since that flag lived only on the
    lost ref and cannot be recovered from the task row alone. This
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
    (PR7) — protects against the duplicate-settle loop dogfooded 2026-06-21
    AND closes a PR4-review nuance: a TransitionConflict landing in this
    window now rolls EVERYTHING back (no partial artifacts, no duplicate log
    line), where before only the transition itself was guarded. The mergeability
    probe moves to AFTER the commit — see the comment at that
    call site for the observable-order note. Returns ``(new_status,
    finished_detail)`` so the EXECUTING handler can plan the next action on
    the same tick with the just-finished detail in hand."""
    ref = status.in_flight
    assert ref is not None  # phase invariant: settle only runs on an in-flight ref
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
    # A context-tripwire LANDING is its own settlement outcome (spec
    # tiny/partial-settlement-continuation). The worker hit the context wall
    # having committed a coherent partial increment — with its specs/ artifacts
    # — onto ``goal/<id>``, the branch the next action is placed on. The task
    # itself settled failed-CLOSED and shipped nothing (#186 untouched), but to
    # this layer it is forward progress to CONTINUE from, not a wasted
    # dispatch, and it must never feed forward as "nothing landed".
    settled_status = "partial" if poll.landed_partial else poll.status
    settle_line = f"{ref.tool} {ref.id} → {settled_status}{ev_str}"

    # An empty span is NOT a delivery (spec 013 FR-014). A code-writing action
    # that finished having changed nothing published nothing, so it must not
    # reset the no-progress watchdog — otherwise a goal whose worker keeps
    # accomplishing nothing looks, to every timestamp upstream, exactly like a
    # goal that keeps shipping.
    # NOTE a landed partial is deliberately NOT `delivered`: it publishes no PR
    # and may have committed only re-planning. Leaving the no-progress watchdog
    # armed is what keeps the brake honest — a goal that lands forever without
    # ever shipping still trips it, so refunding the cap above cannot become a
    # licence to loop. No new counter is needed; this one already exists.
    delivered = 1 if (poll.status == "done" and not poll.no_change) else 0
    # Any SUCCESSFUL settle hands back its dispatch-cap budget: the cap exists
    # to stop a planner that spins without producing, not to ration healthy
    # throughput. That includes gateless settles (reviews) — a
    # mission goal that grounds every delivery in a read-only verification
    # review was structurally re-tripping the cap every ~6 cycles while every
    # verdict was on_track (live-found 2026-07-09, closeloop-mission-v2, one
    # night after the #172 refund shipped). Only failures and gate-FAILED work
    # accumulate; churn on successful-but-aimless dispatches is the direction
    # evaluator's and no-progress watchdog's job, not this counter's.
    # A landed partial is productive: the cap exists to stop a goal that spins
    # without producing, and this one produced — it just ran out of window. Not
    # refunding it is what turned a 2-dispatch cap into two guaranteed no-ops
    # (devclaw-030-env-admission-2026-09-01, parked ~20h with a complete
    # tasks.md on its branch). A landing whose span was empty or undeterminable
    # is NOT partial (see engine._landed_partial) and still burns its dispatch.
    productive = 1 if (
        (poll.status == "done" and poll.gate_passed is not False)
        or poll.landed_partial
    ) else 0
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
        # spec 020: a shipped increment proves the environment now fits its
        # workload — the env-cap adapted-re-dispatch budget resets with it.
        envcap_redispatches=(0 if productive else status.envcap_redispatches),
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
            ctx.store.record_settlement(goal_id, ref_id=ref.id, ref_kind=ref.ref_kind, status=settled_status)
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

    # ---- worker honest-block → typed Problem, immediately (spec 031 R4) ----
    # The task layer already failed this settle CLOSED and un-retried; letting
    # the goal re-dispatch an identical block spends sessions rediscovering a
    # known fact and parks two runs later as mechanical:dispatch_cap naming
    # the cap, not the cause. Raise the Problem now, dispatch count untouched.
    if (poll.status == "failed" and WORKER_BLOCKED_MARKER in (poll.detail or "")
            and not poll.landed_partial):
        reason = (poll.detail or "").split(WORKER_BLOCKED_MARKER, 1)[1]
        reason = reason.split(" — the worker reports", 1)[0].strip() or "no reason given"
        prob = _problems.new_problem(
            goal_id, kind="needs_answer", raised_by="worker_block", what=reason,
            clause="", why="the worker reports it cannot complete the task as specified",
            options=_problems.WORKER_BLOCK_OPTIONS, default_key="correct",
        )
        with ctx.store.transaction():
            _problems.raise_problem(ctx.store, prob)
            ctx.store.transition(
                goal_id, Event.BLOCK,
                replace(new_status, phase="blocked", blocked_on=_problems.summary_line(prob),
                        blocked_kind="needs_answer", problem_id=prob.id, next=""),
                expect=new_status,
            )
        ctx.store.append_log(goal_id, f"worker block → problem {prob.id}")
        # Spec 031 T044: if the block names a capability the admission lint
        # should have refused, record the MISS so the class gets fixed, not
        # the instance (constitution VII). Never wedges the settle.
        try:
            from . import admission_lint as _lint
            if _lint.lint_mechanical(reason).refused:
                ctx.store.record_problem(
                    category="admission", kind="lint_miss",
                    message=f"worker block named a sandbox-impossible capability the lint admitted: {reason[:200]}",
                    recovered=False, goal_id=goal_id,
                )
        except Exception:  # noqa: BLE001
            pass
        await _notify(ctx.notifier, NotifyLevel.OWNER,
                      f"🟡 [{goal_id}] {_problems.render_for_human(prob)}")
        return Outcome.BLOCKED

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
    # the goal for a re-slice. Scoped to a delivered increment (poll ``done``);
    # every goal accumulates on a goal branch, so there is no topology to check
    # (the per-action exemption went with per-action itself, #641). Runs only on
    # the POLLING_ACTION settle path (never idle/blocked), so the
    # ``FakeClaude.calls == 0`` guards stay green. The fail-closed-under-strict consequence is unchanged.
    if poll.status == "done":
        flips = await asyncio.to_thread(
            _slice_guard.tasks_flips_sync, goal.workspace_dir
        )
        if flips > 1:
            # Spec 016 FR-008: the slice dial rides the LIVE resolved
            # strictness (explicit goal > manifest default at the merged
            # base). Resolution runs only on this rare tripped path — never
            # idle — and a malformed base manifest fails LOUD (strict-side).
            try:
                _resolved_strictness = await asyncio.to_thread(
                    _manifest.resolve_goal_strictness, goal
                )
            except _manifest.ManifestError:
                _resolved_strictness = "strict"
            note = (
                f"slice guardrail: this increment advanced {flips} story-slices "
                f"in one delivery — a coherent increment advances ONE story-slice "
                f"([US<n>]) and ships as one reviewable PR, not a build-ahead "
                f"through later stories"
            )
            if gate_consequence("slice", _resolved_strictness) is Consequence.BLOCK:
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

    # ---- post-commit tail: mergeability probe (the one real await) --------
    # Nothing merges here, and that is the design rather than an omission
    # (#641). Every goal delivers on a shared goal branch as ONE cumulative PR
    # which must stay OPEN for the done-gate: merging it mid-flight deletes the
    # branch and forces the next night to re-fork from main, wiping the
    # accumulated work (#486). Auto-merge and the program PR-stack reconciler
    # that used to run here were deleted — they served a per-action topology
    # nothing has selected since the spec 008 shrink, and in companion mode the
    # human reviews and merges. Do not reintroduce a merge on this path.
    #
    # This runs STRICTLY AFTER the settle has committed (PR7). A crash between
    # the two leaves the task settled with its probe unrun — the SAFE direction:
    # before PR7 a crash here lost the settle too and the whole thing re-ran.

    # ---- mergeability probe (#394) -----------------------------------------
    # A delivery whose PR is CONFLICTING at settle is a degraded delivery and
    # must be loud — otherwise it settles `done` indistinguishably from a
    # landable one (both live 2026-07-28 instances: closeloop-bench PR #8
    # accumulated three gate-green deliveries on a branch that structurally
    # conflicted with main after PR #7's squash-merge; fs PR #329 was
    # CONFLICTING at open and reported success anyway). One cheap gh read per
    # settled PR; best-effort — an unknown verdict (probe None, gh hiccup)
    # stays silent rather than crying wolf.
    conflicting: "bool | None" = None
    if ctx.mergeability_probe is not None and poll.status == "done" and poll.pr_url:
        conflicting = await ctx.mergeability_probe(poll.pr_url)
        if conflicting:
            ctx.store.append_log(
                goal_id, f"PR is CONFLICTING with its base — cannot land as-is: {poll.pr_url}"
            )
            await _notify(
                ctx.notifier, NotifyLevel.OWNER,
                f"⚠️ [{goal_id}] delivered PR cannot land — {_action_label(ref)} "
                f"shipped, but its PR conflicts with the base branch and will not "
                f"merge as-is. It needs a rebase or hand-resolution: {poll.pr_url}",
                summarize=ctx.summary_caller,
            )

    # Tells the planner the PR's REAL state instead of letting it infer one.
    # The closeloop-bench 2026-07-05 fix: the planner's done-proposal prose
    # claimed "PR merged (gate passed)" for a PR nothing had merged, because
    # the detail string never said otherwise.
    pr_state = ""
    if poll.pr_url:
        pr_state = " pr_state=open (unmerged — owner review pending)"
        if conflicting:
            # Ground the planner in the PR's real landability, not just its
            # openness — a conflicting PR must not read as shippable work.
            pr_state += (
                " · mergeable=CONFLICTING — cannot land without a rebase/"
                "conflict resolution"
            )

    finished_detail = f"tool={ref.tool} id={ref.id} status={poll.status}{ev_str}{pr_state}\n{poll.detail}"

    return new_status, finished_detail
