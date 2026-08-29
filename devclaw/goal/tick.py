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

import asyncio
import os
import re

from dataclasses import replace
from datetime import datetime, timezone
from typing import Callable, Protocol

from . import issue_ref as _issue_ref
from . import slice_guard as _slice_guard
from . import mergeability as _mergeability
from . import prior_increments as _prior_increments
from . import saga_framing as _saga_framing
from . import project_hold as _project_hold
from . import remote_checks as _remote_checks
from . import triage as _triage
# _deploy stays at tick.py level even though only tick_donegate._auto_deploy calls
# it: tests monkeypatch ``devclaw.goal.tick._deploy.deploy_project`` and both
# modules bind the SAME ..delivery.deploy module object, so patching it here is
# what makes the deploy stub visible to the moved _auto_deploy.
from ..advance_brief import (
    ADVANCE_BRIEF_MARKER,
    ENVCAP_FAILURE_MARKER,
    FAILURE_CONTEXT_MARKER,
    STEERING_MARKER,
)
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
from .. import config as _config

# ---- extracted-module re-export facade (behavior-preserving split) --------
# Every symbol MOVED out of this file is re-exported here so
# ``devclaw.goal.tick.<name>`` (and ~20 test imports / monkeypatch targets)
# resolve exactly as before. Import graph stays acyclic:
# tick_context <- tick_guards <- {tick_dispatch, tick_donegate} <- tick_settle.
from .tick_context import (  # noqa: F401 (re-exported)
    AUTODEPLOY_ENABLED,
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
from .tick_donegate import _finalize_pending_merge as _donegate_finalize_pending_merge
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


class TrendDetector(Protocol):
    """The two hooks the heartbeat drives on :class:`devclaw.trend_detector.TrendDetector`
    — a Protocol (not an import) because the concrete class imports goal-layer
    types and a direct import here would cycle."""

    async def run_per_goal(self, *, goal_id: str, workspace_dir: str) -> None: ...
    async def run_harness_self(self) -> None: ...



async def tick_goal(
    goal_id: str,
    *,
    store: GoalStore,
    engine: GoalEngine,
    evaluator_caller: ClaudeCaller,
    notifier: Notifier,
    notify_url: str = "",
    prepare_ws: WorkspacePrep = prepare_workspace,
    verify_done: bool = VERIFY_DONE,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    no_progress_s: int = NO_PROGRESS_S,
    summary_caller: "ClaudeCaller | None" = None,
    trend_detector: "TrendDetector | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    mergeability_probe: "_mergeability.MergeabilityProbe | None" = None,
    holders: "dict[str, str] | None" = None,
    issue_fetcher: "_issue_ref.IssueFetcher | None" = None,
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
                verify_done=verify_done, autodeploy=autodeploy,
                no_progress_s=no_progress_s,
                summary_caller=summary_caller,
                remote_checker=remote_checker,
                mergeability_probe=mergeability_probe,
                holders=holders,
                issue_fetcher=issue_fetcher,
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
    verify_done: bool = VERIFY_DONE,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    no_progress_s: int = NO_PROGRESS_S,
    summary_caller: "ClaudeCaller | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    mergeability_probe: "_mergeability.MergeabilityProbe | None" = None,
    holders: "dict[str, str] | None" = None,
    issue_fetcher: "_issue_ref.IssueFetcher | None" = None,
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
        verify_done=verify_done, autodeploy=autodeploy,
        no_progress_s=no_progress_s,
        summary_caller=summary_caller,
        remote_checker=remote_checker,
        mergeability_probe=mergeability_probe,
        holders=holders,
        issue_fetcher=issue_fetcher,
    )

    status = store.load_status(goal_id)
    phase = _classify(status)

    # Terminal short-circuit — skip even the watchdog: a done/cancelled goal
    # must keep skipping at zero cost.
    if phase is Phase.TERMINAL_DONE:
        return Outcome.SKIP_DONE
    if phase is Phase.TERMINAL_CANCELLED:
        return Outcome.SKIP_CANCELLED

    # The goal contract is goal.yaml alone; the worker's speckit artifacts
    # live in the repo, and the store's goal docs (log/deliveries/inbox/spec)
    # parse trivially.
    goal = store.load_goal(goal_id)

    # Mechanical auto-heal (F8): lift a mechanical:* block whose condition no
    # longer holds — no LLM, ever (the mirror of the quota pause's
    # timestamp-compare auto-resume in tick_all), damped by the persisted
    # per-goal heal budget so a flapping condition can't turn the zero-token
    # blocked steady-state into a plan + ping per cycle. One healable kind:
    # ``prep`` — its recheck costs a git subprocess (ls-remote), so it runs
    # on the persisted next_heal_at exponential backoff, not every tick. A
    # human-gated: resume_goal clears it.
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
        if goal.mode == "qa":
            return await _handle_qa_goal(goal_id, goal, status, finished_detail, ctx)
        return await _handle_long_lived_advance(goal_id, goal, status, finished_detail, ctx)

    raise RuntimeError(f"unhandled phase {phase} for goal {goal_id}")



# ---- phase handlers --------------------------------------------------------
# One handler per Phase value. Each takes (goal_id, goal, status, ctx) — except
# the polling handlers, which the orchestrator calls with status already loaded
# — and returns either an :class:`Outcome` (terminal for this tick) or, for
# ``_resolve_polling_action``, an ``(updated_status, finished_detail)`` tuple
# so the EXECUTING handler can chain on the same tick.


def _chunk_plan_corruption(workspace_dir: str) -> str:
    """Why the goal's chunk-plan artifact (the current feature's committed
    ``tasks.md``, spec 021 FR-004) cannot be read — or ``""`` when it can.

    Load-bearing on a CONTINUATION: mid-arc, tasks.md is the execution
    contract the next session continues from, so an unreadable one blocks the
    goal loudly instead of dispatching a session that would silently re-plan
    over prior work. Detection is narrow by design — only a feature dir that
    EXISTS with a tasks.md that cannot be read/decoded counts (a repo that
    never adopted speckit legitimately has none). Zero-LLM, pure fs; an
    unexpected checker failure degrades to "" (a checker bug must not wedge
    every dispatch). Module-global so tests patch it here."""
    try:
        feature = _slice_guard.current_feature_dir_sync(workspace_dir)
        if not feature:
            return ""
        path = os.path.join(workspace_dir, feature, "tasks.md")
        try:
            with open(path, encoding="utf-8") as fh:
                fh.read()
        except UnicodeDecodeError:
            return f"{feature}/tasks.md is not valid UTF-8"
        except OSError as exc:
            return f"{feature}/tasks.md unreadable: {exc}"
        return ""
    except Exception:  # noqa: BLE001 — narrow guard, never a dispatch wedge
        return ""


def _advance_brief(
    goal: Goal, steering: str, failure_context: str = "",
    prior_increments: str = "", issue_context: str = "",
) -> str:
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
        # The saga framing — five named slots, one generator, size-bounded
        # (spec 012 US2). Re-sent in full every increment (FR-009a); a goal
        # authored before the schema renders exactly as it did then.
        _saga_framing.render(goal),
    ]
    # The saga feed-forward (spec 012 US1): what earlier increments of THIS goal
    # delivered and how each was judged. Re-sent in full every increment
    # (FR-009a) — a fresh sandbox has no memory, so a pointer would be a request
    # while a slot is a fact. Blank-safe: callers that pass nothing render
    # byte-identically to before this feature.
    # Referenced-lane context (spec 019 US1): the referenced issues' LIVE
    # state, fetched at this dispatch — the worker reads current truth, never
    # a creation-time copy. Blank-safe: issue-less goals render byte-identically.
    if issue_context.strip():
        parts += ["", issue_context.strip()]
    if prior_increments.strip():
        parts += ["", prior_increments.strip()]
    if failure_context.strip():
        # Spec 020 FR-002a: an environment-cap (sandbox OOM) failure gets
        # cap-aware bounding advice — the generic "smaller slice" directive is
        # WRONG for this class (a smaller slice does not shrink the test
        # suite; bounding the tooling does).
        if ENVCAP_FAILURE_MARKER in failure_context:
            adapt = (
                " (failed task or failed gate) — its terminal reason, "
                "verbatim. The sandbox hit its MEMORY CAP and the kernel "
                "killed the previous session. ADAPT this session: bound your "
                "tooling to the declared allocation (DEVCLAW_SANDBOX_MEMORY/"
                "DEVCLAW_SANDBOX_CPUS in your environment) — cap test-runner "
                "workers, run suites serially, limit node heap. Prefer a "
                "slower bounded run over a faster parallel one; do NOT "
                "re-run the previous attempt's commands unchanged:"
            )
        elif "[active_slice:" in failure_context:
            # Spec 021 FR-008: the runner named the slice whose session
            # overflowed the model context. An identical re-attempt of that
            # slice is refused by construction — the brief demands a re-slice
            # of the PLAN first.
            adapt = (
                " (failed task or failed gate) — its terminal reason, "
                "verbatim. The previous session overflowed the model context "
                "while working the NAMED slice. FIRST re-slice that slice in "
                "its tasks.md into strictly smaller slices (edit the plan, "
                "commit it); THEN implement only the first sub-slice. Do not "
                "re-attempt the oversized slice unchanged:"
            )
        else:
            adapt = (
                " (failed task or failed gate) — its "
                "terminal reason, verbatim. Read it and ADAPT this session (a "
                "context overflow or timeout means: take a strictly smaller "
                "slice); do not repeat the attempt unchanged:"
            )
        parts += [
            "",
            FAILURE_CONTEXT_MARKER + adapt,
            failure_context.strip()[:800],
        ]
    if steering.strip():
        parts += ["", STEERING_MARKER, steering.strip()]
    return "\n".join(parts)


