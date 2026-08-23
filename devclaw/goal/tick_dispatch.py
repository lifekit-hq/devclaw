"""Action dispatch — the goal-tick engine-launch path.

The dispatch-cap backstop + atomic action dispatch. (The investigation/
firming/decompose dispatch family died with the host-cognition chain, spec 008
shrink — the worker plans via speckit in-sandbox.) Split out of
:mod:`devclaw.goal.tick`; imports tick_context + tick_guards, and is called by
tick._tick_goal_impl / tick._handle_long_lived_advance via the tick.py re-export facade.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

from ..task_git import BRANCH_STALE_THRESHOLD
from ..task_git import branch_staleness_sync as _branch_staleness_sync
from ..task_git import _default_branch_sync
from .tick_context import (
    NotifyLevel,
    Outcome,
    WorkspacePrep,
    _action_label,
    _engine_kick,
    _notify,
    _run_atomic,
)
from .tick_guards import _block_on_prep_failure
from . import fanout as _fanout
from . import slice_guard as _slice_guard
from . import delivery_strategy as _delivery
from . import repo_brief as _repo_brief
from .engine import GoalEngine
from .models import Action, Goal, GoalStatus
from .notify import Notifier
from ..llm_call import ClaudeCaller
from .store import GoalStore
from .transitions import Event
from ..advance_brief import display_goal as _display_goal
from ..engine.workspace import WorkspaceError
from ..loom import trace as _trace
from .. import speckit_setup as _speckit


async def _branch_staleness(workspace_dir: str, goal_id: str) -> "dict | None":
    """Best-effort commits-ahead/behind probe for ``goal/<goal_id>`` vs the
    repo's default branch.

    Called after ``prepare_ws`` (which has already fetched origin), so the
    ``refs/remotes/origin/HEAD`` symbolic-ref is cached and
    ``_default_branch_sync`` is a cheap read. Returns
    ``{"commits_behind": int, "commits_ahead": int}`` or ``None`` on any
    failure — callers degrade to "proceed unchanged". Module-level so tests
    can patch ``_branch_staleness_sync`` / ``_default_branch_sync`` here.
    Zero-LLM; never raises."""
    def _probe() -> "dict | None":
        try:
            base = _default_branch_sync(workspace_dir)
            return _branch_staleness_sync(workspace_dir, base)
        except Exception:  # noqa: BLE001
            return None
    try:
        return await asyncio.to_thread(_probe)
    except Exception:  # noqa: BLE001
        return None


async def _dispatch_action(
    goal_id: str, goal: Goal, base: GoalStatus, action: Action,
    *, store: GoalStore, engine: GoalEngine, notifier: Notifier,
    notify_url: str, prepare_ws: WorkspacePrep,
    summarize: "ClaudeCaller | None" = None,
    consume_steering: "list[int] | None" = None,
) -> Outcome:
    # Runaway backstop (mechanism, not cognition): never spawn more than the
    # goal's known-bounded work surface + a small margin without a human. The
    # cap is ``len(backlog) + 2`` — tight, matched to the owner's brain-dump of
    # starting tasks. (The checklist-mode cap widening died with the checklist,
    # spec 008 shrink.)
    #
    # The counter is progress-aware: a dispatch that settles successfully
    # (done, gate passed or gateless) is refunded on settle (see
    # _resolve_polling_action), so the cap measures OUTSTANDING failed or
    # gate-failed dispatches, not lifetime throughput. A healthy auto-merging
    # mission goal — including its own verification reviews — never trips it;
    # a looping advance still does. Live-found 2026-07-07 (blocked on merged
    # work) and again 2026-07-09 (blocked on on_track reviews).
    cap = len(goal.backlog) + 2
    if base.actions_dispatched >= cap:
        store.append_log(goal_id, f"dispatch cap {cap} reached — blocking for review")
        store.transition(
            goal_id, Event.BLOCK,
            replace(base, phase="blocked", blocked_on=f"dispatch cap {cap} reached — review the open PRs",
                    blocked_kind="mechanical:dispatch_cap"),
            expect=base, consume_steering=consume_steering,
        )
        await _notify(notifier, NotifyLevel.OWNER, f"🛑 [{goal_id}] dispatch cap ({cap}) reached — paused for your review", summarize=summarize)
        return Outcome.BLOCKED
    # Give the engine a pristine checkout. Per-action mode resets to
    # origin/<default> for freshness; goal-branch mode checks out ``goal/<id>``
    # instead so each increment's commits STACK on the prior ones rather than
    # fork off main and re-implement the foundation (the 2026-06-26
    # finance-sentry-mcp-v3 PR-fan-out failure). Read-only
    # ``review_repository`` actions always run on the default branch — they
    # don't write.
    branch_for_dispatch: str | None = None
    if action.tool != "review_repository":
        branch_for_dispatch = _delivery.resolve_strategy(store, goal_id).goal_branch(goal_id)
    try:
        await prepare_ws(goal.workspace_dir, goal.repo_url, branch_for_dispatch)
    except WorkspaceError as exc:
        return await _block_on_prep_failure(
            goal_id, base, exc, store=store, notifier=notifier, summarize=summarize,
        )
    # Staleness probe (goal-branch mode only): skip dispatch only when the goal
    # branch is HARD-STALE — 0 commits ahead of the default branch AND at least
    # BRANCH_STALE_THRESHOLD commits behind it (created off a very old base, so a
    # fresh worker would build a massive-conflict PR on top of already-moved-on
    # main). This mirrors the prep-time refuse guard in task_queue's
    # ``_prep_branch_target`` — the SAME predicate. It must NOT skip on
    # ``commits_ahead == 0`` alone: a brand-new goal branch (first item) and a
    # post-merge re-seeded branch are BOTH 0 ahead / 0 behind, and skipping those
    # livelocks the goal (the first item never dispatches, so nothing ever lands,
    # so the branch stays 0-ahead forever — invariant-guard finding on #439).
    # Best-effort: a probe hiccup (None) lets dispatch proceed unchanged.
    if branch_for_dispatch is not None:
        staleness = await _branch_staleness(goal.workspace_dir, goal_id)
        if (
            staleness is not None
            and staleness["commits_ahead"] == 0
            and staleness["commits_behind"] >= BRANCH_STALE_THRESHOLD
        ):
            store.append_log(
                goal_id,
                f"staleness: {branch_for_dispatch} is hard-stale — 0 commits ahead"
                f" of default but {staleness['commits_behind']} behind"
                f" (threshold {BRANCH_STALE_THRESHOLD}); skipping dispatch",
            )
            return Outcome.SLEPT
    # No half-installed execution (spec 008 US2): a durable goal must not
    # dispatch FEATURE work into a repo whose speckit install PR is still open —
    # the heartbeat counterpart of the MCP tool's _block_if_speckit_pending, at
    # the shared dispatch chokepoint. Cheap + test-inert: feature_block_reason
    # probes only when a LOCAL install branch exists (concrete evidence onboard
    # scaffolded here), so an ordinary repo pays one `git branch` and this never
    # fires in the stubbed suite. A soft HOLD (no state transition — mirrors the
    # staleness skip above), re-checked next tick; it clears when the install PR
    # merges and lands. Read-only reviews are exempt.
    if action.tool != "review_repository":
        try:
            hold_reason = await _speckit.feature_block_reason(goal.workspace_dir)
        except Exception:  # noqa: BLE001 — a probe hiccup must never wedge dispatch
            hold_reason = None
        if hold_reason:
            store.append_log(goal_id, f"dispatch held: {hold_reason}")
            return Outcome.SLEPT
    # ---- planned fan-out (spec 010 US3, FR-101) -----------------------------
    # Read the plan for a group of tasks it already declared topologically
    # independent — consecutive `[P]` rows with disjoint declared file scopes —
    # and dispatch them as ONE program of concurrent lanes instead of one
    # increment. The decision is the PLAN's; this code only obeys it (the
    # 2026-08-18 ruling: parallelism is data, never executor control flow).
    #
    # Off unless the operator opted in, never for read-only reviews, and never
    # outside goal-branch mode (per-action delivery has no shared branch to
    # integrate onto). Zero-token: a glob, a file read, a string parse — and it
    # sits AFTER the phase/hold gates, so no idle or blocked tick gains work.
    #
    # Every failure here degrades to ordinary single-increment dispatch, which is
    # always correct and merely slower.
    lanes: list = []
    fanout_dispatch = getattr(engine, "dispatch_fanout", None)
    if (
        action.tool != "review_repository"
        and branch_for_dispatch is not None
        and fanout_dispatch is not None
        and _fanout.enabled()
    ):
        try:
            lanes = await asyncio.to_thread(_fanout.plan_lanes_sync, goal.workspace_dir)
        except Exception:  # noqa: BLE001 — a planning hiccup must never wedge dispatch
            lanes = []
        for lane in list(lanes):
            # Each lane needs its OWN checkout of the goal branch: two agents
            # cannot share a working tree. A prep failure abandons the whole
            # group rather than fanning out a half-prepared one.
            try:
                await prepare_ws(lane.workspace_dir, goal.repo_url, branch_for_dispatch)
            except Exception as exc:  # noqa: BLE001
                store.append_log(
                    goal_id,
                    f"fan-out abandoned: could not prepare lane workspace for "
                    f"{lane.key} ({exc}) — dispatching one increment instead",
                )
                lanes = []
                break
        if lanes:
            store.append_log(
                goal_id,
                "fan-out: dispatching "
                + str(len(lanes))
                + " planned parallel lanes — "
                + "; ".join(f"{ln.key} [{', '.join(ln.scopes)}]" for ln in lanes),
            )

    # Atomic dispatch (PR7): task/program row creation + the DISPATCH
    # transition + the log row, as ONE transaction. A crash or CAS conflict
    # anywhere inside rolls the whole unit back — the single-task-orphan
    # class (task dispatched, ref write lost) becomes structurally
    # impossible on the in-process engine. `dispatch_exc` distinguishes
    # "engine.dispatch itself raised" (recovered below, outside the aborted
    # unit, exactly as today) from a TransitionConflict/IllegalTransition
    # raised by store.transition() (propagated UNTOUCHED to tick_goal's
    # top-level choke point, same as before this PR).
    dispatch_exc: "Exception | None" = None
    # Repo-scoped worker brief (MC borrow item 3): prepend the accumulated
    # notes previous workers handed back for THIS repo — build quirks, test
    # gotchas — so a fresh goal doesn't relearn them from zero. Mechanism,
    # not cognition (one SQLite read); skipped for read-only reviews so the
    # reviewer's grounded read isn't seeded with prior claims to confirm.
    brief_prefix = ""
    if action.tool != "review_repository":
        scope = _repo_brief.scope_key_for(goal.workspace_dir)
        if scope:
            brief_prefix = _repo_brief.render_brief_prefix(store.read_repo_brief(scope))
    # Human-facing form of the action (#550 — the display half of the #547
    # class): the thin-advance brief is dispatch plumbing, so everything a
    # HUMAN reads from this dispatch — the ``next`` hint (get_goal/console/
    # STATUS.md), the goal-log head, and the settle-side delivery record +
    # notification/trace labels via the re-stamped ref — renders the goal's
    # objective instead. Only the WORKER receives the full brief
    # (``dispatch_action`` below is untouched).
    display = _display_goal(action.goal)
    try:
        with store.transaction():
            try:
                dispatch_action = (
                    replace(action, goal=brief_prefix + action.goal)
                    if brief_prefix else action
                )
                ref = _run_atomic(
                    fanout_dispatch(dispatch_action, goal, notify_url, lanes)
                    if lanes and fanout_dispatch is not None  # lanes ⇒ fanout (guard above)
                    else engine.dispatch(dispatch_action, goal, notify_url)
                )
            except Exception as exc:  # noqa: BLE001 — caught again below, outside the txn
                dispatch_exc = exc
                raise
            # Re-stamp the ref with the DISPLAY form of the action text. The
            # brief prefix + retry digest are worker INPUT; the ref's goal is
            # what settle records as a delivery and what the direction
            # evaluator reads as "grounded deliveries" — prior workers'
            # unverified hints must not ride that channel as evidence
            # (invariant-guard finding). Same for the raw advance brief
            # (#550): the delivery record / _action_label telemetry names the
            # OBJECTIVE, never the dispatch instruction.
            if dispatch_action is not action or display != action.goal:
                ref = replace(ref, goal=display)
            store.transition(
                goal_id, Event.DISPATCH_ACTION,
                replace(
                    base, phase="in_flight", in_flight=ref, blocked_on=None, next=display,
                    actions_dispatched=base.actions_dispatched + 1,
                ),
                expect=base, consume_steering=consume_steering,
            )
            store.append_log(goal_id, f"dispatched {action.tool}: {display} → {ref.id}", mirror=False)
    except Exception:
        store.discard_pending_mirrors(goal_id)
        if dispatch_exc is None:
            raise  # a real TransitionConflict/IllegalTransition — propagate untouched
        # engine.dispatch raised INSIDE the txn → it rolled back cleanly (no
        # row survives). Re-derive today's error path OUTSIDE the aborted
        # unit: a fresh, separate RESUME_IDLE write.
        store.append_log(goal_id, f"dispatch error ({action.tool}): {dispatch_exc}")
        store.transition(
            goal_id, Event.RESUME_IDLE, replace(base, phase="idle", next=display),
            expect=base, consume_steering=consume_steering,
        )
        await _notify(notifier, NotifyLevel.TASK, f"⚠️ [{goal_id}] dispatch failed: {dispatch_exc}")
        return Outcome.ERROR
    # Past this point the dispatch transaction has committed. Render the
    # deferred mirrors, THEN kick the queue to claim/launch the just-committed
    # row (pump=False left it merely 'pending'), then trace/notify — all
    # post-commit, per the mirror-discipline + dispatch/pump-split rules.
    store.render_mirrors(goal_id)
    # Record which speckit feature this dispatch is advancing (spec 008 US1, D6)
    # so the done-gate can ground on the right specs/NNN/spec.md. Best-effort,
    # AFTER the settle transaction committed (a plain file doc, never inside the
    # atomic unit); a detection hiccup must never wedge or re-settle a dispatch.
    # Read-only reviews carry no feature to advance. Zero-token (pure fs read),
    # work-present path — the idle guard is untouched.
    if action.tool != "review_repository":
        try:
            feature_dir = await asyncio.to_thread(
                _slice_guard.current_feature_dir_sync, goal.workspace_dir
            )
            store.write_executing_feature(goal_id, feature_dir)
        except Exception:  # noqa: BLE001 — recording is a bonus, never a dispatch gate
            pass
    _engine_kick(engine)
    _trace.record_dispatch(goal_id=goal_id, tool=action.tool, ref_id=ref.id, engine=getattr(engine, "kind", ""))
    # Notify uses the short label of the DISPLAY form, not the full prompt
    # body — the raw `action.goal` is a multi-paragraph executor instruction
    # (often 500-1500 chars) and dumping it to Telegram floods the owner with
    # prompt boilerplate; on the thin path even its first line is the advance
    # brief, which every human surface renders as the objective (#550). The
    # full brief reaches only the worker.
    await _notify(
        notifier, NotifyLevel.TASK,
        f"🚀 [{goal_id}] {action.tool}: {_action_label(replace(action, goal=display))}",
    )
    return Outcome.DISPATCHED


