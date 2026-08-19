"""The goal heartbeat — one wakeup.

Folded in from goalclaw, extended with grounded direction evaluation. Order is
load-bearing: the cheap, deterministic, ZERO-TOKEN check runs first and
short-circuits when there's nothing to do. Cognition (plan + evaluate) runs ONLY
past that gate. This is the quota guardrail — N idle ticks must cost ~0 tokens,
or the Pro weekly quota dies (burned this way 2026-05-18).

The evaluation tiers (mechanism gates cognition):
  1. progress check          — Python, every tick, 0 tokens (poll in-flight)
  2. per-delivery evidence    — in-proc, 0 tokens (write the grounded deliveries.md)
     (the per-tick direction eval that used to sit here was removed — demolition
      P1, docs/proposals/cognition-demolition.md; direction is judged only at the
      done-gate now, backed by the mechanical no-progress watchdog mid-flight.)
  3. done-gate                — the planner's "done" is a proposal; it triggers a
                                read-only review whose report the evaluator judges;
                                only "achieved" actually closes the goal.

Everything is injected (store, engine, planner/evaluator callers, notifier,
prepare_ws) so a whole tick runs deterministically under test — no network, no
claude — and the quota assertion is just "FakeClaude.calls == 0" on idle paths.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable

from . import merge as _merge
from . import remote_checks as _remote_checks
from . import triage as _triage
# _deploy stays at tick.py level even though only tick_donegate._auto_deploy calls
# it: tests monkeypatch ``devclaw.goal.tick._deploy.deploy_project`` and both
# modules bind the SAME ..delivery.deploy module object, so patching it here is
# what makes the deploy stub visible to the moved _auto_deploy.
from ..advance_brief import ADVANCE_BRIEF_MARKER, FAILURE_CONTEXT_MARKER, STEERING_MARKER
from ..delivery import deploy as _deploy  # noqa: F401 (re-export/monkeypatch anchor)
from .engine import GoalEngine
from .models import Action, Goal, GoalStatus
from .notify import Notifier
from ..llm_call import ClaudeCaller
from .store import GoalStore
from .transitions import Event, IllegalTransition, TransitionConflict
from ..loom import trace as _trace
from ..loom.limits import FailureKind, classify_failure, pause_seconds
from ..state_store import _now_ms
from ..engine.workspace import prepare_workspace

# ---- extracted-module re-export facade (behavior-preserving split) --------
# Every symbol MOVED out of this file is re-exported here so
# ``devclaw.goal.tick.<name>`` (and ~20 test imports / monkeypatch targets)
# resolve exactly as before. Import graph stays acyclic:
# tick_context <- tick_guards <- {tick_dispatch, tick_donegate} <- tick_settle.
from .tick_context import (  # noqa: F401 (re-exported)
    AUTODEPLOY_ENABLED,
    EVAL_EVERY,
    NO_PROGRESS_S,
    VERIFY_DONE,
    NotifyLevel,
    Outcome,
    Phase,
    TickContext,
    WorkspacePrep,
    _ALTITUDES,
    _action_label,
    _classify,
    _engine_kick,
    _notify,
    _notify_floor,
    _run_atomic,
    _TICK_LOCKS,
    _tick_lock,
    triaged_notify,
)
from .tick_guards import (  # noqa: F401 (re-exported)
    PREP_HEAL_CAP,
    _autoheal_prep,
    _block_on_lost_ref,
    _block_on_prep_failure,
    _check_no_progress,
    _progress_window_active,
)
from .tick_donegate import (  # noqa: F401 (re-exported)
    _auto_deploy,
    _done_gate_review_brief,
    _open_done_gate,
    _project_owns_its_deploy,
    _resolve_done_gate,
)
from .tick_dispatch import (  # noqa: F401 (re-exported)
    _dispatch_action,
)
from .tick_settle import (  # noqa: F401 (re-exported)
    _readopt_orphaned_ref,
    _readopt_ref,
    _resolve_polling_action,
    _resolve_polling_done_gate,
    sweep_orphaned_refs,
)


async def tick_goal(
    goal_id: str,
    *,
    store: GoalStore,
    engine: GoalEngine,
    evaluator_caller: ClaudeCaller,
    notifier: Notifier,
    notify_url: str = "",
    prepare_ws: WorkspacePrep = prepare_workspace,
    eval_every: int = EVAL_EVERY,
    verify_done: bool = VERIFY_DONE,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    no_progress_s: int = NO_PROGRESS_S,
    summary_caller: "ClaudeCaller | None" = None,
    merger: "_merge.Merger | None" = None,
    trend_detector: "object | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    mergeability_probe: "_merge.MergeabilityProbe | None" = None,
) -> Outcome:
    """Run one heartbeat and record a single ``tick`` trace event with the
    incoming (lifecycle, phase) and outgoing outcome — the only place the trace
    sees a tick. All the cognition / dispatch / delivery / notify events fired
    during the body land between this tick and the next.

    ``trend_detector`` (typed as ``object`` to avoid an import cycle with
    ``devclaw.trend_detector``): when set, runs per-project trend signals after
    the tick body settles. Telemetry-shaped: a detector exception NEVER breaks
    the tick — it is recorded as a note and swallowed.

    The ENTIRE body runs under this goal's :func:`_tick_lock` (PR8) — a
    concurrent tick for the SAME goal (tick_one racing tick_all's sweep) waits
    here instead of both running cognition and one losing its round to a
    TransitionConflict. See the lock's own comment for the full rationale.
    Different goals use different Lock objects, so this never serializes the
    fleet — only same-goal overlap."""
    async with _tick_lock(goal_id):
        status_before = store.load_status(goal_id)
        phase_before = _classify(status_before)
        lifecycle_before = status_before.lifecycle or "executing"
        try:
            outcome = await _tick_goal_impl(
                goal_id,
                store=store, engine=engine,
                evaluator_caller=evaluator_caller,
                notifier=notifier, notify_url=notify_url, prepare_ws=prepare_ws,
                eval_every=eval_every, verify_done=verify_done, autodeploy=autodeploy,
                no_progress_s=no_progress_s,
                summary_caller=summary_caller, merger=merger,
                remote_checker=remote_checker,
                mergeability_probe=mergeability_probe,
            )
        except IllegalTransition as exc:
            # A handler proposed an (event, target) the LEGAL table doesn't permit
            # from the goal's CURRENT stored state — always a bug (the handler
            # computed the wrong event, or LEGAL is missing a real code path),
            # never an expected race (see TransitionConflict below for that). Force
            # -block rather than let the tick loop crash-retry the same bug every
            # heartbeat — loud failure over silent degradation (CLAUDE.md's
            # hardening philosophy: verification fails closed, corruption blocks
            # legibly, and this is the state-machine's version of the same rule).
            store.append_log(goal_id, f"ILLEGAL transition — blocking: {exc}")
            store.force_block(goal_id, f"illegal state transition: {exc}")
            await _notify(
                notifier, NotifyLevel.OWNER,
                f"🟥 [{goal_id}] internal state error — I've paused this goal; steer to resume: {exc}",
                summarize=summary_caller,
            )
            outcome = Outcome.BLOCKED
        except TransitionConflict as exc:
            # Expected, not a bug: another writer (steer_goal / cancel_goal,
            # typically) committed between this tick's load and its write. The
            # tick's write is simply abandoned — nothing from this turn was
            # persisted — and the NEXT tick reads the fresh state instead of
            # clobbering it (the stale-snapshot un-cancel class this PR closes:
            # today, without this catch, the tick's stale write would silently
            # win and un-cancel the goal). Zero notify — benign and self-healing,
            # a notification here would just be tick-cadence noise. Note: the
            # PR8 lock makes a tick_one-vs-tick_all conflict on the SAME goal
            # unreachable (they now serialize); this catch remains load-bearing
            # for steer_goal/cancel_goal, which stay lock-free by design.
            store.append_log(goal_id, f"tick abandoned — state changed mid-tick: {exc}")
            outcome = Outcome.CONFLICT
        if trend_detector is not None:
            try:
                # Volume hygiene (2026-07-15): a terminal goal gets no trend
                # sweep — production showed ~350 trend_check rows per goal per
                # night across 17 goals of which 15 were cancelled/done. The
                # skip lives HERE (where the sweep selects goals), not inside
                # the detector; re-read the status so a goal that went terminal
                # DURING this very tick (done-gate closed it, cancel raced in)
                # is skipped too. Cheap SQLite read — zero LLM either way.
                if store.load_status(goal_id).phase not in ("done", "cancelled"):
                    goal = store.load_goal(goal_id)
                    await trend_detector.run_per_goal(
                        goal_id=goal_id, workspace_dir=goal.workspace_dir,
                    )
            except Exception as exc:  # noqa: BLE001 — telemetry must not break ticks
                _trace.record_note(
                    f"trend_detector.run_per_goal failed for {goal_id}: "
                    f"{exc.__class__.__name__}: {exc}"
                )
        _trace.record_tick(
            goal_id=goal_id, lifecycle=lifecycle_before,
            phase=phase_before.value, outcome=outcome.value,
        )
        return outcome


async def _tick_goal_impl(
    goal_id: str,
    *,
    store: GoalStore,
    engine: GoalEngine,
    evaluator_caller: ClaudeCaller,
    notifier: Notifier,
    notify_url: str = "",
    prepare_ws: WorkspacePrep = prepare_workspace,
    eval_every: int = EVAL_EVERY,
    verify_done: bool = VERIFY_DONE,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    no_progress_s: int = NO_PROGRESS_S,
    summary_caller: "ClaudeCaller | None" = None,
    merger: "_merge.Merger | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    mergeability_probe: "_merge.MergeabilityProbe | None" = None,
) -> Outcome:
    """Run one heartbeat. Reads the goal's status, classifies it into a
    :class:`Phase`, dispatches to the matching handler.

    Two design pillars carried over from the original implementation:
      * **Terminal short-circuit** runs BEFORE the no-progress watchdog so done /
        cancelled goals don't even read the clock.
      * **Action-poll chains into EXECUTING** in the same tick — a settled
        regular action records its delivery, clears ``in_flight``, and the
        planner sees the just-finished detail without waiting another heartbeat.
        (Discovery / done-gate polls do NOT chain — they have dedicated
        resolution handlers.)
    """
    ctx = TickContext(
        store=store, engine=engine,
        evaluator_caller=evaluator_caller,
        notifier=notifier, notify_url=notify_url, prepare_ws=prepare_ws,
        eval_every=eval_every, verify_done=verify_done, autodeploy=autodeploy,
        no_progress_s=no_progress_s,
        summary_caller=summary_caller, merger=merger,
        remote_checker=remote_checker,
        mergeability_probe=mergeability_probe,
    )

    status = store.load_status(goal_id)
    phase = _classify(status)

    # Terminal short-circuit — skip even the watchdog: a done/cancelled goal
    # must keep skipping at zero cost (including the legacy-lifecycle heal
    # below — a cancelled pre-shrink row never earns a write).
    if phase is Phase.TERMINAL_DONE:
        return Outcome.SKIP_DONE
    if phase is Phase.TERMINAL_CANCELLED:
        return Outcome.SKIP_CANCELLED

    # Legacy-lifecycle heal (spec 008 shrink): a pre-shrink row still carrying
    # ``investigating``/``firming`` names a phase that no longer exists. Heal
    # it LOUDLY to executing, once — zero cognition, one log line; the goal
    # then rides the ordinary advance path.
    if status.lifecycle in ("investigating", "firming"):
        store.append_log(
            goal_id,
            f"legacy lifecycle {status.lifecycle!r} healed to 'executing' — the "
            "investigation/firming phases were removed (spec 008 shrink); the "
            "worker plans via speckit",
        )
        # Column-scoped + self-guarding (WHERE lifecycle IN (...)): never a
        # whole-row write, so a concurrent steer/cancel cannot be clobbered —
        # see GoalStore.heal_legacy_lifecycle.
        store.heal_legacy_lifecycle(goal_id)
        status = store.load_status(goal_id)
        phase = _classify(status)

    # The goal contract is goal.yaml alone now — the firmed.yaml overlay (and
    # the checklist.yaml corrupt-doc probe) died with the host-cognition chain
    # (spec 008 shrink): the worker's speckit artifacts live in the repo, and
    # the store's goal docs (log/deliveries/inbox/spec) parse trivially.
    goal = store.load_goal(goal_id)

    # Mechanical auto-heal (F8): lift a mechanical:* block whose condition no
    # longer holds — no LLM, ever (the mirror of the quota pause's
    # timestamp-compare auto-resume in tick_all), damped by the persisted
    # per-goal heal budget so a flapping condition can't turn the zero-token
    # blocked steady-state into a plan + ping per cycle. One healable kind
    # remains: ``prep`` — its recheck costs a git subprocess (ls-remote), so it
    # runs on the persisted next_heal_at exponential backoff, not every tick.
    # (``mechanical:corrupt_doc`` died with its contract files — a legacy row
    # still blocked on it stays human-gated: resume_goal clears it.)
    # needs_answer / bug / lost_ref / dispatch_cap stay human-gated (see the
    # heal guards' docstrings). A refused heal (budget spent / window closed /
    # still broken) leaves the blocked status untouched and the tick idles
    # below at zero cognition, same as any blocked tick.
    if status.phase == "blocked":
        healed = None
        if status.blocked_kind == "mechanical:prep":
            healed = await _autoheal_prep(
                goal_id, goal, status, store=store, notifier=notifier,
            )
        if healed is not None:
            status = healed
            phase = _classify(status)

    # Zero-token no-progress watchdog: pure timestamp math; fires one owner ping
    # if an executing goal hasn't shipped in too long. Mutates status; never
    # transitions phase.
    status = await _check_no_progress(
        goal_id, goal, status,
        store=store, notifier=notifier, window_s=no_progress_s, summarize=summary_caller,
    )

    # Polling phases — settle in-flight work first.
    if phase is Phase.POLLING_DONE_GATE:
        return await _resolve_polling_done_gate(goal_id, goal, status, ctx)

    # Orphaned-ref reconcile used to run HERE, every tick (2026-07-09:
    # closeloop-mission-v2 waited all night on a program that had already
    # failed, because STATUS.md was truncated by a crash mid-write). PR7's
    # atomic dispatch (task/program row + the DISPATCH transition + the log
    # row as ONE unit) makes that class of loss structurally impossible on
    # the in-process engine going forward — the in_flight ref can no longer
    # commit without the row it points at (and vice versa). The remaining
    # recovery surface — a goal whose ref was lost by an OLDER, pre-PR7
    # build, or by something outside the dispatch path entirely (manual DB
    # surgery, a cross-environment restore) — is handled ONCE per service
    # start by sweep_orphaned_refs, not every tick — see GoalService._loop().

    finished_detail = ""
    if phase is Phase.POLLING_ACTION:
        outcome = await _resolve_polling_action(goal_id, goal, status, ctx)
        if isinstance(outcome, Outcome):
            return outcome
        # A regular action settled; chain to the lifecycle phase that the just-
        # cleared status now classifies into (usually EXECUTING).
        status, finished_detail = outcome
        phase = _classify(status)

    # Lifecycle phase (in_flight is None).
    if phase is Phase.EXECUTING:
        return await _handle_executing(goal_id, goal, status, finished_detail, ctx)

    raise RuntimeError(f"unhandled phase {phase} for goal {goal_id}")



# ---- phase handlers --------------------------------------------------------
# One handler per Phase value. Each takes (goal_id, goal, status, ctx) — except
# the polling handlers, which the orchestrator calls with status already loaded
# — and returns either an :class:`Outcome` (terminal for this tick) or, for
# ``_resolve_polling_action``, an ``(updated_status, finished_detail)`` tuple
# so the EXECUTING handler can chain on the same tick.


def _advance_brief(goal: Goal, steering: str, failure_context: str = "") -> str:
    """The light pull-brief for a thin-path advance session (demolition P3;
    speckit substrate, spec 008 US1).

    Deliberately thin (§3a trust-the-input): the worker PULLS its context — the
    speckit ``specs/*/`` artifacts + the repo's ``.specify/`` scripts + the repo
    itself — the way a briefed subagent explores, rather than being handed a
    pre-chewed dossier. This says only WHAT to pursue and to advance it by one
    story-slice via the speckit flow. Model-agnostic (Principle II): plain
    imperative text referencing the ``.specify/`` bash scripts, never Claude-Code
    slash-command wiring. Steering (an owner input, or the done-gate's own
    corrections re-applied via ``_apply_corrections``) rides in here for the
    worker to read — never applied by a planner, because there isn't one."""
    parts = [
        # Built from the shared marker so the detectors (delivery's title/body
        # guard, tick_dispatch's display choke point) can never drift from the
        # generator (#547/#550).
        ADVANCE_BRIEF_MARKER
        + ", shippable increment using speckit, then stop.",
        "Find the CURRENT feature: the smallest not-yet-complete specs/NNN-*/ "
        "(its tasks.md still has unchecked items). If none exists and new work is "
        "called for, create one with .specify/scripts/bash/create-new-feature.sh.",
        "Run the speckit steps for that feature — specify, then plan, then tasks, "
        "then implement — using the repo's .specify/ scripts and templates. "
        "Implement only the SMALLEST not-yet-done story-slice (one coherent slice "
        "= one reviewable PR); do NOT build ahead into later stories.",
        "Check off the completed items in tasks.md and commit the specs/NNN-*/ "
        "artifacts together with the code.",
        "",
        f"Goal: {goal.objective}",
    ]
    if goal.done_when.strip():
        parts += ["", f"Done when: {goal.done_when.strip()}"]
    if failure_context.strip():
        parts += [
            "",
            FAILURE_CONTEXT_MARKER + " (failed task or failed gate) — its "
            "terminal reason, verbatim. Read it and ADAPT this session (a "
            "context overflow or timeout means: take a strictly smaller "
            "slice); do not repeat the attempt unchanged:",
            failure_context.strip()[:800],
        ]
    if steering.strip():
        parts += ["", STEERING_MARKER, steering.strip()]
    return "\n".join(parts)


