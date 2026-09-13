"""The goal layer's facade + heartbeat (spec 046).

Create, cancel, decide, read. The loop runs the tick rule over every open
goal, one session per project at a time, and fires the self-deploy edge.
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Optional

from .. import config as _config
from ..engine.workspace import remove_goal_checkout
from ..state_store import Goal, StateStore, _now_ms
from . import github as _gh
from . import self_deploy as _self_deploy
from .notify import HttpNotifier, Notifier, NullNotifier
from .tick import SPAWNING, TickContext, tick_goal
from .world import WorldReader


class GoalService:
    def __init__(self, queue, store: StateStore, *, project_registry=None,
                 notifier: Optional[Notifier] = None, world_reader: Optional[WorldReader] = None,
                 post_comment=None, merge=None, tick_seconds: Optional[int] = None) -> None:
        self._queue = queue
        self._store = store
        self._registry = project_registry
        if notifier is None:
            url = _config.goal_notify_url()
            notifier = HttpNotifier(url) if url else NullNotifier()
        self._notifier = notifier
        if world_reader is None:
            from ..probes import green_credentials
            world_reader = WorldReader(credentials=green_credentials)
        self._ctx = TickContext(
            store=store, queue=queue, world=world_reader, notifier=notifier,
            **({"post_comment": post_comment} if post_comment else {}),
            **({"merge": merge} if merge else {}),
        )
        self._tick_seconds = tick_seconds or _config.goal_tick_seconds()
        self._wake: Optional[asyncio.Event] = None
        self._loop_task: Optional[asyncio.Task] = None
        self.started_at_ms: Optional[int] = None
        self.last_tick_at_ms: Optional[int] = None

    @property
    def tick_seconds(self) -> int:
        return self._tick_seconds

    # ---- heartbeat ----------------------------------------------------

    def start(self) -> None:
        if self._loop_task is not None and not self._loop_task.done():
            return
        self._wake = asyncio.Event()
        self.started_at_ms = _now_ms()
        self._loop_task = asyncio.ensure_future(self._loop())

    def poke(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def _loop(self) -> None:
        assert self._wake is not None
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._tick_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                await self.tick_all()
            except Exception as exc:  # noqa: BLE001 — a tick crash must not kill the loop
                sys.stderr.write(f"goal-layer: tick crashed: {exc!r}\n")
            try:
                await _self_deploy.maybe_trigger(self._store, now_ms=_now_ms())
            except Exception as exc:  # noqa: BLE001
                sys.stderr.write(f"goal-layer: self-deploy edge crashed: {exc!r}\n")
            try:
                self._store.maybe_prune_events()
            except Exception:  # noqa: BLE001
                pass

    async def tick_all(self) -> dict[str, str]:
        """Every open goal, one project lane at a time. Zero sessions unless
        a goal's world moved."""
        self.last_tick_at_ms = _now_ms()
        outcomes: dict[str, str] = {}
        await self._pause_ping()
        by_project: dict[str, list[Goal]] = {}
        for g in self._store.list_goals(open_only=True):
            by_project.setdefault(g.project_id, []).append(g)
        for project_id, goals in by_project.items():
            if any(self._store.goal_has_live_task(g.id) for g in goals):
                for g in goals:
                    outcomes[g.id] = "running" if self._store.goal_has_live_task(g.id) else "lane busy"
                continue
            for g in goals:
                try:
                    outcome = await tick_goal(g, self._ctx)
                except Exception as exc:  # noqa: BLE001 — one goal's crash never stops the sweep
                    outcome = f"error: {exc!r}"
                    sys.stderr.write(f"goal-layer: {g.id}: tick error: {exc!r}\n")
                outcomes[g.id] = outcome
                if outcome in SPAWNING:
                    for other in goals:
                        outcomes.setdefault(other.id, "lane busy")
                    break
        return outcomes

    async def _pause_ping(self) -> None:
        until, reason = self._store.global_pause()
        if until and until > _now_ms():
            if not self._store.pause_notified():
                self._store.set_pause_notified(True, reason.split(":")[0])
                await self._ctx.notifier.send(
                    f"⏸ devclaw paused ({reason[:160]}) — resumes on its own when the limit lifts"
                    + ("; re-login needed" if reason.startswith("auth") else "")
                )
        elif self._store.pause_notified():
            self._store.set_pause_notified(False)

    # ---- verbs --------------------------------------------------------

    async def create_goal(self, goal_id: str, *, project_id: str, workspace_dir: str,
                          repo_url: str, issues: list[int], objective: str = "") -> dict:
        if self._store.get_goal(goal_id) is not None:
            raise FileExistsError(goal_id)
        if not issues:
            raise ValueError("a goal needs at least one issue — the issue is the contract")
        if not repo_url:
            raise ValueError("the project has no repo_url — a goal needs a GitHub repository")
        for other in self._store.list_goals(open_only=True):
            overlap = sorted(set(other.issues) & set(issues))
            if other.repo_url == repo_url and overlap:
                raise ValueError(f"issue(s) {overlap} already belong to open goal {other.id!r} — cancel it first")
        titles = []
        for n in issues:
            issue = await self._ctx.world.issue(repo_url, n)  # raises IssueError loudly
            titles.append(issue.title)
        goal = self._store.create_goal(
            id=goal_id, project_id=project_id, workspace_dir=workspace_dir, repo_url=repo_url,
            objective=objective or "; ".join(t for t in titles if t)[:300],
            issues=list(issues), branch=f"goal/{goal_id}",
        )
        if self._registry is not None:
            try:
                self._registry.link_goal(project_id, goal_id)
            except Exception:  # noqa: BLE001 — advisory link
                pass
        self.poke()
        return self.get_goal(goal.id)

    def cancel_goal(self, goal_id: str) -> dict:
        goal = self._store.get_goal(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if not goal.open:
            return {**self.get_goal(goal_id), "note": f"already {goal.outcome}"}
        for t in self._store.list_tasks(parent_goal_id=goal_id, limit=5):
            if t.status in ("pending", "running"):
                self._queue.cancel_task(t.id)
        self._store.close_goal(goal_id, "cancelled")
        remove_goal_checkout(goal.workspace_dir, goal_id)
        return self.get_goal(goal_id)

    async def decide(self, goal_id: str, text: str) -> dict:
        """The owner's verb: an instruction on the goal's thread, recorded as
        a Decision. The next tick sees a newer instruction and spawns."""
        goal = self._store.get_goal(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        if not goal.open:
            raise ValueError(f"goal {goal_id!r} is {goal.outcome}")
        text = (text or "").strip()
        if not text:
            raise ValueError("decide needs text")
        mention = _config.mention()
        body = text if mention.lower() in text.lower() else f"{mention} {text}"
        number = goal.issues[0] if goal.issues else 0
        url = await self._ctx.post_comment(goal.repo_url, number, body) if number else ""
        if number and not url:
            raise RuntimeError(f"could not post the decision on issue #{number} — is gh authenticated?")
        decision = self._store.record_decision(goal_id, text, url)
        self.poke()
        return {"goal": self.get_goal(goal_id), "decision": decision.to_dict()}

    # ---- reads --------------------------------------------------------

    def get_goal(self, goal_id: str) -> dict:
        goal = self._store.get_goal(goal_id)
        if goal is None:
            raise KeyError(goal_id)
        tasks = self._store.list_tasks(parent_goal_id=goal_id, limit=10)
        last = tasks[0] if tasks else None
        try:
            seen = json.loads(goal.last_seen_json) if goal.last_seen_json else None
        except ValueError:
            seen = None
        return {
            **goal.to_dict(),
            "state": _state_word(goal, last),
            "lastSeen": seen,
            "lastSession": _task_view(last) if last else None,
            "sessions": [_task_view(t) for t in tasks],
            "decisions": [d.to_dict() for d in self._store.list_decisions(goal_id)],
        }

    def list_goals(self) -> list[dict]:
        out = []
        for g in self._store.list_goals():
            last = self._store.latest_task_for_goal(g.id)
            out.append({**g.to_dict(), "state": _state_word(g, last),
                        "lastSession": _task_view(last) if last else None})
        return out

    def status(self) -> dict:
        until, reason = self._store.global_pause()
        hold_on, hold_reason = self._store.operator_hold()
        return {
            "goals": self.list_goals(),
            "running": self._store.count_running(),
            "pause": {"until_ms": until, "reason": reason} if until else None,
            "operatorHold": {"on": hold_on, "reason": hold_reason},
            "schedule": self._store.get_run_schedule(),
            "lastTickAt": self.last_tick_at_ms,
            "tickSeconds": self._tick_seconds,
        }


def _task_view(t) -> dict:
    return {"id": t.id, "kind": t.kind, "status": t.status, "exit": t.exit,
            "exitDetail": t.exit_detail, "prUrl": t.pr_url, "createdAt": t.created_at,
            "completedAt": t.completed_at}


def _state_word(goal: Goal, last) -> str:
    if not goal.open:
        return goal.outcome or "closed"
    if last is None:
        return "new"
    if last.status in ("pending", "running"):
        return "running"
    if last.exit == "BLOCKED":
        return "blocked"
    if last.exit == "DONE":
        return "proposed done"
    if last.exit == "INTERRUPTED":
        return "interrupted"
    return "waiting"


__all__ = ["GoalService", "_gh"]