def validation_action(goal: Goal) -> Action:
    """The one Action shape a validation run dispatches as (spec 015). Shared
    by the qa cadence below and the deploy trigger (GoalService), so both
    edges produce identical runs."""
    return Action(
        engine="devclaw",
        tool="validate_product",
        goal=(
            "Validate the running product against the repo-declared "
            f"devclaw.json validation contract (qa goal {goal.id}). Boot the "
            "hermetic seeded instance, run the accumulated acceptance suites, "
            "report every failure."
        ),
        verify_cmd=None,
        open_pr=False,
    )


async def _handle_qa_goal(
    goal_id: str, goal: Goal, status: GoalStatus, finished_detail: str, ctx: TickContext,
) -> Outcome:
    """Spec 015 US3 — the ``qa`` mode's whole tick surface. A qa goal never
    plans feature work and never proposes done: a settled validation run's
    detail is appended as the RUN RECORD (the done-gate is never opened —
    validation findings are intake, not verdicts), and the only self-initiated
    dispatch is the owner-armed cadence. Unarmed (cadence empty — the shipped
    default), an idle tick is a pure timestamp write: zero cognition, zero
    subprocess (constitution III)."""
    store = ctx.store
    if finished_detail:
        # The settle header/detail IS the run record (US2 scenario 3 — a run
        # record, not silence). One line; the task row keeps the full detail.
        first = finished_detail.split("\n", 1)[0][:400]
        store.append_log(goal_id, f"qa run settled: {first}")

    cadence = (goal.cadence or "").strip()
    if cadence and store.cadence_due(goal, status):
        now = store.now_iso()
        base = replace(status, last_plan_at=now, last_tick_at=now)
        return await _dispatch_action(
            goal_id, goal, base, validation_action(goal),
            store=store, engine=ctx.engine, notifier=ctx.notifier,
            notify_url=ctx.notify_url, prepare_ws=ctx.prepare_ws,
            summarize=ctx.summary_caller, consume_steering=[],
        )

    store.update_status_fields(goal_id, last_tick_at=store.now_iso())
    return Outcome.IDLE