async def _handle_long_lived_advance(
    goal_id: str, goal: Goal, status: GoalStatus, finished_detail: str, ctx: TickContext,
) -> Outcome:
    """The long_lived executing path — ZERO per-tick planner cognition
    (the planner was cut, demolition P3b). The worker owns the
    plan (the speckit ``specs/*/`` artifacts in the repo); the control plane only
    dispatches "advance the goal via speckit" and lets the grounded done-gate
    judge done:

      * a SUCCESSFUL advance session just settled → propose done. The done-gate
        verifies against ``done_when``: ``achieved`` closes the goal; not-achieved
        re-applies its own corrections as steering and returns the goal to idle,
        so the NEXT cadence advances again with those corrections in hand — a
        ralph-loop, not a re-review-every-tick spin (the done-gate only fires
        after a real session settles, never on an idle tick);
      * otherwise (a fresh cadence tick, or a FAILED/gate-failed settle) → gate
        on work-present/cadence (the zero-token idle guard — a blocked goal
        unblocks only on work, never the timer) and dispatch ONE advance session.

    The done-TRIGGER is the worker's own session-success header — devclaw's
    controlled ``status=done`` settle line (the same header the planner used to
    read), NOT the worker's free-text self-report. It is a cheap trigger for the
    expensive grounded gate, never a substitute for it: the worker's done-claim
    is never trusted on faith (#358); the grounded done-gate is the authority."""
    store = ctx.store
    # A successful advance settled → propose done; the grounded done-gate decides.
    # Read ONLY devclaw's controlled settle header — its FIRST line,
    # "tool=… id=… status=…{gate/PR}" (tick_settle._resolve_polling_action). The
    # worker's free-text narration follows the newline and must NOT be scanned:
    # otherwise a worker could flip the control-plane's done-decision by writing
    # "status=done" / "gate=FAILED" into its own summary — the exact #358 trust
    # boundary this trigger exists to respect (never trust the worker's claim on
    # faith; the grounded gate is the authority, the header is only a cheap
    # trigger). invariant-guard reproduced the free-text crack, 2026-08-05.
    header = finished_detail.split("\n", 1)[0] if finished_detail else ""
    settled_ok = "status=done" in header and "gate=FAILED" not in header
    if settled_ok:
        now = store.now_iso()
        base = replace(status, last_plan_at=now, last_tick_at=now)
        store.append_log(goal_id, "thin: advance session settled — proposing done")
        return await _open_done_gate(
            goal_id, goal, base,
            store=store, engine=ctx.engine, evaluator_caller=ctx.evaluator_caller,
            notifier=ctx.notifier, notify_url=ctx.notify_url, prepare_ws=ctx.prepare_ws,
            verify_done=ctx.verify_done, note="thin: advance session settled",
            summarize=ctx.summary_caller, remote_checker=ctx.remote_checker,
            autodeploy=ctx.autodeploy,
        )

    # Steering + should_plan gate — mirrors the planner path's gate exactly so
    # the zero-token idle guard is preserved: a blocked goal unblocks only on
    # work, an idle goal plans only on work or a due cadence.
    rows = store.unread_steering_rows(goal_id)
    steering = "\n".join(line for _, line in rows)
    # unread_steering_rows() may have lazily ingested inbox lines, bumping
    # version; reload so the dispatch's expect= CAS's against the current row
    # (same reason as _handle_executing).
    status = store.load_status(goal_id)
    work = bool(finished_detail) or bool(steering)
    if status.phase == "blocked":
        should_plan = work
    else:
        should_plan = work or store.cadence_due(goal, status)
    if not should_plan:
        store.update_status_fields(goal_id, last_tick_at=store.now_iso())
        return Outcome.IDLE

    consume_ids = [rid for rid, _ in rows]
    now = store.now_iso()
    base = replace(status, last_plan_at=now, last_tick_at=now)
    # A non-ok settle reaching this dispatch (failed task, or done-with-
    # failed-gate) carries its terminal reason in finished_detail — thread it
    # into the brief so the next session ADAPTS instead of re-running blind
    # (the reason used to be collapsed to bool(finished_detail): the 3h
    # context-overflow and the 1h wall-clock burns of 2026-08-19 were each
    # followed by a byte-identical brief).
    failure_context = finished_detail if finished_detail else ""
    action = Action(
        engine="devclaw",
        tool="implement_feature",
        goal=_advance_brief(goal, steering, failure_context=failure_context),
        verify_cmd=goal.verify_cmd,
        open_pr=goal.open_pr,
    )
    return await _dispatch_action(
        goal_id, goal, base, action,
        store=store, engine=ctx.engine, notifier=ctx.notifier,
        notify_url=ctx.notify_url, prepare_ws=ctx.prepare_ws,
        summarize=ctx.summary_caller, consume_steering=consume_ids,
    )


