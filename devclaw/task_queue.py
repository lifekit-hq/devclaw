"""Async task executor — DB-driven, crash-safe, heartbeat-paced.

``submit()`` creates a row; engine runs happen in the background and the
store is flipped when they settle. Single-writer-to-state by design — only
this queue mutates task rows. Scheduling is reconciled from DB state (the
``running`` rows), never from in-memory counters, so a restart resumes work:
:meth:`recover` resets orphaned ``running`` rows and the next pump relaunches.

**Cheap-idle guard:** every pump first asks the store "is there any work?" and
returns immediately if not.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from typing import Callable, Optional

from . import config as _config
from .dispatch_gate import operator_block
from .engine import Engine
from .engine.claude import run_claude
from .queue.admission import AdmissionMixin
from .queue.settle import SettleMixin, _capture_change, _diff_stats  # noqa: F401 — re-exported for tests
from .state_store import EXIT_INTERRUPTED, StateStore, TaskKind, _now_ms

#: default cap on concurrently-running sessions; ``set_max_concurrent`` beats it.
GLOBAL_MAX_CONCURRENT = _config.GLOBAL_MAX_CONCURRENT
TICK_SECONDS = _config.TICK_SECONDS


RunnerFn = Engine


class TaskQueue(SettleMixin, AdmissionMixin):
    @staticmethod
    def _derive_engine_kind(runner: "RunnerFn") -> str:
        qualname = getattr(runner, "__qualname__", "") or getattr(runner, "__name__", "")
        if "run_claude" in qualname:
            return "claude"
        if "stub_engine" in qualname or qualname.startswith("stub"):
            return "stub"
        return qualname or "unknown"

    @property
    def engine_kind(self) -> str:
        return self._engine_kind

    def __init__(self, store: StateStore, runner: Optional[RunnerFn] = None,
                 on_settle: Optional[Callable[[], None]] = None) -> None:
        self._store = store
        self._runner: RunnerFn = runner or run_claude
        self._engine_kind: str = self._derive_engine_kind(self._runner)
        #: fired on every terminal settle — the goal heartbeat's wake
        self._on_settle: Optional[Callable[[], None]] = on_settle
        self._bg: set[asyncio.Task] = set()
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._tick_task: Optional[asyncio.Task] = None
        self._hold_logged: Optional[str] = None

    def set_on_settle(self, hook: Optional[Callable[[], None]]) -> None:
        self._on_settle = hook

    def _fire_settle(self) -> None:
        if self._on_settle is not None:
            try:
                self._on_settle()
            except Exception as err:  # noqa: BLE001 — a bad hook must never break a run
                sys.stderr.write(f"task-queue: on_settle hook failed: {err}\n")

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    # ---- cancellation -------------------------------------------------

    def cancel_task(self, task_id: str) -> bool:
        """Mark cancelled (terminal) THEN tear down the live run, so the
        CancelledError can't be re-settled as failed. True iff it was live."""
        if not self._store.mark_task_cancelled(task_id):
            return False
        self._store.append_event(task_id=task_id, type="cancelled", source="devclaw",
                                 payload_json=json.dumps({"reason": "cancelled by client"}))
        live = self._running_tasks.get(task_id)
        if live is not None and not live.done():
            live.cancel()
        self._pump()
        return True

    async def drain(self) -> None:
        """Await all in-flight background work (tests)."""
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
            await asyncio.sleep(0)

    # ---- submission ---------------------------------------------------

    def submit(self, *, kind: TaskKind, workspace_dir: str, goal: str,
               verify_cmd: Optional[str] = None, deliver: bool = False,
               parent_goal_id: Optional[str] = None, target_branch: Optional[str] = None,
               project_id: Optional[str] = None, pump: bool = True,
               unplaced: Optional[str] = None) -> str:
        """Create a pending row and (by default) reconcile at once. ``pump=False``
        lets a caller create the row inside its own transaction and pump after
        it commits. ``unplaced`` is why the session has nowhere to run: the row
        settles ``INTERRUPTED`` with it at once and never dispatches."""
        task_id = str(uuid.uuid4())
        self._store.create_task(
            id=task_id, kind=kind, workspace_dir=workspace_dir, goal=goal,
            verify_cmd=verify_cmd, deliver=deliver, parent_goal_id=parent_goal_id,
            target_branch=target_branch, project_id=project_id,
        )
        if unplaced is not None:
            detail = f"workspace prep failed: {unplaced}"
            self._store.mark_failed(task_id, detail[:2000], exit=EXIT_INTERRUPTED, exit_detail=detail[:400])
            return task_id
        if pump:
            self._pump()
        return task_id

    def pump(self) -> None:
        self._pump()

    # ---- crash recovery + heartbeat ------------------------------------

    def recover(self) -> int:
        """Once at startup: reset rows left ``running`` by a dead process to
        ``pending`` — each reruns on the reset goal branch in a fresh session."""
        reaped = self._store.reset_running_to_pending()
        for tid in reaped:
            self._store.append_event(
                task_id=tid, type="reaped", source="devclaw",
                payload_json=json.dumps({"reason": "orphaned running task reset to pending on startup"}),
            )
        if reaped:
            sys.stderr.write(f"task-queue: recovered {len(reaped)} orphaned running task(s)\n")
        return len(reaped)

    def start_ticking(self) -> None:
        if self._tick_task is None or self._tick_task.done():
            self._tick_task = asyncio.ensure_future(self._tick_loop())

    async def stop_ticking(self) -> None:
        if self._tick_task is not None:
            self._tick_task.cancel()
            try:
                await self._tick_task
            except asyncio.CancelledError:
                pass
            self._tick_task = None

    async def _tick_loop(self) -> None:
        while True:
            try:
                self._pump()
            except Exception as err:  # noqa: BLE001 — a bad tick must never kill the heartbeat
                sys.stderr.write(f"task-queue: tick pump failed: {err}\n")
            await asyncio.sleep(TICK_SECONDS)

    # ---- the reconcile core -------------------------------------------

    def dispatch_open(self) -> tuple[bool, str]:
        """Whether NEW dispatch may happen right now: the quota pause, the
        operator hold and the run window, in that order. ``(open, why)``."""
        until, reason = self._store.global_pause()
        if until and _now_ms() < until:
            return False, f"paused until reset: {reason[:80]}"
        blocked, why = operator_block(self._store.operator_hold(), self._store.get_run_schedule(), _now_ms())
        if blocked:
            return False, why
        return True, ""

    def _pump(self) -> None:
        """Reconcile execution against DB state: launch what's runnable up to
        the caps. Synchronous and atomic; ``claim_pending`` is the final guard."""
        until, reason = self._store.global_pause()
        pause_expired = False
        if until:
            if _now_ms() < until:
                return
            self._store.clear_global_pause()
            pause_expired = True
        blocked, why = operator_block(self._store.operator_hold(), self._store.get_run_schedule(), _now_ms())
        if blocked:
            hold_key = why.split(" (local ")[0]
            if pause_expired or hold_key != self._hold_logged:
                prefix = f"quota pause expired ({reason[:80]}) — " if pause_expired else ""
                sys.stderr.write(f"task-queue: {prefix}dispatch held: {why}\n")
                self._hold_logged = hold_key
            return
        if pause_expired:
            sys.stderr.write(f"task-queue: quota pause expired ({reason[:80]}) — resuming\n")
        if self._hold_logged is not None:
            sys.stderr.write(f"task-queue: dispatch hold lifted ({self._hold_logged}) — resuming\n")
            self._hold_logged = None
        if not self._store.has_active_work():
            return
        max_concurrent = self._effective_max_concurrent()
        running = self._store.count_running()
        if running >= max_concurrent:
            return
        for t in self._store.list_pending(limit=max_concurrent):
            if running >= max_concurrent:
                break
            if self._workspace_break_active(t.workspace_dir):
                continue
            if self._store.claim_pending(t.id):
                running += 1
                self._launch(t.id, t.kind, t.workspace_dir, t.goal)

    def _launch(self, task_id: str, kind: TaskKind, workspace_dir: str, goal: str) -> None:
        task = self._spawn(self._execute(task_id, kind, workspace_dir, goal))
        self._running_tasks[task_id] = task
        def _drop(_t: "asyncio.Task", tid: str = task_id) -> None:
            self._running_tasks.pop(tid, None)

        task.add_done_callback(_drop)

    def _effective_max_concurrent(self) -> int:
        try:
            override = self._store.max_concurrent()
        except Exception:  # noqa: BLE001 — a dial must never wedge dispatch
            override = None
        return override if override else GLOBAL_MAX_CONCURRENT
