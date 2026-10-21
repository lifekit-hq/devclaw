"""Settle — ONE session, executed and settled (spec 046).

The spine: place the goal branch → run the engine once (the session ships its
own branch through no-mistakes, #953) → classify how the session ended →
settle the row WITH its exit. There is no retry loop: a session that could
not finish — a red verify or a hand-off that did not ship — is ``INTERRUPTED``
(its successor starts a fresh session on the reset goal branch; ``--resume``
is only for a quota-paused row), and everything the session itself said
(DELIVERED / DONE / BLOCKED / NOTHING) is recorded as its exit for the goal
layer to read. A usage limit pauses the account and requeues the row with its
work snapshotted.

A mixin on the TaskQueue instance; never imports ``devclaw.task_queue``.
"""

from __future__ import annotations

import asyncio
import functools
import json
import re
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from ..engine import EngineEvent, EngineRequest
from ..engine.workspace import WorkspaceError, prepare_workspace
from ..loom.limits import classify_failure, pause_seconds
from ..state_store import (
    EXIT_BLOCKED, EXIT_DELIVERED, EXIT_DONE, EXIT_INTERRUPTED, EXIT_NOTHING,
    EXIT_REVIEW, TaskKind, _now_ms,
)
from ..task_git import (
    _git_commit_exists_sync, _git_head_sync, _git_tracked_changes_sync, _wip_commit_sync,
)

if TYPE_CHECKING:
    from ..engine import Engine
    from ..state_store import StateStore

#: usage-limit pause → requeue cycles one task gets before it is failed.
MAX_PAUSE_REQUEUES = 5

#: the session's exit line — last match wins (the engine already turned a
#: ``BLOCKED:`` line into a blocked result; this reads the other three).
_EXIT_LINE_RE = re.compile(
    r"^[ \t>#*_-]*(DELIVERED|DONE|BLOCKED|NOTHING):[ \t]*(.*?)[ \t*_]*$", re.MULTILINE
)


def parse_exit_line(text: Optional[str]) -> "tuple[str | None, str]":
    """``(exit, detail)`` from a session's final message, or ``(None, "")``."""
    if not text:
        return None, ""
    found = _EXIT_LINE_RE.findall(text)
    if not found:
        return None, ""
    word, detail = found[-1]
    return word, detail.strip()


# ---- async wrappers (module globals so tests patch them here) --------------

async def _git_head(host_dir: str) -> str:
    return await asyncio.to_thread(_git_head_sync, host_dir)


async def _git_commit_exists(host_dir: str, sha: str) -> bool:
    return await asyncio.to_thread(_git_commit_exists_sync, host_dir, sha)


async def _wip_commit(host_dir: str, message: str) -> str:
    """Best-effort: a snapshot hiccup is a reason string, never an exception
    that could strand a row as ``running``."""
    try:
        return await asyncio.to_thread(_wip_commit_sync, host_dir, message)
    except Exception as err:  # noqa: BLE001
        return f"crashed: {err.__class__.__name__}: {err}"


async def _snapshot(kind: str, host_dir: str, message: str) -> str:
    """A read-only review has no work to snapshot, and a WIP commit would move
    the HEAD the read-only check compares against."""
    return "" if kind == "review_repository" else await _wip_commit(host_dir, message)


async def _review_touched(host_dir: str, base: str) -> str:
    """Why a read-only review left its worktree changed, or ``""``. Fails
    CLOSED: a tree git cannot read is not proof the review stayed read-only."""
    head = await _git_head(host_dir)
    if not base or head != base:
        return f"the review session moved HEAD ({base[:10] or '?'} -> {head[:10] or '?'}); a review is read-only"
    changes = await asyncio.to_thread(_git_tracked_changes_sync, host_dir)
    if changes is None:
        return "git could not read the review's worktree; a review is read-only"
    if changes:
        return f"the review session modified tracked files; a review is read-only:\n{changes[:600]}"
    return ""