async def _handle_executing(
    goal_id: str, goal: Goal, status: GoalStatus, finished_detail: str, ctx: TickContext,
) -> Outcome:
    """ONE execution path for both modes (spec 008 shrink — the checklist-as-
    program one_shot branch died with the host-cognition chain): every
    executing goal advances via the speckit pull-brief. The mode dial is back
    to selecting only the re-evaluation cadence (ADR 0003): a one_shot goal
    rides the same advance loop — its first advance fires immediately (no
    ``last_plan_at`` yet ⇒ cadence due) and the done-gate's corrections chain
    work-present advances until achieved, so it still drives to done without
    waiting out the cadence."""
    return await _handle_long_lived_advance(goal_id, goal, status, finished_detail, ctx)



# ---- multi-goal driver -----------------------------------------------------


async def tick_all(
    *,
    store: GoalStore,
    engine: GoalEngine,
    evaluator_caller: ClaudeCaller,
    notifier: Notifier,
    notify_url: str = "",
    prepare_ws: WorkspacePrep = prepare_workspace,
    eval_every: int = EVAL_EVERY,
    verify_done: bool = VERIFY_DONE,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    no_progress_s: int = NO_PROGRESS_S,
    summary_caller: "ClaudeCaller | None" = None,
    merger: "_merge.Merger | None" = None,
    merger_resolver: "Callable[[Goal], _merge.Merger | None] | None" = None,
    verify_done_resolver: "Callable[[Goal], bool] | None" = None,
    autodeploy_resolver: "Callable[[Goal], bool | None] | None" = None,
    tracer_factory: "Callable[[str], _trace.Tracer | None] | None" = None,
    trend_detector: "object | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    triage_caller: "ClaudeCaller | None" = None,
    mergeability_probe: "_merge.MergeabilityProbe | None" = None,
) -> dict[str, Outcome]:
    """Tick every goal. One goal's failure never stops the others, and a usage
    limit pauses the whole layer (0 tokens) rather than crashing per-goal.

    ``tracer_factory(goal_id) -> Tracer | None`` is the seam GoalService uses
    to attach a :class:`PersistentTracer` per goal-tick so the cascade's
    cognition / dispatch / delivery events land in the durable trace store.

    ``merger_resolver``, when given, computes automerge FRESH per goal (a
    project's automerge override must not leak from one goal onto another in
    the same sweep) and takes precedence over the flat ``merger``. Plain
    ``merger`` stays supported for callers (and existing tests) with a single
    fleet-wide value. ``verify_done_resolver`` and ``autodeploy_resolver`` are
    the same idea for the done-gate re-check flag and the on-complete deploy
    flag: fresh per goal, each taking precedence over its flat counterpart.

    ``trend_detector`` (typed as ``object`` to avoid the import cycle with
    ``devclaw.trend_detector``): when set, runs per-project signals inside each
    per-goal tracer scope, and runs harness-self signals once after the loop
    inside a sentinel-keyed (``_harness_self_``) tracer scope. Telemetry-shaped
    catches: a detector exception NEVER breaks the heartbeat.
    """
    outcomes: dict[str, Outcome] = {}

    # Unified quota pause: the OAuth quota is account-wide, so if anything (a task
    # or earlier goal cognition) paused dispatch, skip ALL goal cognition until it
    # lifts — zero tokens while paused. Auto-clear + resume once it expires.
    until, reason = _engine_pause(engine)
    if until and _now_ms() < until:
        # Tell the owner ONCE per pause (a weekly cap can halt everything for
        # days — silence here looks like devclaw died). The goal layer owns the
        # Notifier, so the ping lives here and covers pauses set by EITHER
        # layer (task queue or goal cognition). The persisted flag is what
        # keeps this to one ping, not one per tick.
        if not _engine_pause_notified(engine):
            resume_hhmm = datetime.fromtimestamp(
                until / 1000, tz=timezone.utc
            ).strftime("%H:%M")
            if reason.startswith(FailureKind.AUTH.value):
                # An auth pause is ACTIONABLE, not weather: waiting won't fix a
                # broken login, a human re-login will (2026-07-20 night: the old
                # REAL classification burned the whole run window in silent
                # terminal failures). Say what to do; the fixed re-probe both
                # auto-resumes after the fix and re-pings while still broken.
                msg = (
                    f"🔑 paused — Claude auth/login failure ({reason}). Waiting "
                    f"won't fix this: re-login on the devclaw host (`claude` → "
                    f"/login). Work auto-resumes on the next probe ~{resume_hhmm} "
                    f"UTC; I'll ping again if it's still broken."
                )
            else:
                msg = f"⏸️ paused on a usage limit — {reason}; resuming ~{resume_hhmm} UTC"
            await _notify(notifier, NotifyLevel.OWNER, msg, summarize=summary_caller)
            kind = (
                FailureKind.AUTH.value
                if reason.startswith(FailureKind.AUTH.value) else "limit"
            )
            _engine_set_pause_notified(engine, True, kind=kind)
        return {gid: Outcome.RATE_LIMITED for gid in store.list_goal_ids()}
    if until:
        _engine_clear_pause(engine)
    # Resume ping — the counterpart of the pause ping above, once per pause.
    # Checked whenever no pause is ACTIVE (not only on the expiry tick that
    # cleared it): the task queue lazily clears an expired pause too, and the
    # owner must still hear the resume in that race. An AUTH episode is the
    # exception: its pause expiring means "re-probe now", not "the limit
    # lifted" — announcing a resume would be a lie while the login may still be
    # broken. The auth check keys on the kind PERSISTED with the ping (the
    # queue's 10s pump wipes the live pause_reason first on the dominant
    # ordering — invariant-guard find, 2026-07-21), with the live reason as a
    # fallback for engines predating the kind accessor. Skip the ping but still
    # clear the flag: if the probe re-trips auth, the fresh pause re-pings (the
    # periodic still-broken reminder); if the login was fixed, work just
    # resumes and the next delivery speaks for itself.
    if _engine_pause_notified(engine):
        auth_episode = (
            _engine_pause_notified_kind(engine) == FailureKind.AUTH.value
            or bool(until and reason.startswith(FailureKind.AUTH.value))
        )
        if not auth_episode:
            await _notify(
                notifier, NotifyLevel.OWNER,
                "▶️ usage limit lifted — resuming work",
                summarize=summary_caller,
            )
        _engine_set_pause_notified(engine, False)

    # Operator controls: a manual pause toggle or a daily run-window can hold ALL
    # goal cognition (0 tokens) the same way the quota pause does. Tasks already
    # dispatched finish; nothing new is planned while gated. Re-checked every tick.
    blocked, _why = _engine_operator_block(engine)
    if blocked:
        return {gid: Outcome.RATE_LIMITED for gid in store.list_goal_ids()}

    # Retention (volume hygiene): AFTER the cheap gates above, BEFORE any
    # per-goal work — daily, batched, pure-SQLite DELETEs of the two
    # highest-volume append-only logs past their retention windows: traces
    # (DEVCLAW_TRACE_RETENTION_DAYS, 2026-07-15) and events (raw runner SDK
    # events, DEVCLAW_EVENTS_RETENTION_DAYS, 2026-07-18). Zero LLM calls, so the
    # zero-token idle guarantee is untouched; StateStore owns the actual writes
    # (single-writer invariant), the engine is just the seam.
    _engine_prune_traces(engine)
    _engine_prune_events(engine)
    # Reclaim the disk those DELETEs free — a weekly, freelist-gated VACUUM
    # (SQLite reuses freed pages but never shrinks the .db file on its own).
    # Same cheap-path slot, same zero-LLM guarantee.
    _engine_vacuum(engine)
    # Loud-not-silent DB-size alarm: if the .db has grown past the threshold
    # despite retention+VACUUM, ping the owner ONCE (re-armed when it drops back
    # under) — a silent disk-fill wedge is the failure mode this whole tranche
    # exists to prevent. Zero LLM (raw owner ping, no summarizer).
    await _maybe_alert_db_size(engine, notifier, triage_caller=triage_caller)

    for goal_id in store.list_goal_ids():
        # Per-goal run-window: a goal can carry its OWN night/off-hours schedule
        # on top of the engine-wide gate above (e.g. a token-heavy standing loop
        # confined to nights while other goals run all day). Outside its window,
        # skip just this goal — 0 tokens for it — while the others still tick.
        g_blocked, _gwhy = _engine_goal_operator_block(engine, goal_id)
        if g_blocked:
            outcomes[goal_id] = Outcome.RATE_LIMITED
            continue
        tracer = tracer_factory(goal_id) if tracer_factory else None
        goal_merger = merger
        goal_verify_done = verify_done
        goal_autodeploy = autodeploy
        # Load the goal once for whichever per-goal resolvers are wired (a bad
        # goal.yaml must not sink the sweep — fall back to the flat values).
        if any(r is not None for r in (merger_resolver, verify_done_resolver, autodeploy_resolver)):
            try:
                _g = store.load_goal(goal_id)
                if merger_resolver is not None:
                    goal_merger = merger_resolver(_g)
                if verify_done_resolver is not None:
                    goal_verify_done = verify_done_resolver(_g)
                if autodeploy_resolver is not None:
                    goal_autodeploy = autodeploy_resolver(_g)
            except Exception:  # noqa: BLE001 — a bad goal.yaml must not sink the sweep
                goal_merger, goal_verify_done, goal_autodeploy = merger, verify_done, autodeploy
        try:
            with _trace.tracer_scope(tracer):
                outcomes[goal_id] = await tick_goal(
                    goal_id, store=store, engine=engine,
                    evaluator_caller=evaluator_caller,
                    notifier=notifier, notify_url=notify_url, prepare_ws=prepare_ws,
                    eval_every=eval_every, verify_done=goal_verify_done,
                    autodeploy=goal_autodeploy, no_progress_s=no_progress_s,
                    summary_caller=summary_caller, merger=goal_merger,
                    trend_detector=trend_detector,
                    remote_checker=remote_checker,
                    mergeability_probe=mergeability_probe,
                )
        except Exception as exc:  # noqa: BLE001 — isolate per-goal blast radius
            # the goal's OWN cognition (claude --print) hitting a limit pauses the
            # whole layer instead of crash-looping + burning quota; anything else is
            # logged with its real cause (never a blind 'crashed') and isolated.
            paused = _maybe_pause(engine, store, goal_id, str(exc))
            if paused is not None:
                outcomes[goal_id] = paused
            else:
                store.append_log(goal_id, f"tick error (isolated): {str(exc)[:160]}")
                outcomes[goal_id] = Outcome.ERROR

    # Harness-self trend pass — runs ONCE per heartbeat after the per-goal loop.
    # Sentinel goal_id keeps the trace events in the same table for replay via
    # get_trace; the detector observes devclaw itself, not any specific goal.
    if trend_detector is not None:
        harness_tracer = (
            tracer_factory("_harness_self_") if tracer_factory else None
        )
        try:
            with _trace.tracer_scope(harness_tracer):
                await trend_detector.run_harness_self()
        except Exception:  # noqa: BLE001 — telemetry must not break the heartbeat
            pass

    return outcomes


