"""The tick rule — the whole control plane (spec 046, section 1).

Every ~15 minutes, for every open goal: read the world, compare it to what
the last session was given, and either spawn ONE session, run the done-gate,
close, or do nothing. The host routes on facts the world holds (PR state,
CI, the newest instruction and the newest devclaw record); it never stores
why the world changed and never retries a stop.

Pure over its context: every read and verb is injected, so the tick is
testable with zero subprocesses and the idle path costs zero sessions.
"""

from __future__ import annotations

import datetime as _dt
import sys
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from .. import config as _config
from ..engine.workspace import ensure_goal_checkout, goal_checkout_dir, prepare_workspace, remove_goal_checkout
from ..state_store import (
    EXIT_BLOCKED, EXIT_DONE, EXIT_INTERRUPTED, EXIT_REFUSED, EXIT_REVIEW, Goal, StateStore, Task,
)
from . import donegate as _donegate
from . import github as _gh
from .notify import Notifier
from .prompts import review_brief, session_brief
from .world import World, WorldReader

PostComment = Callable[[str, int, str], Awaitable[str]]
Merge = Callable[[str, str], Awaitable[tuple[str, str]]]

#: outcomes after which the goal HOLDS its project's lane this tick
SPAWNING = frozenset({"spawned", "resumed", "gate", "running"})


@dataclass
class TickContext:
    store: StateStore
    queue: object  # TaskQueue — typed loosely so the goal layer stays above the queue
    world: WorldReader
    notifier: Notifier
    post_comment: PostComment = _gh.post_comment
    merge: Merge = _gh.squash_merge
    now_ms: Callable[[], int] = lambda: int(_dt.datetime.now(_dt.timezone.utc).timestamp() * 1000)

    def log(self, goal_id: str, line: str) -> None:
        sys.stderr.write(f"goal-layer: {goal_id}: {line}\n")


def _utc_day_start_ms(now_ms: int) -> int:
    day = _dt.datetime.fromtimestamp(now_ms / 1000, tz=_dt.timezone.utc).date()
    return int(_dt.datetime(day.year, day.month, day.day, tzinfo=_dt.timezone.utc).timestamp() * 1000)


def _thread_number(goal: Goal, world: World) -> int:
    """Where devclaw records go: the PR when it exists, else the first issue."""
    if world.pr.number:
        return world.pr.number
    return goal.issues[0] if goal.issues else 0


async def _ping(ctx: TickContext, text: str) -> None:
    try:
        await ctx.notifier.send(text)
    except Exception:  # noqa: BLE001 — a ping never breaks a tick
        pass


async def _spawn(ctx: TickContext, goal: Goal, world: World, kind: str, brief: str) -> None:
    """Create the row inside one transaction with the fingerprint it was given."""
    project_ws = goal.workspace_dir
    checkout = goal_checkout_dir(project_ws, goal.id)
    try:
        import os
        if not os.path.isdir(os.path.join(project_ws, ".git")):
            await prepare_workspace(project_ws, goal.repo_url)
        await ensure_goal_checkout(project_ws, goal.repo_url, goal.id)
    except Exception as exc:  # noqa: BLE001 — the queue's prep will say so loudly
        ctx.log(goal.id, f"checkout seeding degraded: {exc}")
    with ctx.store.transaction():
        ctx.queue.submit(  # type: ignore[attr-defined]
            kind=kind, workspace_dir=checkout, goal=brief, verify_cmd=None,
            deliver=(kind != "review_repository"), parent_goal_id=goal.id,
            target_branch=goal.branch, project_id=goal.project_id, pump=False,
        )
        ctx.store.set_goal_last_seen(goal.id, world.fingerprint_json())
    ctx.queue.pump()  # type: ignore[attr-defined]


async def _close(ctx: TickContext, goal: Goal, outcome: str, note: str) -> str:
    if ctx.store.close_goal(goal.id, outcome):
        remove_goal_checkout(goal.workspace_dir, goal.id)
        ctx.log(goal.id, f"closed ({outcome}): {note}")
        await _ping(ctx, f"{'✅' if outcome == 'achieved' else '⏹'} {goal.id} {outcome} — {note}")
    return "closed"


async def _block(ctx: TickContext, goal: Goal, world: World, *, task_id: str, head: str,
                 kind: str, text: str, default: str = "") -> str:
    number = _thread_number(goal, world)
    body = _donegate.render_block(task_id=task_id, head=head, kind=kind, text=text, default=default)
    url = await ctx.post_comment(goal.repo_url, number, body) if number else ""
    ctx.log(goal.id, f"blocked ({kind}): {text[:160]}")
    await _ping(ctx, f"⛔ {goal.id} stopped — {kind}: {text[:300]}"
                    + (f"\n{url}" if url else "") + "\nReply on the thread mentioning the bot, or decide().")
    return "blocked"