def _verify_failure(verify: "dict | None") -> "str | None":
    """Why the session's own verify run is red, or None — a red run never ships."""
    if not verify or not verify.get("ran") or verify.get("passed"):
        return None
    cmd = verify.get("cmd", "")
    head = (f"verify timed out: `{cmd}`" if verify.get("timed_out")
            else f"verify failed (exit {verify.get('exit_code')}): `{cmd}`")
    out = (verify.get("output") or "").strip()
    return f"{head}\n{out[-1500:]}" if out else head


class SettleMixin:
    if TYPE_CHECKING:
        _store: StateStore
        _runner: Engine

        def _pump(self) -> None: ...
        def _fire_settle(self) -> None: ...
        def _check_and_trip_breaker(self, workspace_dir: str, task_id: str) -> None: ...

    def _append_task_event(self, task_id: str, event: EngineEvent) -> None:
        try:
            self._store.append_event(
                task_id=task_id, type=event.type, source=event.source,
                payload_json=json.dumps(event.payload),
                ts=int(event.ts) if isinstance(event.ts, (int, float)) else _now_ms(),
            )
        except Exception as err:  # noqa: BLE001 — event writes never crash the run
            sys.stderr.write(f"task-queue: append_event failed task={task_id}: {err}\n")

    async def _execute(self, task_id: str, kind: TaskKind, workspace_dir: str, goal: str) -> None:
        try:
            await self._execute_inner(task_id, kind, workspace_dir, goal)
        finally:
            self._fire_settle()
            self._pump()

    async def _execute_inner(self, task_id: str, kind: TaskKind, workspace_dir: str, goal: str) -> None:
        row = self._store.get_task(task_id)
        if row is None:
            return
        resumed = row.pause_count > 0

        # 1. place the goal branch (a pause-resume keeps the tree as it is)
        if row.target_branch and not resumed:
            try:
                await prepare_workspace(workspace_dir, branch=row.target_branch)
            except WorkspaceError as exc:
                self._store.mark_failed(task_id, f"workspace prep failed: {exc}",
                                        exit=EXIT_INTERRUPTED, exit_detail=f"workspace prep failed: {exc}")
                self._check_and_trip_breaker(workspace_dir, task_id)
                return
            except Exception as exc:  # noqa: BLE001
                self._store.mark_failed(task_id, f"workspace prep failed: {exc!r}",
                                        exit=EXIT_INTERRUPTED, exit_detail=f"workspace prep failed: {exc!r}")
                self._check_and_trip_breaker(workspace_dir, task_id)
                return

        # 2. the span's base: captured after placement; reused on a resume
        pre_run_sha = ""
        if resumed and row.pre_run_sha and await _git_commit_exists(workspace_dir, row.pre_run_sha):
            pre_run_sha = row.pre_run_sha
        if not pre_run_sha:
            pre_run_sha = await _git_head(workspace_dir)
            if pre_run_sha:
                self._store.set_task_pre_run_sha(task_id, pre_run_sha)

        # 3. run ONE session
        brief = goal if not resumed else (
            "[Resuming after an interruption] A previous session was cut off mid-work; "
            "its partial progress is in the tree (possibly as a 'wip(devclaw)' commit). "
            "Inspect `git status` and `git log` and CONTINUE from what is there.\n\n" + goal
        )
        request = EngineRequest(
            kind=kind, workspace_dir=workspace_dir, goal=brief,
            on_event=functools.partial(self._append_task_event, task_id),
            verify_cmd=row.verify_cmd,
            resume_session_id=row.session_id if resumed else None,
            on_session=functools.partial(self._store.set_task_session_id, task_id),
            hand_off=row.deliver, closes=self._goal_issues(row.parent_goal_id),
        )
        try:
            result = await self._runner(request)
        except Exception as err:  # noqa: BLE001 — a harness failure
            if await self._maybe_pause(task_id, kind, workspace_dir, str(err)):
                return
            msg = f"engine error: {err}"
            self._store.mark_failed(task_id, msg[:2000], exit=EXIT_INTERRUPTED, exit_detail=msg[:400])
            self._check_and_trip_breaker(workspace_dir, task_id)
            return

        status = result.get("status")
        agent_output = str(result.get("agent_output") or "")
        if status == "blocked":
            reason = (result.get("reason") or "").strip() or "no reason given"
            if result.get("block_kind") == "env" and result.get("block_item"):
                reason = f"env — {result['block_item']}"
            await _snapshot(kind, workspace_dir, f"wip(devclaw): blocked (session {task_id[:8]})")
            self._store.mark_done(task_id, json.dumps(result), exit=EXIT_BLOCKED, exit_detail=reason)
            return
        if status != "ok":
            text = str(result.get("error") or "unknown error")
            if status == "rate_limited" and result.get("retry_after"):
                text = f"rate limit; retry-after: {result['retry_after']}s"
            if await self._maybe_pause(task_id, kind, workspace_dir, text):
                return
            await _snapshot(kind, workspace_dir, f"wip(devclaw): interrupted (session {task_id[:8]})")
            self._store.mark_failed(task_id, text[:2000], exit=EXIT_INTERRUPTED, exit_detail=text[:400])
            self._check_and_trip_breaker(workspace_dir, task_id)
            return

        if kind == "review_repository":
            # read-only is checked, not trusted: a review that committed or
            # edited a tracked file judged its own work, not the delivered head
            touched = await _review_touched(workspace_dir, pre_run_sha)
            if touched:
                self._store.mark_failed(task_id, touched, exit=EXIT_REVIEW, exit_detail=touched[:400],
                                        result_json=json.dumps(result))
                return
            self._store.mark_done(task_id, json.dumps(result), exit=EXIT_REVIEW, exit_detail="")
            return

        # 4. a red verify never ships: snapshot the session's own unfinished
        # work; the successor starts fresh
        red = _verify_failure(result.get("verify"))
        if red is not None:
            await _wip_commit(workspace_dir, f"wip(devclaw): verify red (session {task_id[:8]})")
            self._store.mark_failed(task_id, red, exit=EXIT_INTERRUPTED, exit_detail=red[:400])
            self._check_and_trip_breaker(workspace_dir, task_id)
            return

        # 5. the engine shipped the branch through no-mistakes (or there was nothing to ship)
        pr_url = result.get("pr_url")
        exit_word, exit_detail = parse_exit_line(agent_output)
        self._store.mark_done(task_id, json.dumps(result), pr_url=pr_url,
                              exit=exit_word or (EXIT_DELIVERED if pr_url else EXIT_NOTHING),
                              exit_detail=exit_detail)

    def _goal_issues(self, goal_id: "str | None") -> "tuple[int, ...]":
        goal = self._store.get_goal(goal_id) if goal_id else None
        return tuple(goal.issues) if goal is not None else ()

    async def _maybe_pause(self, task_id: str, kind: str, workspace_dir: str, text: str) -> bool:
        """A usage / auth / provider-outage failure pauses the ACCOUNT and
        requeues the row with its work snapshotted. True iff it paused."""
        cls = classify_failure(text, now_utc=datetime.now(timezone.utc))
        if not cls.is_pausing:
            return False
        backoff = pause_seconds(cls.retry_after_s, stated=cls.stated, kind=cls.kind)
        self._store.set_global_pause(_now_ms() + backoff * 1000, f"{cls.kind.value}: {text[:160]}")
        task = self._store.get_task(task_id)
        if task is not None and task.pause_count >= MAX_PAUSE_REQUEUES:
            msg = f"exceeded {MAX_PAUSE_REQUEUES} usage-limit pauses; last: {text[:300]}"
            self._store.mark_failed(task_id, msg, exit=EXIT_INTERRUPTED, exit_detail=msg[:400])
            self._check_and_trip_breaker(workspace_dir, task_id)
            return True
        snapshot = await _snapshot(kind, workspace_dir, f"wip(devclaw): interrupted by usage limit (session {task_id[:8]})")
        self._store.requeue_task(task_id)
        sys.stderr.write(f"task-queue: session {task_id} hit {cls.kind.value} — pausing dispatch "
                         f"~{backoff}s, requeued (wip: {snapshot})\n")
        return True


__all__ = ["SettleMixin", "parse_exit_line", "MAX_PAUSE_REQUEUES",
           "EXIT_DELIVERED", "EXIT_DONE", "EXIT_REVIEW"]