def _engine_pause(engine: GoalEngine) -> tuple[int, str]:
    """Read the shared quota pause via the engine, if it exposes one (the
    in-process engine does; test doubles may not → treated as no pause)."""
    fn = getattr(engine, "global_pause", None)
    return fn() if callable(fn) else (0, "")


def _engine_prune_traces(engine: GoalEngine) -> None:
    """Run the daily trace-retention prune via the engine, if it exposes one
    (the in-process engine does; test doubles may not → no prune). Best-effort:
    a maintenance failure must never break the heartbeat — the traces table
    just stays bigger until a later tick succeeds."""
    fn = getattr(engine, "prune_traces", None)
    if not callable(fn):
        return
    try:
        fn()
    except Exception:  # noqa: BLE001 — maintenance must not break the heartbeat
        pass


def _engine_prune_events(engine: GoalEngine) -> None:
    """Run the daily events-retention prune via the engine, if it exposes one
    (the in-process engine does; test doubles may not → no prune). Best-effort:
    a maintenance failure must never break the heartbeat — the events table
    just stays bigger until a later tick succeeds."""
    fn = getattr(engine, "prune_events", None)
    if not callable(fn):
        return
    try:
        fn()
    except Exception:  # noqa: BLE001 — maintenance must not break the heartbeat
        pass