async def tick_goal(goal: Goal, ctx: TickContext) -> str:
    """One goal, one tick. Returns a short outcome word for the log."""
    store = ctx.store
    if store.goal_has_live_task(goal.id):
        return "running"
    open_, why = ctx.queue.dispatch_open()  # type: ignore[attr-defined]
    if not open_:
        return "held"
    now = ctx.now_ms()
    if store.count_goal_tasks_since(goal.id, _utc_day_start_ms(now)) >= _config.sessions_per_day():
        return "capped"

    world = await ctx.world.read(goal)
    if world.pr.state == "unknown":
        ctx.log(goal.id, f"world unreadable: {world.pr.ci_detail}")
        return "unreadable"
    if world.pr.state == "merged":
        return await _close(ctx, goal, "achieved", f"PR merged: {world.pr.url}")
    last: Optional[Task] = store.latest_task_for_goal(goal.id)
    head = world.pr.head_sha

    # a session that ended BLOCKED: post its question once, then wait
    if last is not None and last.exit == EXIT_BLOCKED and not world.block_exists(task_id=last.id):
        question, default = _donegate.block_default(last.exit_detail or "")
        return await _block(ctx, goal, world, task_id=last.id, head=head, kind="session blocked",
                            text=question or "(no question stated)", default=default)
    if world.blocked:
        return "blocked"

    # a red CI on a delivered head is an environment gap: stop, never retry
    if world.pr.state == "open" and world.pr.ci == "red" and not world.block_exists(head=head):
        detail = world.pr.ci_detail
        logs = "\n\n".join(f"**{n}**\n```\n{t[-2500:]}\n```" for n, t in world.pr.failing_logs[:3])
        return await _block(ctx, goal, world, task_id=last.id if last else "", head=head,
                            kind="red CI after a green local verify",
                            text=f"{detail}\n\n{logs}".strip())

    if last is not None and last.exit == EXIT_INTERRUPTED:
        await _spawn(ctx, goal, world, "implement_feature", session_brief(goal, world, last))
        return "resumed"

    if last is not None and last.exit == EXIT_REVIEW:
        return await _settle_review(ctx, goal, world, last)

    if last is not None and last.exit == EXIT_DONE:
        if world.pr.state == "none":
            return await _block(ctx, goal, world, task_id=last.id, head="", kind="DONE without a PR",
                                text="the session proposed DONE but nothing was ever delivered")
        if world.pr.state == "open" and world.pr.ci == "pending":
            return "ci pending"
        if world.pr.state == "open" and world.pr.ci in ("green", "no_workflows"):
            verdicts = world.verdicts_for_head(head)
            unreadable = [v for v in verdicts if v.field("unreadable") == "1"]
            if not verdicts or (len(verdicts) == len(unreadable) == 1):
                await _spawn(ctx, goal, world, "review_repository", review_brief(goal, world))
                return "gate"
            if unreadable and len(unreadable) >= 2:
                return await _block(ctx, goal, world, task_id=last.id, head=head,
                                    kind="done-gate unreadable twice",
                                    text="two reviews produced no readable verdict — a devclaw defect")
            achieved = [v for v in verdicts if v.field("achieved") == "1"]
            if achieved:
                return await _merge(ctx, goal, world)
        # conflicting / infra_broken / red-already-blocked: the world moved — fall through

    if last is not None and last.exit == EXIT_REFUSED and (
        goal.last_seen_json == world.fingerprint_json()
    ):
        # the world did not move, but the refusal is a fact the next session
        # must act on (revert the gate-input edit) — spawn once with it
        prior = store.list_tasks(parent_goal_id=goal.id, limit=2)
        if len(prior) >= 2 and prior[1].exit == EXIT_REFUSED and (prior[1].exit_detail == last.exit_detail):
            return await _block(ctx, goal, world, task_id=last.id, head=head, kind="delivery refused twice",
                                text=last.exit_detail or "")
        await _spawn(ctx, goal, world, "implement_feature", session_brief(goal, world, last))
        return "resumed"

    if goal.last_seen_json == world.fingerprint_json():
        return "idle"
    await _spawn(ctx, goal, world, "implement_feature", session_brief(goal, world, last))
    return "spawned"


async def _settle_review(ctx: TickContext, goal: Goal, world: World, review: Task) -> str:
    """The review session finished: record its verdict on the PR once, then
    act on it — merge and close, or stop for the owner."""
    head = world.pr.head_sha
    existing = [v for v in world.verdicts_for_head(head) if v.field("task") == review.id]
    if not existing:
        import json as _json
        try:
            result = _json.loads(review.result_json or "{}")
        except ValueError:
            result = {}
        verdict = _donegate.parse_verdict(str(result.get("agent_output") or ""))
        if review.status != "done":
            verdict = _donegate.Verdict(False, unreadable=True, raw_error=review.error or "review session failed")
        body = _donegate.render_verdict(verdict, head=head, task_id=review.id)
        if world.pr.number:
            await ctx.post_comment(goal.repo_url, world.pr.number, body)
        ctx.log(goal.id, f"done-gate: achieved={verdict.achieved} unreadable={verdict.unreadable}")
        if verdict.unreadable:
            return "gate unreadable"
        if not verdict.achieved:
            await _ping(ctx, f"⛔ {goal.id} done-gate refused — {verdict.summary[:300]}\n{world.pr.url}")
            return "blocked"
        return await _merge(ctx, goal, world)
    v = existing[-1]
    if v.field("achieved") == "1":
        return await _merge(ctx, goal, world)
    return "blocked" if v.field("unreadable") != "1" else "gate unreadable"


async def _merge(ctx: TickContext, goal: Goal, world: World) -> str:
    if world.pr.state == "conflicting":
        return "conflicting"  # the fingerprint moved: the next tick spawns the resolution
    if world.pr.state == "open" and world.pr.ci not in ("green", "no_workflows"):
        return "ci pending" if world.pr.ci == "pending" else "merge held"
    outcome, detail = await ctx.merge(goal.repo_url, world.pr.url)
    if outcome == "merged":
        return await _close(ctx, goal, "achieved", f"merged {world.pr.url}")
    ctx.log(goal.id, f"merge {outcome}: {detail}")
    return f"merge {outcome}"
