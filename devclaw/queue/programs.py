"""Programs — the DAG-program lifecycle of the queue, split out as a mixin.

:class:`ProgramsMixin` carries the program surface: submission (planned and
planner-driven), plan persistence, ready-task scheduling under the concurrency
caps, terminalization, and cancellation. ``GLOBAL_MAX_CONCURRENT`` /
``MAX_CONCURRENT_PER_PROGRAM`` are bound HERE from :mod:`devclaw.config` for
``_schedule_program``'s readers (tests that cap program scheduling patch THIS
namespace); ``task_queue`` keeps its own binding of the same config values for
``_pump``'s standalone loop and for ``goal.fanout``'s host-cap read.

Split out of ``TaskQueue`` as a mixin on the SAME instance — every method here
runs against the ``self._store`` / ``self._planning`` / ``self._planner`` the
base ``TaskQueue`` owns, so the single-writer / sticky-failure semantics are
byte-identical to the pre-split monolith. This module must never import
``devclaw.task_queue`` at runtime (the dependency points the other way).
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from .. import config as _config
from ..llm_call import PlannerError
from ..program_plan import PlannedTask, order_tasks

if TYPE_CHECKING:
    import asyncio

    from ..state_store import Program, StateStore, Task, TaskKind

MAX_CONCURRENT_PER_PROGRAM = _config.MAX_CONCURRENT_PER_PROGRAM
#: global cap on concurrently-running tasks across all programs — backpressure
GLOBAL_MAX_CONCURRENT = _config.GLOBAL_MAX_CONCURRENT


class ProgramsMixin:
    if TYPE_CHECKING:
        # The composing class (TaskQueue) owns these; declared under
        # TYPE_CHECKING so the seam is checked, never run.
        _store: StateStore
        _planning: set[str]
        _planner: Callable[[str, str], Awaitable[list[PlannedTask]]]

        def _pump(self) -> None: ...
        def _fire_settle(self) -> None: ...
        def _spawn(self, coro: Awaitable) -> asyncio.Task: ...
        def _abort_live_task(self, task_id: str) -> None: ...
        def _mem_can_launch(self, need_bytes: "int | None" = None) -> bool: ...
        def _mem_commit_launch(self, need_bytes: "int | None" = None) -> None: ...
        def _launch(
            self,
            task_id: str,
            kind: TaskKind,
            workspace_dir: str,
            goal: str,
            program_id: Optional[str],
        ) -> None: ...
        async def _notify_program(self, program: Program, tasks: list[Task]) -> None: ...

    def cancel_program(self, program_id: str) -> bool:
        """Abort a whole program: cancel its pending tasks (so nothing new starts),
        tear down every running task, and mark the program 'cancelled'. Returns
        True iff the program was non-terminal (i.e. abortable)."""
        program = self._store.get_program(program_id)
        if program is None or program.status in ("done", "failed", "cancelled"):
            return False
        # Stop scheduling first, then drain in-flight work.
        cancelled_pending = self._store.cancel_program_pending_tasks(program_id)
        running = [
            t.id
            for t in self._store.list_program_tasks(program_id)
            if t.status == "running"
        ]
        for tid in running:
            self._store.mark_task_cancelled(tid)
            self._abort_live_task(tid)
        self._store.mark_program_cancelled(program_id, error="cancelled by client")
        for tid in cancelled_pending + running:
            self._store.append_event(
                task_id=tid,
                program_id=program_id,
                type="cancelled",
                source="devclaw",
                payload_json=json.dumps({"reason": "program cancelled by client"}),
            )
        # Cancelling freed global concurrency slots (the running rows are now
        # terminal) — let other pending work / programs claim them.
        self._pump()
        return True

    def submit_program(
        self,
        *,
        workspace_dir: str,
        goal: str,
        notify_url: Optional[str] = None,
        open_pr: bool = False,
        verify_cmd: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
        strictness: str = "trust",
        project_id: Optional[str] = None,
        pump: bool = True,
    ) -> str:
        """Submit a program the decomposer will plan into child tasks.

        ``open_pr`` (default False — commit directly, open no PR) is inherited
        by every child task the decomposer creates — under a standing goal with
        ``open_pr: true`` on the Action, each child task delivers as a
        reviewable-slice PR instead of committing directly to the workspace
        branch. ``verify_cmd`` (default None) is inherited the same way as the
        gate command. Closes the closeloop-mission-v2 defect where the
        activity-timeline program pushed straight to main because the flags
        stopped at ``submit_program`` and never reached child ``create_task``
        calls.

        ``pump=False`` (PR7 — see :meth:`submit`): create the program row
        ONLY — no planner kickoff (no ``_planning`` bookkeeping, no
        ``_plan_and_start`` spawn). The row lands 'planning' with zero
        tasks, which the EXISTING reconcile-from-DB-state logic in
        :meth:`_pump` already treats as "the planner never started (or
        died before persisting) — kick it off" — the same recovery path a
        crash mid-plan takes, reused here on purpose rather than duplicated.
        """
        program_id = str(uuid.uuid4())
        self._store.create_program(
            id=program_id, goal=goal, workspace_dir=workspace_dir,
            notify_url=notify_url, open_pr=open_pr, verify_cmd=verify_cmd,
            parent_goal_id=parent_goal_id, strictness=strictness,
            project_id=project_id,
        )
        if pump:
            self._planning.add(program_id)
            self._spawn(self._plan_and_start(program_id, workspace_dir, goal))
        return program_id

    def _maybe_terminalize(self, program: Program, tasks: list[Task]) -> bool:
        """Mark the program done/failed (+ notify) if it has reached a terminal
        state. Returns True if it did."""
        all_done = len(tasks) > 0 and all(t.status == "done" for t in tasks)
        any_failed = any(t.status == "failed" for t in tasks)
        any_cancelled = any(t.status == "cancelled" for t in tasks)
        running_in_prog = sum(1 for t in tasks if t.status == "running")
        if all_done:
            self._store.mark_program_done(program.id)
            final = self._store.get_program(program.id)
            if final:
                self._spawn(self._notify_program(final, tasks))
            self._fire_settle()  # a program terminalized → wake the goal layer
            return True
        # Sticky terminal: a failed or cancelled child blocks its dependents, so
        # the program can't complete. Terminalize once no sibling is still in
        # flight. Failure outranks cancellation (an error is worth surfacing).
        if (any_failed or any_cancelled) and running_in_prog == 0:
            if any_failed:
                first_err = (
                    next((t.error for t in tasks if t.status == "failed"), None)
                    or "task failed"
                )
                self._store.mark_program_failed(program.id, first_err)
            else:
                # Sweep still-pending siblings to cancelled too, so they don't
                # dangle 'pending' under a terminal program (which would keep
                # has_active_work() true forever). The failure path above leaves
                # them as-is — unchanged behavior, deliberately.
                self._store.cancel_program_pending_tasks(program.id)
                self._store.mark_program_cancelled(
                    program.id, error="a task was cancelled"
                )
            final = self._store.get_program(program.id)
            if final:
                self._spawn(self._notify_program(final, tasks))
            self._fire_settle()  # program failed/cancelled → wake the goal layer
            return True
        return False

    def _schedule_program(self, program: Program, tasks: list[Task], running: int) -> int:
        """Launch a program's ready tasks (deps all done) up to both caps. A
        present failure OR cancellation suppresses new launches (sticky) — the
        program is about to terminalize. Returns the updated global running tally."""
        if any(t.status in ("failed", "cancelled") for t in tasks):
            return running
        by_id = {t.id: t for t in tasks}
        running_in_prog = sum(1 for t in tasks if t.status == "running")
        for t in tasks:
            if running >= GLOBAL_MAX_CONCURRENT or running_in_prog >= MAX_CONCURRENT_PER_PROGRAM:
                break
            if t.status != "pending":
                continue
            deps_ready = all(
                (by_id.get(d) is not None and by_id[d].status == "done") for d in t.depends_on
            )
            if not deps_ready:
                continue
            if not self._mem_can_launch():
                break  # host RAM exhausted — defer remaining launches to a later tick
            if not self._store.claim_pending(t.id):  # lost the race
                continue
            self._mem_commit_launch()
            running += 1
            running_in_prog += 1
            self._launch(t.id, t.kind, t.workspace_dir, t.goal, program.id)
        return running

    def _persist_plan(
        self, program_id: str, workspace_dir: str, planned: list[PlannedTask]
    ) -> None:
        """Map planner keys -> real UUIDs and insert the program's tasks with
        depends_on remapped. Runs as one batch before anything is scheduled, so
        the dep graph is fully consistent by the time the first task starts.

        Child tasks INHERIT the program's ``open_pr`` and ``verify_cmd`` — the
        standing-goal / reviewable-slice contract. Review tasks
        (``review_repository``) always skip PR + gate because they write a
        read-only report, matching the standalone-task rule at engine.py."""
        program = self._store.get_program(program_id)
        if program is None:
            # Both callers create the row in the same synchronous call, so a
            # miss here is lost state, not an old row. It used to fall through
            # to open_pr=False — which silently reinstates the 2026-07-03
            # commit-straight-to-main defect on a whole program (#616 cutoff:
            # loud failure over silent degradation).
            raise RuntimeError(f"program row vanished before planning: {program_id}")
        # Child tasks inherit the program's PR + gate contract and its strictness
        # dial (ADR 0007), plus its owning project_id (#524 P3) so their
        # per-project knobs (review_gate, sandbox_image, browser_gate_mode)
        # resolve by id rather than by a workspace-path scan.
        program_open_pr = bool(program.open_pr)
        program_verify_cmd = program.verify_cmd
        program_strictness = program.strictness
        program_project_id = program.project_id
        key_to_uuid = {p.key: str(uuid.uuid4()) for p in planned}
        for idx, p in enumerate(planned):
            dep_uuids: list[str] = []
            for k in p.depends_on_keys:
                u = key_to_uuid.get(k)
                if not u:  # should never happen — order_tasks rejects dangling refs
                    raise RuntimeError(f"planner produced dangling ref '{k}'")
                dep_uuids.append(u)
            is_review = p.kind == "review_repository"
            self._store.create_task(
                id=key_to_uuid[p.key],
                kind=p.kind,
                # A fan-out lane brings its OWN checkout (spec 010 US3): two
                # agents cannot share one working tree. Everything else runs in
                # the program's workspace, exactly as before.
                workspace_dir=p.workspace_dir or workspace_dir,
                goal=p.goal,
                notify_url=None,  # per-task notify omitted — only program-level fires
                program_id=program_id,
                depends_on=dep_uuids,
                order_idx=idx,
                milestone=p.milestone,
                verify_cmd=None if is_review else program_verify_cmd,
                deliver=False if is_review else program_open_pr,
                # Threaded from ChecklistItem.scaffold via the decomposer
                # adapter — skips ONLY the adversarial review gate; the verify
                # gate + test-integrity scan still run (see _run_and_settle).
                scaffold=p.scaffold,
                # For a one-shot goal's program the key IS the checklist item
                # id — the goal settle path's child→item join (ADR 0003 st. 2).
                plan_key=p.key,
                # Inherited dial (ADR 0007) — every child of a strict program
                # blocks on a dial-able gate failure; a trust program advises.
                strictness=program_strictness,
                project_id=program_project_id,
                # Lane metadata (spec 010 US3) — NULL for every ordinary task.
                lane_json=json.dumps(p.lane) if p.lane else None,
            )

    def start_planned_program(
        self,
        *,
        goal: str,
        workspace_dir: str,
        planned: list[PlannedTask],
        notify_url: Optional[str] = None,
        open_pr: bool = False,
        verify_cmd: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
        strictness: str = "trust",
        project_id: Optional[str] = None,
        pump: bool = True,
    ) -> str:
        """Submit an ALREADY-PLANNED program (the caller supplies the
        ``PlannedTask`` DAG). Persists the DAG and starts it synchronously —
        never observed in 'planning', so no plan-time recovery edge case.
        Returns the program_id.

        ``open_pr`` / ``verify_cmd`` / ``parent_goal_id`` mirror
        :meth:`submit_program` — child tasks inherit the PR + gate contract
        via ``_persist_plan``. ``pump=False`` (see :meth:`submit`): rows only
        — a caller's atomic dispatch transaction commits first, then kicks
        the queue.

        The DAG is VALIDATED here (``order_tasks``: duplicate/self-dep/
        dangling/cycle rejection + the ``MAX_PROGRAM_TASKS`` cost brake) —
        the validation used to live in the deleted checklist adapter (spec
        008 shrink); it moved to this consumer boundary so no producer can
        hand the queue a deadlocking or unbounded plan. Raises
        :class:`PlannerError` before any row is written."""
        planned = order_tasks(list(planned))
        program_id = str(uuid.uuid4())
        self._store.create_program(
            id=program_id, goal=goal, workspace_dir=workspace_dir,
            notify_url=notify_url, open_pr=open_pr, verify_cmd=verify_cmd,
            parent_goal_id=parent_goal_id, strictness=strictness,
            project_id=project_id,
        )
        self._persist_plan(program_id, workspace_dir, planned)
        self._store.mark_program_running(program_id)
        if pump:
            self._pump()
        return program_id

    async def _plan_and_start(self, program_id: str, workspace_dir: str, goal: str) -> None:
        try:
            try:
                planned = await self._planner(goal, workspace_dir)
            except Exception as err:
                msg = f"planner: {err}" if isinstance(err, PlannerError) else str(err)
                self._store.mark_program_failed(program_id, msg)
                program = self._store.get_program(program_id)
                if program:
                    await self._notify_program(program, [])
                return
            self._persist_plan(program_id, workspace_dir, planned)
            self._store.mark_program_running(program_id)
        finally:
            self._planning.discard(program_id)
        self._pump()
