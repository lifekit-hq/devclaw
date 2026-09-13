"""Settle — ONE session, executed and settled (spec 046).

The spine: place the goal branch → run the engine once → classify how the
session ended → materialize the span → run the four gates → deliver → settle
the row WITH its exit. There is no retry loop: a session that could not
finish is ``INTERRUPTED`` (resumed by the next tick), a delivery a gate
refused is ``REFUSED`` (the next session is told why), and everything the
session itself said (DELIVERED / DONE / BLOCKED / NOTHING) is recorded as its
exit for the goal layer to read. A usage limit pauses the account and requeues
the row with its work snapshotted.

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

from .. import config as _config
from ..delivery import deliver_change, delivery_failed
from ..engine import EngineEvent, EngineRequest
from ..engine.workspace import WorkspaceError, prepare_workspace
from ..gates import GateInput, run_gates
from ..loom.limits import classify_failure, pause_seconds
from ..state_store import (
    EXIT_BLOCKED, EXIT_DELIVERED, EXIT_DONE, EXIT_INTERRUPTED, EXIT_NOTHING,
    EXIT_REFUSED, EXIT_REVIEW, TaskKind, _now_ms,
)
from ..task_change import (
    CHANGE as _CHANGE_SOME,
    ERROR as _CHANGE_ERROR,
    NO_CHANGE as _CHANGE_NONE,
    NO_REPO as _CHANGE_NO_REPO,
    ChangeSet,
    base_ref_sync as _base_ref_sync,
    build_paths as _build_paths,
    changed_entries_sync as _changed_entries_sync,
    in_scope_from_text as _in_scope_from_text,
    materialization_message,
    materialize_worktree_sync,
    own_paths_sync as _own_paths_sync,
)
from ..task_git import _git_commit_exists_sync, _git_diff_sync, _git_head_sync, _wip_commit_sync

if TYPE_CHECKING:
    from ..engine import Engine
    from ..state_store import StateStore

TASK_TIMEOUT_S = _config.TASK_TIMEOUT_S
#: usage-limit pause → requeue cycles one task gets before it is failed.
MAX_PAUSE_REQUEUES = 5

#: the session's exit line — last match wins (the runner already turned a
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


def wall_clock_teardown_msg(timeout_s: float = TASK_TIMEOUT_S) -> str:
    return (f"session exceeded the {timeout_s:.0f}s wall-clock timeout "
            f"with no terminal result — sandbox torn down")


# ---- async wrappers (module globals so tests patch them here) --------------

async def _git_diff(host_dir: str, base: str = "", head: str = "",
                    paths: "list[str] | None" = None) -> "str | None":
    if paths is None:
        return await asyncio.to_thread(_git_diff_sync, host_dir, base, head)
    return await asyncio.to_thread(_git_diff_sync, host_dir, base, head, paths)


async def _git_name_status(host_dir: str, base: str, head: str) -> "list[tuple[str, str]]":
    return await asyncio.to_thread(_changed_entries_sync, host_dir, base, head)


async def _base_ref(host_dir: str, base_branch: "str | None") -> "str | None":
    return await asyncio.to_thread(_base_ref_sync, host_dir, base_branch)


async def _own_paths(host_dir: str, base_ref: str, head: str) -> "set[str] | None":
    return await asyncio.to_thread(_own_paths_sync, host_dir, base_ref, head)


async def _materialize_worktree(host_dir: str, base: str, *, task_id: str, message: str) -> dict:
    return await asyncio.to_thread(materialize_worktree_sync, host_dir, base, task_id=task_id, message=message)


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


async def _capture_change(workspace_dir: str, base: str, *, task_id: str, message: str,
                          brief: str = "", base_branch: "str | None" = None) -> ChangeSet:
    """**The** answer to "what did the session change?" (spec 013, #630):
    materialize once — stage everything left and commit it — then render the
    ``base..head`` range less every path the base branch already carries
    (spec 045). Never raises — a crash becomes an ``ERROR`` ChangeSet, which
    the materialize gate fails CLOSED on."""
    try:
        mat = await _materialize_worktree(workspace_dir, base, task_id=task_id, message=message)
    except Exception as err:  # noqa: BLE001
        return ChangeSet(status=_CHANGE_ERROR, base_sha=base, reason=f"{err.__class__.__name__}: {err}")
    if mat["status"] == _CHANGE_ERROR:
        return ChangeSet(status=_CHANGE_ERROR, base_sha=base, reason=mat["reason"])
    head = mat["head"]
    common = {"base_sha": base, "head_sha": head,
              "agent_authored": bool(mat["agent_authored"]), "materialized": bool(mat["materialized"])}
    try:
        diff = await _git_diff(workspace_dir, base, head)
    except Exception as err:  # noqa: BLE001
        diff = None
        mat["reason"] = mat["reason"] or f"{err.__class__.__name__}: {err}"
    if diff is None:
        if mat["status"] == _CHANGE_NO_REPO:
            return ChangeSet(status=_CHANGE_NO_REPO, base_sha=base,
                             reason=mat["reason"] or f"{workspace_dir} is not a git repository")
        return ChangeSet(status=_CHANGE_ERROR, reason=(
            f"git could not diff {base[:8] or '(no base)'}..{head[:8] or '(no head)'} in {workspace_dir}"
        ), **common)
    if mat["status"] == _CHANGE_NO_REPO:
        return ChangeSet(status=_CHANGE_NO_REPO, base_sha=base, diff=diff,
                         reason=mat["reason"] or f"{workspace_dir} is not a git repository")
    status = _CHANGE_SOME if diff.strip() else _CHANGE_NONE
    paths: tuple = ()
    base_ref = ""
    note = ""
    if status == _CHANGE_SOME:
        try:
            entries = await _git_name_status(workspace_dir, base, head)
            base_ref = (await _base_ref(workspace_dir, base_branch)) or ""
            own = (await _own_paths(workspace_dir, base_ref, head)) if base_ref else None
            if own is None:
                note = (f"span unfiltered: no base ref resolved in {workspace_dir}" if not base_ref
                        else f"span unfiltered: no merge-base between {base_ref} and {head[:8]}")
                base_ref = ""
            else:
                kept = [e for e in entries if e[1] in own]
                if len(kept) != len(entries):
                    dropped = [p for _, p in entries if p not in own]
                    note = (f"{len(dropped)} path(s) carried by {base_ref} left out of the span: "
                            + ", ".join(dropped[:8]) + (" …" if len(dropped) > 8 else ""))
                    entries = kept
                    rendered = await _git_diff(workspace_dir, base, head, [p for _, p in kept])
                    if rendered is None:
                        raise RuntimeError(f"git could not render the span's own paths against {base_ref}")
                    diff = rendered
                    if not diff.strip():
                        status = _CHANGE_NONE
            if status == _CHANGE_SOME:
                paths = _build_paths(entries, diff, _in_scope_from_text(brief))
        except Exception as err:  # noqa: BLE001
            return ChangeSet(status=_CHANGE_ERROR, diff=diff, reason=(
                f"the changed paths could not be classified: {err.__class__.__name__}: {err}"
            ), **common)
    return ChangeSet(status=status, diff=diff, paths=paths, base_ref=base_ref, note=note, **common)


def _diff_stats(diff: str) -> "dict | None":
    if not diff or not diff.strip():
        return None
    files = insertions = deletions = binary = 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            files += 1
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            binary += 1
        elif line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    stats = {"files": files, "insertions": insertions, "deletions": deletions}
    if binary:
        stats["binary"] = binary
    return stats


def _change_record(change: ChangeSet) -> dict:
    rec: dict = {"status": change.status, "base_sha": change.base_sha, "head_sha": change.head_sha,
                 "agent_authored": change.agent_authored, "materialized": change.materialized}
    if change.reason:
        rec["reason"] = change.reason
    if change.note:
        rec["note"] = change.note
    stats = _diff_stats(change.diff)
    if stats:
        rec["diff_stats"] = stats
    return rec


class SettleMixin:
    if TYPE_CHECKING:
        _store: StateStore
        _runner: Engine
        _sandbox_owner: str

        def _pump(self) -> None: ...
        def _fire_settle(self) -> None: ...
        def _sandbox_image(self, project_id: Optional[str]): ...
        def _sandbox_sizing(self, project_id: Optional[str]) -> "tuple[Optional[str], Optional[str]]": ...
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
        project_id = row.project_id

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
            sandbox_image=self._sandbox_image(project_id),
            sandbox_memory=self._sandbox_sizing(project_id)[0],
            sandbox_cpus=self._sandbox_sizing(project_id)[1],
            owner_id=self._sandbox_owner,
        )
        try:
            if TASK_TIMEOUT_S > 0:
                result = await asyncio.wait_for(self._runner(request), timeout=TASK_TIMEOUT_S)
            else:
                result = await self._runner(request)
        except asyncio.TimeoutError:
            await _wip_commit(workspace_dir, f"wip(devclaw): interrupted by timeout (session {task_id[:8]})")
            msg = wall_clock_teardown_msg()
            self._store.mark_failed(task_id, msg, exit=EXIT_INTERRUPTED, exit_detail=msg)
            self._check_and_trip_breaker(workspace_dir, task_id)
            return
        except Exception as err:  # noqa: BLE001 — a harness failure
            if await self._maybe_pause(task_id, workspace_dir, str(err)):
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
            await _wip_commit(workspace_dir, f"wip(devclaw): blocked (session {task_id[:8]})")
            self._store.mark_done(task_id, json.dumps(result), exit=EXIT_BLOCKED, exit_detail=reason)
            return
        if status != "ok":
            text = str(result.get("error") or "unknown error")
            if status == "rate_limited" and result.get("retry_after"):
                text = f"rate limit; retry-after: {result['retry_after']}s"
            if await self._maybe_pause(task_id, workspace_dir, text):
                return
            await _wip_commit(workspace_dir, f"wip(devclaw): interrupted (session {task_id[:8]})")
            self._store.mark_failed(task_id, text[:2000], exit=EXIT_INTERRUPTED, exit_detail=text[:400])
            self._check_and_trip_breaker(workspace_dir, task_id)
            return

        if kind == "review_repository":
            self._store.mark_done(task_id, json.dumps(result), exit=EXIT_REVIEW, exit_detail="")
            return

        # 4. the gates, over the one materialized span
        gate_input = GateInput(
            workspace_dir=workspace_dir, verify=result.get("verify"),
            change_fn=lambda: _capture_change(
                workspace_dir, pre_run_sha, task_id=task_id,
                message=materialization_message(task_id), brief=goal,
            ),
        )
        verdict = await run_gates(gate_input)
        if verdict is not None:
            if verdict.gate_id == "verify":
                # the session's own unfinished work: keep it, resume next tick
                await _wip_commit(workspace_dir, f"wip(devclaw): verify red (session {task_id[:8]})")
                self._store.mark_failed(task_id, verdict.reason, exit=EXIT_INTERRUPTED,
                                        exit_detail=verdict.reason[:400])
            else:
                self._store.mark_failed(task_id, verdict.reason, exit=EXIT_REFUSED,
                                        exit_detail=verdict.reason[:600])
            self._check_and_trip_breaker(workspace_dir, task_id)
            return
        change = await gate_input.change()
        result["change"] = _change_record(change)
        exit_word, exit_detail = parse_exit_line(agent_output)

        # 5. deliver the judged span (nothing to deliver ⇒ a first-class outcome)
        if not change.is_change:
            result["no_change"] = True
        if not change.is_change or not row.deliver:
            self._store.mark_done(task_id, json.dumps(result),
                                  exit=exit_word or EXIT_NOTHING, exit_detail=exit_detail)
            return
        try:
            delivery = await deliver_change(
                workspace_dir=workspace_dir, task_id=task_id, goal=goal, kind=kind,
                verify=result.get("verify"), target_branch=row.target_branch,
                judged_head=change.head_sha, agent_authored=change.agent_authored,
            )
            failure = delivery_failed(delivery)
        except Exception as err:  # noqa: BLE001
            delivery, failure = {}, f"{err.__class__.__name__}: {err}"
        result["delivery"] = delivery
        pr_url = delivery.get("pr_url")
        if delivery.get("no_agent_commit"):
            self._store.append_event(
                task_id=task_id, type="delivery.no_agent_commit", source="devclaw",
                payload_json=json.dumps({"reason": "agent authored no commit; workspace captured as machine snapshot"}),
            )
        sys.stderr.write(f"task-queue: delivery task={task_id}: {delivery}\n")
        if failure is not None and not pr_url:
            self._store.mark_failed(task_id, f"delivery failed: {failure}", result_json=json.dumps(result),
                                    exit=EXIT_INTERRUPTED, exit_detail=f"delivery failed: {failure}"[:400])
            self._check_and_trip_breaker(workspace_dir, task_id)
            return
        self._store.mark_done(task_id, json.dumps(result), pr_url=pr_url,
                              exit=exit_word or EXIT_DELIVERED, exit_detail=exit_detail)

    async def _maybe_pause(self, task_id: str, workspace_dir: str, text: str) -> bool:
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
        snapshot = await _wip_commit(workspace_dir, f"wip(devclaw): interrupted by usage limit (session {task_id[:8]})")
        self._store.requeue_task(task_id)
        sys.stderr.write(f"task-queue: session {task_id} hit {cls.kind.value} — pausing dispatch "
                         f"~{backoff}s, requeued (wip: {snapshot})\n")
        return True


__all__ = ["SettleMixin", "parse_exit_line", "wall_clock_teardown_msg", "MAX_PAUSE_REQUEUES",
           "EXIT_DELIVERED", "EXIT_DONE", "EXIT_REVIEW"]