def _engine_vacuum(engine: GoalEngine) -> None:
    """Run the weekly, freelist-gated VACUUM via the engine, if it exposes one
    (the in-process engine does; test doubles may not → no vacuum). Best-effort:
    a maintenance failure must never break the heartbeat — the .db just stays at
    its current size until a later tick reclaims it."""
    fn = getattr(engine, "vacuum", None)
    if not callable(fn):
        return
    try:
        fn()
    except Exception:  # noqa: BLE001 — maintenance must not break the heartbeat
        pass


async def _maybe_alert_db_size(
    engine: GoalEngine, notifier: Notifier, *, triage_caller: "ClaudeCaller | None" = None,
) -> None:
    """Check the DB-size alarm via the engine and, if it just crossed the
    threshold, ping the owner ONCE. Best-effort on both legs: a stat failure or
    a notifier outage must never break the heartbeat.

    Zero-token idle guard: ``check_db_size_alert`` returns a message ONLY on the
    tick the .db crosses the threshold (deduped by the ``db_size_alerted`` meta
    flag). On every idle / under-threshold tick it returns ``None`` and this
    function returns before any cognition — so the guarantee holds regardless of
    whether triage is wired.

    When ``triage_caller`` is set (production, via GoalService), the alert routes
    through the propose-only self-triage interceptor (:func:`triaged_notify`,
    ``kind="db_size"``): it dedupes against the ``problems`` catalog and proposes
    a grounded retention fix, delivering "problem + proposed fix + how to
    approve" instead of the bare alert. ``triage_caller=None`` (the default, and
    every existing test) keeps the RAW owner send, byte-identical to before. A
    triage failure falls back to the raw alert — loud, not silent."""
    fn = getattr(engine, "check_db_size_alert", None)
    if not callable(fn):
        return
    try:
        msg = fn()
    except Exception:  # noqa: BLE001 — maintenance must not break the heartbeat
        return
    if not msg:
        return
    if triage_caller is None:
        await _notify(notifier, NotifyLevel.OWNER, msg)
        return
    # A real problem fired — enrich it. Catalog + size read through the engine
    # seam (never the StateStore directly); both best-effort so a hiccup degrades
    # to the raw ping rather than swallowing the alarm.
    catalog = ""
    size_bytes = 0
    try:
        lp = getattr(engine, "list_problems", None)
        if callable(lp):
            catalog = _triage.format_catalog(lp())
        sb = getattr(engine, "db_size_bytes", None)
        if callable(sb):
            size_bytes = sb()
    except Exception:  # noqa: BLE001 — grounding is best-effort
        catalog, size_bytes = "", 0
    await triaged_notify(
        notifier, NotifyLevel.OWNER, msg,
        kind="db_size", triage_caller=triage_caller,
        catalog=catalog, repo_context=_triage.retention_context(size_bytes),
    )


