"""Blocking guards + the no-progress watchdog — the goal-tick failure handlers.

These are the "fail loud, not silent" handlers (CLAUDE.md hardening philosophy):
a workspace-prep failure and a lost in-flight ref each
block the goal legibly with an owner ping instead of wedging the tick loop; the
watchdog fires one owner ping when an executing goal stops shipping. Split out of
:mod:`devclaw.goal.tick`; imported by tick_dispatch / tick_settle and re-exported
from tick.py.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import datetime, timedelta

from .tick_context import NotifyLevel, Outcome, TickContext, _notify
from .engine import GoalEngineError
from .models import Goal, GoalStatus, Phase
from .notify import Notifier
from ..llm_call import ClaudeCaller
from .store import GoalStore
from .transitions import Event
from ..engine.workspace import WorkspaceError
from ..task_git import _ls_remote_ok_sync
from .. import env_cap as _env_cap
from .. import project_manifest as _manifest


def _progress_window_active(status: GoalStatus) -> bool:
    """Is this a goal the no-progress watchdog should measure? Only one that is
    actively executing — not waiting on the owner (the `blocked` phase, which
    already pinged), not done/cancelled (returned earlier). 'verifying' counts
    (the done-gate review is still work in flight)."""
    return status.lifecycle == "executing" and status.phase in (
        "idle", "in_flight", "verifying",
    )


async def _check_no_progress(
    goal_id: str, goal: Goal, status: GoalStatus,
    *, store: GoalStore, notifier: Notifier, window_s: int,
    summarize: "ClaudeCaller | None" = None,
) -> GoalStatus:
    """Zero-token wall-clock watchdog. Self-initializes the progress baseline the
    first time it sees an executing goal, then fires exactly one OWNER ping if the
    goal has gone ``window_s`` without a delivery. Returns the (possibly updated)
    status so the caller carries the baseline/flag forward instead of clobbering it.

    Pure mechanism: it reads timestamps and never calls cognition on the measuring
    path (the summarizer only runs for the one ping that actually clears the gate)."""
    if goal.mode == "qa":
        # A qa goal never ships (spec 015 US3) — "no delivery in N hours" is
        # its healthy steady state, not a stall; the watchdog would ping the
        # owner forever.
        return status
    if window_s <= 0 or not _progress_window_active(status):
        return status
    if status.last_progress_at is None:
        # start the clock — the goal just began executing (covers goals created
        # any path into executing, without touching every transition site).
        # Telemetry-only field → update_status_fields, never a full-row rewrite
        # (a full save_status here would be the exact stale-snapshot clobber
        # class this PR closes: a watchdog init racing a concurrent
        # phase-changing write must never win).
        status = store.update_status_fields(goal_id, last_progress_at=store.now_iso())
        return status
    elapsed = store.seconds_since(status.last_progress_at)
    if elapsed is None or elapsed < window_s or status.no_progress_notified:
        return status
    hours = round(elapsed / 3600, 1)
    status = store.update_status_fields(goal_id, no_progress_notified=True)
    store.append_log(goal_id, f"no-progress watchdog fired — ~{hours}h since last delivery")
    await _notify(
        notifier, NotifyLevel.OWNER,
        f"🐢 [{goal_id}] no progress in ~{hours}h on \"{goal.objective}\" — "
        f"it's still working but nothing has shipped; you may want to take a look",
        summarize=summarize,
    )
    return status


async def _block_on_prep_failure(
    goal_id: str, status: GoalStatus, exc: "WorkspaceError",
    *, store: GoalStore, notifier: Notifier, summarize: "ClaudeCaller | None",
) -> Outcome:
    """A workspace couldn't be prepared — a bad/missing ``repo_url``, a clone
    that 404s (the repo doesn't exist *or* is private and unreadable — GitHub
    returns the same "not found" for both), an auth or fetch failure. None of
    these self-heal on the next tick, so the old behaviour (log it, drop to
    ``phase=idle``, notify at TASK altitude) made the goal look merely idle while
    it silently re-tried the same doomed clone every cadence — invisible to the
    owner, who then can't tell a wedged goal from one with nothing to do.

    Instead: block with the *real* git error as ``blocked_on`` and tell the owner
    at OWNER altitude. Subsequent ticks route through the blocked-guard in
    :func:`tick_goal` (cadence does not re-poke a blocked goal — only steering
    does), making this a single, legible notification rather than a per-tick
    loop. When the owner answers/steers (e.g.
    fixes the repo_url), the goal unblocks and prep is retried with the fix."""
    msg = str(exc)
    store.append_log(goal_id, f"workspace prep failed — blocking for the owner: {msg}")
    store.transition(
        goal_id, Event.BLOCK,
        replace(status, phase="blocked", blocked_on=msg,
                blocked_kind="mechanical:prep", in_flight=None, next=""),
        expect=status,
    )
    await _notify(
        notifier, NotifyLevel.OWNER,
        f"🟡 [{goal_id}] I couldn't set up the workspace, so I've paused — {msg}",
        summarize=summarize,
    )
    return Outcome.BLOCKED


async def _block_on_lost_ref(
    goal_id: str, status: GoalStatus, exc: GoalEngineError, ctx: TickContext,
) -> Outcome:
    """The in-flight ref points at a task/program row the engine no longer has
    (a lost/replaced SQLite DB, manual row cleanup, a cross-environment
    restore). The row never comes back, so an unguarded poll raises into
    tick_all's per-goal catch-all — which logs "tick error (isolated)" but
    never clears ``in_flight``, and the goal re-raises identically on EVERY
    subsequent tick: a silent, permanent error loop the owner never hears
    about (audit-found 2026-07-10).

    Instead: clear the lost ref, block with the real error as ``blocked_on``,
    and tell the owner ONCE at OWNER altitude. Blocked goals are not re-poked
    by cadence (see :func:`_handle_long_lived_advance`) — only steering unblocks them —
    so this is one legible failure, and the owner decides how to proceed
    (typically steer_goal to re-plan). (Both this and
    :func:`_block_on_prep_failure` used to pin ``lifecycle="executing"`` here,
    to stop a lost DISCOVERY ref leaving the goal in ``investigating`` and
    being routed straight back into a fresh dispatch that contradicted the
    "paused; steer me" ping. That phase was removed by the spec 008 shrink and
    the #616 cutoff migrated the last rows carrying it, so the pin has nothing
    left to correct.) Catches :class:`GoalEngineError` ONLY — a real bug must
    still surface through the catch-all, not be absorbed as a lost ref.

    DELIBERATELY HUMAN-GATED — never auto-healed. Unlike a prep failure
    (the remote can come back), this
    block is structurally unhealable by mechanism: ``in_flight`` is destroyed
    right here at block time (the ``in_flight=None`` below), so the lost id
    survives only in the ``blocked_on`` prose — there is nothing machine-
    readable left for a recheck to re-poll, and re-attaching from prose would
    be exactly the string-matching ``blocked_kind`` exists to forbid. The
    owner decides how to proceed (typically steer_goal to re-plan)."""
    ref = status.in_flight
    assert ref is not None  # only reachable from a poll on an in-flight ref
    msg = f"lost in-flight {ref.ref_kind} {ref.id} — {exc}"
    ctx.store.append_log(goal_id, f"poll failed — blocking for the owner: {msg}")
    ctx.store.transition(
        goal_id, Event.BLOCK,
        replace(
            status, in_flight=None,
            phase="blocked", blocked_on=msg, blocked_kind="mechanical:lost_ref", next="",
        ),
        expect=status,
    )
    await _notify(
        ctx.notifier, NotifyLevel.OWNER,
        f"🟡 [{goal_id}] I lost track of the in-flight work ({ref.ref_kind} {ref.id}) — "
        "paused; steer me to continue",
        summarize=ctx.summary_caller,
    )
    return Outcome.BLOCKED


#: Max mechanical auto-heals for one goal before the loop hands the block back
#: to a human. Damping is MANDATORY, not a nicety: the quota pause's auto-resume
#: (tick_all) needed none because its heal signal is monotone (time only moves
#: forward), but a mechanical condition can FLAP — block → heal → re-block —
#: and an undamped heal would convert the zero-token blocked steady-state into
#: a planner call (+ a block ping) per cycle. Past the cap the goal stays
#: blocked at zero cost until a human lifts it (steer_goal), which restores the
#: budget; a productive settle also earns it back (see tick_settle).
PREP_HEAL_CAP = 5

#: Exponential backoff for the prep recheck: 30min · 2^heal_attempts, capped.
#: The recheck is a git subprocess, so between windows a blocked goal must stay
#: a zero-subprocess tick — the persisted ``next_heal_at`` window enforces it.
PREP_BACKOFF_BASE_S = 30 * 60
PREP_BACKOFF_MAX_S = 6 * 3600

#: Heal budget for a ``mechanical:env`` hold (spec 030). The recheck is free
#: (a persisted-row read), so unlike prep there is no backoff window — the cap
#: exists only to park a FLAPPING capability for the owner instead of cycling
#: hold→resume→hold forever. Counted on its OWN column
#: (``GoalStatus.env_heal_attempts``), never the shared ``heal_attempts``: a
#: goal that had earlier healed unrelated ``mechanical:prep`` blocks would
#: otherwise arrive at its first env hold with the budget already spent and
#: sit parked instead of auto-resuming within one sweep of the probe going
#: green (US2).
ENV_HEAL_CAP = 5


async def _prep_recheck_ok(goal: Goal) -> bool:
    """The mechanical prep recheck — no LLM, best-effort, never raises.

    With a ``repo_url``: one ``git ls-remote`` (offloaded to a thread — it can
    block up to its 10s timeout) probing the exact surface prepare_workspace's
    clone/fetch fails on. Without one (pre-existing-workspace config, where
    prep only resets the checkout): does ``<workspace_dir>/.git`` exist —
    a stat, no subprocess."""
    if not goal.repo_url:
        return os.path.isdir(os.path.join(goal.workspace_dir, ".git"))
    return await asyncio.to_thread(_ls_remote_ok_sync, goal.repo_url)


async def _autoheal_prep(
    goal_id: str, goal: Goal, status: GoalStatus,
    *, store: GoalStore, notifier: Notifier,
) -> "GoalStatus | None":
    """Mechanically lift a ``mechanical:prep`` block once the repo is reachable
    again. The recheck COSTS a git
    subprocess, so it runs on a persisted exponential backoff instead of every
    tick. ``next_heal_at`` gates it — before that instant the tick returns
    immediately (zero subprocess, zero cognition); a FAILED recheck pushes the
    window out (30min · 2^attempts, capped at 6h) and spends one attempt.

    A successful recheck fires the resume-shaped UNBLOCK
    (:func:`_heal_unblock`): the next dispatch runs the REAL prepare_ws — ls-remote
    proves reachability, not that the clone will succeed — and if prep still
    fails it re-blocks loudly and the backoff continues where it left off
    (``heal_attempts`` persists across the heal; only a human unblock or a
    productive settle resets it).

    Returns the healed status, or ``None`` (parked / window not open /
    still unreachable)."""
    if status.heal_attempts > PREP_HEAL_CAP:
        return None  # parked — the gave-up ping already went out
    if status.heal_attempts >= PREP_HEAL_CAP:
        await _heal_give_up(
            goal_id, store=store, notifier=notifier, cap=PREP_HEAL_CAP,
            reason="the workspace still can't be prepared",
        )
        return None
    # Backoff window: no recheck — not even the subprocess — before it opens.
    remaining = store.seconds_since(status.next_heal_at)
    if status.next_heal_at and remaining is not None and remaining < 0:
        return None
    if not await _prep_recheck_ok(goal):
        backoff_s = min(PREP_BACKOFF_MAX_S, PREP_BACKOFF_BASE_S * (2 ** status.heal_attempts))
        next_at = (
            datetime.fromisoformat(store.now_iso()) + timedelta(seconds=backoff_s)
        ).isoformat(timespec="seconds")
        n = status.heal_attempts + 1
        store.update_status_fields(goal_id, heal_attempts=n, next_heal_at=next_at)
        store.append_log(
            goal_id,
            f"prep recheck: repo still unreachable (attempt {n}/{PREP_HEAL_CAP}) — "
            f"next recheck at {next_at}",
        )
        return None
    n = status.heal_attempts + 1
    healed = _heal_unblock(goal_id, status, store, heal_attempts=n)
    store.append_log(
        goal_id,
        f"auto-resumed: repo reachable again (heal {n}/{PREP_HEAL_CAP}) — "
        "next dispatch retries the real workspace prep",
    )
    return healed


def _heal_unblock(
    goal_id: str, status: GoalStatus, store: GoalStore, *, heal_attempts: int,
    env_heal_attempts: "int | None" = None,
) -> GoalStatus:
    """The resume-shaped UNBLOCK write the mechanical heal fires: actions +
    plan cadence reset so the tick actually re-plans, the backoff window
    cleared, and a preserved in-flight ref restored to its polling phase so it
    settles normally instead of being orphaned.

    Each brake spends its OWN budget: ``env_heal_attempts`` is written only
    when the env heal supplies it, so an env heal never consumes the prep
    budget and vice versa."""
    if status.in_flight is not None:
        restored_phase: Phase = "verifying" if status.in_flight.is_done_check else "in_flight"
    else:
        restored_phase = "idle"
    return store.transition(
        goal_id, Event.UNBLOCK,
        replace(
            status, phase=restored_phase, blocked_on="",
            actions_dispatched=0, last_plan_at=None,
            heal_attempts=heal_attempts, next_heal_at=None,
            env_heal_attempts=(
                status.env_heal_attempts if env_heal_attempts is None
                else env_heal_attempts
            ),
        ),
        expect=status,
    )


async def _heal_give_up(
    goal_id: str, *, store: GoalStore, notifier: Notifier, cap: int, reason: str,
    counter_field: str = "heal_attempts",
) -> None:
    """Park a mechanical block whose heal budget is spent: mark FIRST (the
    sentinel bump one past the cap — a column-only write, the goal stays
    blocked, so this must not be a phase transition; it is what keeps the
    ping to exactly one, the pause_notified pattern), then log, then ONE
    plain owner ping — never through the summarizer LLM.

    ``counter_field`` names the budget being parked, so each brake's sentinel
    lands on its own column."""
    store.update_status_fields(goal_id, **{counter_field: cap + 1})
    store.append_log(
        goal_id, f"auto-recovery gave up after {cap} attempts — {reason}; needs you",
    )
    await _notify(
        notifier, NotifyLevel.OWNER,
        f"🟡 [{goal_id}] auto-recovery gave up after {cap} attempts — "
        f"{reason}; needs you (steer to resume)",
    )


def _declared_caps_for(
    goal: Goal, project_caps: "dict[str, tuple[str, ...]] | None",
) -> "tuple[str, ...]":
    """The environment capabilities this goal's PROJECT declares (spec 030).

    The registry-sourced map wins whenever it carries this goal's project: it
    is read from the project's own checkout once per sweep, so it answers even
    when the GOAL's workspace has never been prepared — which is the whole
    point. Reading only the goal's workspace made a brand-new goal's FIRST
    dispatch fail open on a capability that was already red on record, the
    hole in SC-002's "zero worker sessions until rotated" promise.

    The goal-workspace read stays as the fallback for a goal that belongs to no
    registered project (an ad-hoc goal pointed straight at a checkout). A
    project present in the map with NO capabilities is authoritative — it
    declares none — and must not fall through to a second, divergent read.

    The fallback branch reads the manifest off disk, so async callers must run
    this through ``asyncio.to_thread`` rather than block the heartbeat loop.

    Never raises: an unreadable manifest degrades to "declares nothing", which
    is fail-open by FR-007. A malformed manifest fails loud on the paths that
    own that (prep/doctor), not here.
    """
    project_id = (goal.project_id or "").strip()
    if project_caps and project_id:
        declared = project_caps.get(project_id)
        if declared is not None:
            return tuple(declared)
    try:
        manifest_obj = _manifest.load_manifest(goal.workspace_dir)
    except Exception:  # noqa: BLE001 — see docstring
        return ()
    return tuple(manifest_obj.capabilities) if manifest_obj else ()


async def _block_on_env_cap(
    goal_id: str, status: GoalStatus,
    red_caps: "list[tuple[str, _env_cap.CapProbeResult]]",
    *, store: GoalStore, notifier: Notifier,
    summarize: "ClaudeCaller | None" = None,
    consume_steering: "list[int] | None" = None,
) -> Outcome:
    """Block the goal because one or more required capability probes are red.

    Spec 030 FR-002/FR-003. The block message names every failing capability
    and its remedy so the operator sees ONE story.

    Exactly one owner ping per hold EPISODE, marked by ``env_hold_notified``:
    a re-block that follows an env heal logs but does not ping, so a probe
    oscillating green↔red converges to held + one ping instead of a ping storm
    (spec 030 edge case). The marker is this brake's OWN — gating on
    ``heal_attempts`` swallowed the first ping of a genuine breakage whenever
    the goal had earlier healed an unrelated ``mechanical:prep`` block, which
    is precisely the ping SC-002 promises. It resets on a productive settle
    and when a human vouches, so a later breakage pings again."""
    cap_lines = "; ".join(
        f"{cap_id}: {r.evidence} → {r.remedy}" if r.remedy
        else f"{cap_id}: {r.evidence}"
        for cap_id, r in red_caps
    )
    msg = (
        f"environment capability check failed — dispatching would burn a session: "
        f"{cap_lines}. Waiting for the environment to be fixed; devclaw will "
        "resume automatically when the probe turns green."
    )
    store.append_log(goal_id, f"env-cap hold: {cap_lines}")
    store.transition(
        goal_id, Event.BLOCK,
        replace(status, phase="blocked", blocked_on=msg,
                blocked_kind="mechanical:env", next=""),
        expect=status, consume_steering=consume_steering,
    )
    if not status.env_hold_notified:
        # Mark FIRST, then ping (the pause_notified pattern shared with
        # _heal_give_up): a column-only write on the still-blocked goal, so a
        # crash in the notifier can never re-arm the ping on the next tick.
        store.update_status_fields(goal_id, env_hold_notified=True)
        await _notify(
            notifier, NotifyLevel.OWNER,
            f"🔴 [{goal_id}] dispatch held — environment not ready: {cap_lines}; "
            "devclaw auto-resumes when the probe turns green",
            summarize=summarize,
        )
    return Outcome.BLOCKED


async def _autoheal_env_cap(
    goal_id: str, goal: Goal, status: GoalStatus,
    *, store: GoalStore, notifier: Notifier,
    project_caps: "dict[str, tuple[str, ...]] | None" = None,
) -> "GoalStatus | None":
    """Lift a ``mechanical:env`` block once all required capability probes are
    no longer red.

    Reads persisted probe rows (zero network, zero LLM) — unlike the prep heal
    there is no backoff window, because the recheck IS the row read; the sweep
    runner refreshes the probes before each sweep, so a healed environment
    resumes within ~one sweep (spec 030 US2).

    The heal budget still applies: a probe that flaps green↔red burns one
    attempt per heal and parks for the owner at :data:`ENV_HEAL_CAP` rather
    than cycling forever. That budget is this brake's own
    (``env_heal_attempts``) — see :data:`ENV_HEAL_CAP`. Returns the healed
    status, or ``None`` when the block must remain (still red, or parked)."""
    if status.env_heal_attempts > ENV_HEAL_CAP:
        return None  # parked — the gave-up ping already went out
    if status.env_heal_attempts >= ENV_HEAL_CAP:
        await _heal_give_up(
            goal_id, store=store, notifier=notifier, cap=ENV_HEAL_CAP,
            reason="the required environment capability keeps breaking",
            counter_field="env_heal_attempts",
        )
        return None
    # SAME resolution as the dispatch guard (:func:`_declared_caps_for`) — a
    # hold set from the project registry on an unprepared workspace would
    # otherwise read "declares nothing" here and clear itself every tick,
    # re-dispatching straight back into the red capability.
    declared = await asyncio.to_thread(_declared_caps_for, goal, project_caps)
    if not declared:
        # No declared capabilities → the hold should not have been set; clear it
        # defensively so the goal is not permanently wedged.
        healed = _heal_unblock(goal_id, status, store, heal_attempts=status.heal_attempts)
        store.append_log(goal_id, "env-cap hold cleared (no capabilities declared)")
        return healed
    red = _env_cap.red_caps_for(store, declared, goal.project_id)
    if red:
        return None  # still broken — stay blocked at zero cost
    n = status.env_heal_attempts + 1
    healed = _heal_unblock(
        goal_id, status, store,
        heal_attempts=status.heal_attempts, env_heal_attempts=n,
    )
    store.append_log(
        goal_id,
        f"auto-resumed: required capabilities are green again (heal {n}/{ENV_HEAL_CAP})",
    )
    return healed