async def _handle_long_lived_advance(
    goal_id: str, goal: Goal, status: GoalStatus, finished_detail: str, ctx: TickContext,
) -> Outcome:
    """The ONE executing path for both modes (spec 008 shrink) — ZERO
    per-tick planner cognition (the planner was cut, demolition P3b). The mode
    dial selects only the re-evaluation cadence (ADR 0003): a one_shot goal
    rides this same advance loop — its first advance fires immediately (no
    ``last_plan_at`` yet ⇒ cadence due) and the done-gate's corrections chain
    work-present advances until achieved, so it drives to done without
    waiting out the cadence. The worker owns the plan (the speckit
    ``specs/*/`` artifacts in the repo); the control plane only dispatches
    "advance the goal via speckit" and lets the grounded done-gate judge
    done:

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
            autodeploy=ctx.autodeploy, issue_fetcher=ctx.issue_fetcher,
        )

    # Pending merge (spec 025 FR-003): a done-gate `achieved` verdict already
    # stands and only the MERGE is owed — the goal parked mechanical:merge_failed
    # and a human resumed it. Retry the merge, never the gate: zero cognition.
    # Placed BEFORE the project hold on purpose: merging is not a dispatch and
    # touches no workspace, and under lane skip-over a successor goal may hold
    # the lane while this goal finishes its merge.
    if status.pending_merge_pr and status.phase != "blocked":
        return await _donegate_finalize_pending_merge(
            goal_id, goal, status,
            store=store, notifier=ctx.notifier, summarize=ctx.summary_caller,
            autodeploy=ctx.autodeploy,
        )

    # Single-writer project hold (spec 010 P1). THE dispatch choke point: a
    # goal that is not its project's holder dispatches nothing, so two
    # independent plans can never run against one repository (the #553 class,
    # closed by construction rather than mitigated).
    #
    # Placed here on purpose — after the settled-ok done-gate branch above, so a
    # goal that still has in-flight work finishes settling it and nothing is
    # orphaned (the spec's upgrade edge case), and BEFORE the steering read
    # below, which lazily ingests inbox lines and therefore WRITES. A queued
    # tick must cost zero cognition AND zero writes: the hold is derived, so
    # there is nothing here to acquire, stamp, or release.
    #
    # ``ctx.holders`` is the sweep-wide map when tick_all threaded one in;
    # a direct tick_goal call (tick_one, tests) derives it here instead.
    holders = ctx.holders if ctx.holders is not None else _project_hold.holder_map(store)
    scope = _project_hold.scope_key(goal)
    holder = holders.get(scope) if scope else None
    if holder is not None and holder != goal_id:
        # No log line and no status write: this fires every heartbeat for as
        # long as the holder runs, and a per-tick append would bury the goal's
        # real history under queue noise. The wait is legible where an operator
        # actually looks — get_goal derives it from this same function (FR-002,
        # SC-006).
        return Outcome.QUEUED

    # Steering + should_plan gate — mirrors the planner path's gate exactly so
    # the zero-token idle guard is preserved: a blocked goal unblocks only on
    # work, an idle goal plans only on work or a due cadence.
    rows = store.unread_steering_rows(goal_id)
    steering = "\n".join(line for _, line in rows)
    # unread_steering_rows() may have lazily ingested inbox lines, bumping
    # version; reload so the dispatch's expect= CAS's against the current row
    # (same reason as _handle_long_lived_advance).
    status = store.load_status(goal_id)
    work = bool(finished_detail) or bool(steering)
    if status.phase == "blocked":
        # Human-gated: only a settle or HUMAN steering unblocks. Machine
        # rows (source ``auto-*``, e.g. the churn brake's own corrections)
        # stay parked with the goal and are consumed by the first dispatch
        # after a human acts.
        should_plan = bool(finished_detail) or store.has_unread_human_steering(goal_id)
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
    # Spec 020 FR-002a: the sandbox-OOM environment-cap class is deterministic
    # for a given environment, so it earns exactly ONE adapted re-dispatch
    # (the brief below carries cap-aware bounding advice). A recurrence past
    # that budget parks the goal with the cap in the reason — raising sizing
    # (or shrinking the verify workload) and resume_goal is the recovery path.
    # The counter resets on a productive settle (tick_settle), mirroring
    # heal_attempts.
    if failure_context and ENVCAP_FAILURE_MARKER in failure_context:
        if base.envcap_redispatches >= 1:
            cap_m = re.search(r"sandbox OOM-killed \(cap=([^,)]+)", failure_context)
            cap_txt = cap_m.group(1).strip() if cap_m else "the configured cap"
            q = (
                f"sandbox OOM at cap {cap_txt} after an adapted retry — the "
                "container memory limit was exhausted and the kernel killed "
                "the agent again. Raise sizing for this project (per-project "
                "override or DEVCLAW_SANDBOX_MEMORY) or shrink its verify "
                "workload, then resume_goal."
            )
            store.transition(
                goal_id, Event.BLOCK,
                replace(base, phase="blocked", blocked_on=q,
                        blocked_kind="mechanical:env_cap", next=""),
                expect=status, consume_steering=consume_ids,
            )
            await _notify(ctx.notifier, NotifyLevel.OWNER, f"🛑 [{goal_id}] {q[:400]}")
            return Outcome.BLOCKED
        base = replace(base, envcap_redispatches=base.envcap_redispatches + 1)
        store.append_log(
            goal_id,
            "sandbox OOM on the previous increment — dispatching the one "
            "adapted (bounded-tooling) retry this class earns (spec 020)",
        )
    # Saga feed-forward (spec 012 US1) — read BELOW the should_plan gate so an
    # idle or blocked tick performs no delivery read at all (constitution III;
    # the zero-token idle guard covers I/O, not just cognition). Pure mechanism:
    # two SQLite reads + string work, never an LLM call. Best-effort — a store
    # hiccup degrades to "no feed-forward", never a wedged dispatch.
    try:
        increment_rows = store.increment_records(goal_id)
        prior_increments = _prior_increments.render(increment_rows)
    except Exception:  # noqa: BLE001
        increment_rows = []
        prior_increments = ""
    # Chunk-plan integrity (spec 021 FR-004): a continuation (prior increments
    # exist) whose current feature's tasks.md cannot be read blocks LOUD —
    # the committed speckit artifacts are the workspace's memory of the arc,
    # and dispatching over a corrupt one silently re-plans prior work. The
    # mechanical:corrupt_doc kind self-heals when the condition clears
    # (restore the file on the goal branch, or resume_goal after fixing).
    if increment_rows:
        corrupt = await asyncio.to_thread(_chunk_plan_corruption, goal.workspace_dir)
        if corrupt:
            q = (
                f"chunk-plan artifact unreadable: {corrupt} — the committed "
                "speckit tasks.md is this goal's continuation contract. "
                "Restore it on the goal branch (or cancel and re-file), then "
                "resume_goal."
            )
            store.transition(
                goal_id, Event.BLOCK,
                replace(base, phase="blocked", blocked_on=q,
                        blocked_kind="mechanical:corrupt_doc", next=""),
                expect=status, consume_steering=consume_ids,
            )
            await _notify(ctx.notifier, NotifyLevel.OWNER, f"🛑 [{goal_id}] {q[:400]}")
            return Outcome.BLOCKED
    # Referenced lane (spec 019 US1): resolve every ref to LIVE issue state at
    # this dispatch boundary — below the should_plan gate, so idle/blocked
    # ticks fetch nothing. The fetch is LOAD-BEARING input (a worker brief,
    # not optional grounding): a failure BLOCKS human-gated instead of
    # degrading to a stale or empty ask.
    issue_context = ""
    if goal.issue_refs:
        fetcher = ctx.issue_fetcher or _issue_ref.fetch_issue
        snaps: list[_issue_ref.IssueSnapshot] = []
        try:
            for n in goal.issue_refs:
                snaps.append(await fetcher(goal.repo_url or "", n))
        except _issue_ref.IssueRefError as exc:
            q = (
                f"referenced issue could not be fetched: {exc} — a referenced "
                "goal dispatches only from live issue state, never a stale "
                "copy. Fix access or the reference, then resume_goal."
            )
            store.transition(
                goal_id, Event.BLOCK,
                replace(base, phase="blocked", blocked_on=q,
                        blocked_kind="lost_ref", next=""),
                expect=status, consume_steering=consume_ids,
            )
            await _notify(ctx.notifier, NotifyLevel.OWNER, f"🟡 [{goal_id}] {q[:400]}")
            return Outcome.BLOCKED
        open_snaps = [
            s for s in snaps if s.state == "open" and _issue_ref.is_ready(s)
        ]
        for s in snaps:
            if s.state != "open":
                store.append_log(
                    goal_id,
                    f"referenced issue #{s.number} is {s.state} — dropped from "
                    "the remaining scope (dispatch-boundary freshness guard)",
                )
            elif not _issue_ref.is_ready(s):
                # readiness revoked mid-goal (spec 019 US4 sc.3): the owner
                # pulled the label — same freshness semantics as a close.
                store.append_log(
                    goal_id,
                    f"referenced issue #{s.number} is no longer graded ready "
                    "— skipped until re-graded (dispatch-boundary freshness "
                    "guard)",
                )
        if not open_snaps:
            unready_open = [s for s in snaps if s.state == "open"]
            if unready_open:
                # Open issues whose readiness was revoked: NOT done — the
                # owner pulled the work back. Park human-gated (re-grade or
                # cancel is their call); proposing done here would judge
                # unfinished scenarios and churn the gate.
                nums = ", ".join(f"#{s.number}" for s in unready_open)
                q = (
                    f"referenced issue(s) {nums} are open but no longer "
                    "graded ready — the owner revoked readiness. Re-grade "
                    "them (regrade_intake) and resume_goal, or cancel."
                )
                store.transition(
                    goal_id, Event.BLOCK,
                    replace(base, phase="blocked", blocked_on=q,
                            blocked_kind="needs_answer", next=""),
                    expect=status, consume_steering=consume_ids,
                )
                await _notify(ctx.notifier, NotifyLevel.OWNER, f"🟡 [{goal_id}] {q[:400]}")
                return Outcome.BLOCKED
            if base.donegate_rounds == 0:
                # First pass: all issues closed, no prior done-gate refusal.
                # Out-of-band work may have fully satisfied the contract —
                # propose done and let the grounded gate decide.
                # If the gate refuses (donegate_rounds becomes > 0), the next
                # tick dispatches a worker instead of re-proposing (issue #726).
                store.append_log(
                    goal_id,
                    "all referenced issues are closed — proposing done without "
                    "dispatching a worker",
                )
                return await _open_done_gate(
                    goal_id, goal, base,
                    store=store, engine=ctx.engine,
                    evaluator_caller=ctx.evaluator_caller,
                    notifier=ctx.notifier, notify_url=ctx.notify_url,
                    prepare_ws=ctx.prepare_ws, verify_done=ctx.verify_done,
                    note="all referenced issues closed",
                    summarize=ctx.summary_caller, remote_checker=ctx.remote_checker,
                    autodeploy=ctx.autodeploy, consume_steering=consume_ids,
                    issue_fetcher=ctx.issue_fetcher,
                )
            # donegate_rounds > 0: the done-gate already refused — the contract
            # is unmet even with all issues closed. An issue can be closed by a
            # partial implementation (e.g. a PR with Closes #N on an
            # intermediate increment while the full spec remains unbuilt).
            # Dispatch a worker to complete the remaining contract; the closed
            # issues tell it not to re-open or re-work them. The issue closure
            # is an input to the evaluation, not the verdict (spec 019 US2).
            store.append_log(
                goal_id,
                f"all referenced issues are closed but done-gate previously "
                f"refused ({base.donegate_rounds} round(s)) — dispatching "
                "worker to complete the remaining contract",
            )
            issue_context = _issue_ref.render_issue_context([], snaps)
        else:
            issue_context = _issue_ref.render_issue_context(
                open_snaps, [s for s in snaps if s.state != "open"]
            )
    action = Action(
        engine="devclaw",
        tool="implement_feature",
        goal=_advance_brief(
            goal, steering, failure_context=failure_context,
            prior_increments=prior_increments, issue_context=issue_context,
        ),
        verify_cmd=goal.verify_cmd,
        open_pr=goal.open_pr,
    )
    return await _dispatch_action(
        goal_id, goal, base, action,
        store=store, engine=ctx.engine, notifier=ctx.notifier,
        notify_url=ctx.notify_url, prepare_ws=ctx.prepare_ws,
        summarize=ctx.summary_caller, consume_steering=consume_ids,
    )



# ---- multi-goal driver -----------------------------------------------------


async def tick_all(
    *,
    store: GoalStore,
    engine: GoalEngine,
    evaluator_caller: ClaudeCaller,
    notifier: Notifier,
    notify_url: str = "",
    prepare_ws: WorkspacePrep = prepare_workspace,
    verify_done: bool = VERIFY_DONE,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    no_progress_s: int = NO_PROGRESS_S,
    summary_caller: "ClaudeCaller | None" = None,
    verify_done_resolver: "Callable[[Goal], bool] | None" = None,
    autodeploy_resolver: "Callable[[Goal], bool | None] | None" = None,
    tracer_factory: "Callable[[str], _trace.Tracer | None] | None" = None,
    trend_detector: "TrendDetector | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    triage_caller: "ClaudeCaller | None" = None,
    mergeability_probe: "_mergeability.MergeabilityProbe | None" = None,
    project_workspaces: "Callable[[], set[str]] | None" = None,
    issue_fetcher: "_issue_ref.IssueFetcher | None" = None,
) -> dict[str, Outcome]:
    """Tick every goal. One goal's failure never stops the others, and a usage
    limit pauses the whole layer (0 tokens) rather than crashing per-goal.

    ``tracer_factory(goal_id) -> Tracer | None`` is the seam GoalService uses
    to attach a :class:`PersistentTracer` per goal-tick so the cascade's
    cognition / dispatch / delivery events land in the durable trace store.

    ``verify_done_resolver`` and ``autodeploy_resolver`` compute the done-gate
    re-check flag and the on-complete deploy flag FRESH per goal (a project's
    override must not leak from one goal onto another in the same sweep), each
    taking precedence over its flat counterpart.

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
                # terminal failures). Name the exact credential path so the
                # operator doesn't re-login to the wrong user/path (2026-08-19:
                # root re-login changed nothing because the container reads a
                # different home; the hour-long archaeology is the cost of
                # omitting this line — issue #569).
                cred_path = _config.host_claude_dir() + "/.credentials.json"
                msg = (
                    f"🔑 paused — Claude auth/login failure ({reason}). "
                    f"A re-login must land in `{cred_path}` (the path this "
                    f"instance reads — a login elsewhere changes nothing). "
                    f"Fastest fix: `claude setup-token` via the container, or "
                    f"copy a fresh `.credentials.json` into that path. "
                    f"Verify with a `dry_evaluate` probe — the pause "
                    f"auto-resumes on the next probe ~{resume_hhmm} UTC; "
                    f"I'll re-ping if still broken."
                )
                # The instance-dead class (spec 025 US3): an auth failure only
                # a human re-login fixes must pierce quiet mode — an unsent
                # auth ping silently kills an unattended week.
                await _notify(notifier, NotifyLevel.OWNER, msg,
                              summarize=summary_caller, critical=True)
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
    # Same cheap slot, same zero-LLM guarantee: release the workspace of a
    # goal that ended long enough ago that nobody will look at it again
    # (#595). Bounded per tick and watermark-gated inside the engine, so a
    # 34-directory backlog drains across ticks instead of wedging one.
    _engine_reap_workspaces(engine, store, project_workspaces)
    # Loud-not-silent DB-size alarm: if the .db has grown past the threshold
    # despite retention+VACUUM, ping the owner ONCE (re-armed when it drops back
    # under) — a silent disk-fill wedge is the failure mode this whole tranche
    # exists to prevent. Zero LLM (raw owner ping, no summarizer).
    await _maybe_alert_db_size(engine, notifier, triage_caller=triage_caller)

    # Single-writer project hold (spec 010 P1): derive who holds each project
    # ONCE for the whole sweep. The derivation reads every goal, so deriving it
    # per goal would make one sweep an N² scan. Cheap and zero-LLM — it belongs
    # in this same pre-loop slot as the other mechanical housekeeping above.
    holders = _project_hold.holder_map(store)

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
        goal_verify_done = verify_done
        goal_autodeploy = autodeploy
        # Load the goal once for whichever per-goal resolvers are wired (a bad
        # goal.yaml must not sink the sweep — fall back to the flat values).
        if any(r is not None for r in (verify_done_resolver, autodeploy_resolver)):
            try:
                _g = store.load_goal(goal_id)
                if verify_done_resolver is not None:
                    goal_verify_done = verify_done_resolver(_g)
                if autodeploy_resolver is not None:
                    goal_autodeploy = autodeploy_resolver(_g)
            except Exception:  # noqa: BLE001 — a bad goal.yaml must not sink the sweep
                goal_verify_done, goal_autodeploy = verify_done, autodeploy
        try:
            with _trace.tracer_scope(tracer):
                outcomes[goal_id] = await tick_goal(
                    goal_id, store=store, engine=engine,
                    evaluator_caller=evaluator_caller,
                    notifier=notifier, notify_url=notify_url, prepare_ws=prepare_ws,
                    verify_done=goal_verify_done,
                    autodeploy=goal_autodeploy, no_progress_s=no_progress_s,
                    summary_caller=summary_caller,
                    trend_detector=trend_detector,
                    remote_checker=remote_checker,
                    mergeability_probe=mergeability_probe,
                    holders=holders,
                    issue_fetcher=issue_fetcher,
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


def _engine_reap_workspaces(
    engine: GoalEngine,
    store: GoalStore,
    project_workspaces: "Callable[[], set[str]] | None",
) -> None:
    """Run the daily goal-workspace retention sweep via the engine, if it
    exposes one. Best-effort — a maintenance failure must never break the
    heartbeat, exactly like the trace prune.

    ``project_workspaces`` resolves which workspaces belong to REGISTERED
    projects; those are released by ``delete_project``, never swept because
    their goals happen to be terminal. No resolver (or one that raises) means
    the sweep does nothing — never that everything is fair game."""
    fn = getattr(engine, "reap_workspaces", None)
    if not callable(fn):
        return
    try:
        owned = project_workspaces() if project_workspaces else None
    except Exception:  # noqa: BLE001 — unknown ownership sweeps nothing
        return
    rows: list[dict] = []
    for gid in store.list_goal_ids():
        # id + workspace_dir live on the durable Goal; phase/direction/timestamps
        # on the mutable GoalStatus. The sweep needs both, so flatten here.
        try:
            goal = store.load_goal(gid)
            status = store.load_status(gid)
        except Exception:  # noqa: BLE001 — an unreadable goal is simply not swept
            continue
        rows.append(
            {
                "id": gid,
                "workspace_dir": goal.workspace_dir,
                "phase": status.phase,
                "direction": getattr(status, "direction", None),
                "blocked_on": status.blocked_on,
                "last_progress_at": getattr(status, "last_progress_at", None),
                "last_tick_at": getattr(status, "last_tick_at", None),
                "last_eval_at": getattr(status, "last_eval_at", None),
                "last_plan_at": getattr(status, "last_plan_at", None),
            }
        )
    try:
        fn(rows, owned)
    except Exception:  # noqa: BLE001 — maintenance must not break the heartbeat
        pass


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