def _engine_clear_pause(engine: GoalEngine) -> None:
    fn = getattr(engine, "clear_global_pause", None)
    if callable(fn):
        fn()


def _engine_pause_notified(engine: GoalEngine) -> bool:
    """Read the owner-was-pinged-about-this-pause flag via the engine, if it
    exposes one (the in-process engine does; test doubles may not → False)."""
    fn = getattr(engine, "pause_notified", None)
    return bool(fn()) if callable(fn) else False


def _engine_set_pause_notified(engine: GoalEngine, on: bool, kind: str = "") -> None:
    fn = getattr(engine, "set_pause_notified", None)
    if not callable(fn):
        return
    if on and kind:
        try:
            fn(on, kind)
        except TypeError:  # older double without the kind param — degrade
            fn(on)
    else:
        fn(on)


def _engine_pause_notified_kind(engine: GoalEngine) -> str:
    """The kind persisted WITH the pause ping ("" when the engine/double
    doesn't carry one). This — not the live pause_reason — is what the resume
    path keys on: the queue's 10s pump lazily clears an expired pause (reason
    included) before the heartbeat looks, on the dominant ordering."""
    fn = getattr(engine, "pause_notified_kind", None)
    return str(fn() or "") if callable(fn) else ""


def _engine_operator_block(engine: GoalEngine) -> tuple[bool, str]:
    """Read the operator hold + run-window gate via the engine, if it exposes one
    (the in-process engine does; test doubles may not → treated as open)."""
    fn = getattr(engine, "operator_block", None)
    return fn(_now_ms()) if callable(fn) else (False, "")


def _engine_goal_operator_block(engine: GoalEngine, goal_id: str) -> tuple[bool, str]:
    """Read one goal's OWN run-window gate via the engine, if it exposes one (the
    in-process engine does; test doubles may not → treated as open, so existing
    fakes tick every goal exactly as before)."""
    fn = getattr(engine, "goal_operator_block", None)
    return fn(goal_id, _now_ms()) if callable(fn) else (False, "")


def _maybe_pause(engine: GoalEngine, store: GoalStore, goal_id: str, err: str) -> "Outcome | None":
    """If ``err`` is a usage/rate-limit or an auth failure, set the shared quota
    pause and return Outcome.RATE_LIMITED; otherwise None (the caller handles it
    as a real error). Centralizes the goal-side pause guard so every cognition
    call can use it. AUTH pausing here is what turned the 2026-07-20 night's
    ~58 terminal planner failures into one pause + one actionable ping."""
    # now_utc lets absolute reset wording ("resets 10pm (UTC)") become a real
    # hint; a stated hint is trusted past the default cap (pause_seconds).
    cls = classify_failure(err, now_utc=datetime.now(timezone.utc))
    if not (cls.is_pausing and hasattr(engine, "set_global_pause")):
        return None
    backoff = pause_seconds(cls.retry_after_s, stated=cls.stated, kind=cls.kind)
    engine.set_global_pause(_now_ms() + backoff * 1000, f"{cls.kind.value} (goal cognition)")
    store.append_log(goal_id, f"paused — {cls.kind.value}; resuming in ~{backoff}s")
    return Outcome.RATE_LIMITED
