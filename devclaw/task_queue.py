"""Async task executor — DB-driven, crash-safe, heartbeat-paced.

The MCP handler calls ``submit()`` and gets an id back
immediately; engine runs happen in the background and the state store is flipped
when they settle. Single-writer-to-state by design — only this queue mutates rows.

**Scheduling is reconciled from DB state, not from in-memory counters.** The core
is :meth:`_pump`: read what's runnable from the store, claim it atomically, launch
up to the concurrency caps. Three things call it — ``submit``, a task settling,
and a periodic **heartbeat tick** — and they're all idempotent because
``claim_pending`` is the final guard. Because concurrency is derived from the
``running`` rows (not a counter that dies with the process), the system is
**crash-safe**: :meth:`recover` resets orphaned ``running`` tasks at startup and
the next pump resumes them. That's the "ephemeral body / durable mind" model — a
build survives restarts.

**Cheap-idle guard:** every pump first asks the store "is there any work?" (one
COUNT) and returns immediately if not — an idle tick costs ~nothing, so we never
burn the engine on empty ticks.

Notifications: standalone tasks fire their own ``notify_url`` on terminal state
(bounded retries). (The program/DAG lane and its program-level notify were
retired by spec 022 US3; the ``programs`` table survives as historical rows
behind the read-only get_program/list_programs surfaces.)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Awaitable, Callable, Optional, Protocol

from .engine import Engine
from .quality import format_feedback, review_gate
from .quality.reachability import judge_reachability
from . import config as _config
# Module-local bindings on purpose: tests patch host_mem_available_bytes on
# THIS namespace (the queue's readers resolve it here). ``_parse_mem`` is
# re-exported for the unit tests that exercise it via this module.
from .host_resources import _parse_mem, host_mem_available_bytes  # noqa: F401
from .engine.sandcastle import (
    run_sandcastle,
    sandbox_owner_id,
    sweep_orphan_sandboxes,
)
from .dispatch_gate import operator_block
from .state_store import StateStore, TaskKind, _now_ms
# Leaf concerns split out of this module. The git ``_sync`` helpers are
# re-exported here because tests import them from ``task_queue``; the async
# wrappers that resolve them as module globals live in ``queue/settle.py``
# now (patch the sync seams THERE for the settle path).
from .task_git import (  # noqa: F401
    BRANCH_STALE_THRESHOLD,
    _base_branch_error_sync,
    _git_commit_exists_sync,
    _git_diff_sync,
    _git_head_sync,
    _review_repo_context_sync,
    _wip_snapshot_sync,
    branch_staleness_sync as _branch_staleness_sync,
)
from .task_notify import _NotifyMixin
# The TaskQueue method groups split out as mixins (same idiom as state_store/
# and goal/store/). Dependency direction is one-way: this module imports from
# devclaw.queue; nothing under devclaw/queue/ imports this module at runtime.
# The seams the moved methods resolve as module globals (deliver_change,
# _capture_change, _git_diff, TASK_MAX_RETRIES, the fast-fail markers, the
# breaker/memory constants, …) now live in — and are patched on — the mixin
# modules; the names re-imported below are runtime uses of the settle module's
# bindings plus byte-identical re-exports for existing importers.
from .queue.admission import (  # noqa: F401 — constants re-exported for callers/tests
    AdmissionMixin,
    COGNITION_MEM_RESERVE_BYTES,
    MEM_LAUNCH_FLOOR_BYTES,
    SANDBOX_MEMORY_BYTES,
)
from .queue.settle import (  # noqa: F401 — helpers re-exported for callers/tests
    SettleMixin,
    _PAUSED,
    _PausedSentinel,
    _REVIEW_CRASH_MARKER,
    _capture_change,
    _diff_stats,
    _review_repo_context,
)

NOTIFY_BACKOFF_MS = (1000, 2000, 4000)
#: global cap on concurrently-running tasks — backpressure
GLOBAL_MAX_CONCURRENT = _config.GLOBAL_MAX_CONCURRENT


#: heartbeat interval — the tick re-derives scheduling from DB state
TICK_SECONDS = _config.TICK_SECONDS
#: the pre-PR adversarial review gate: after the verify gate + test-integrity
#: pass (behaviour is proven), a Claude pass READS the diff against the ticket +
#: the quality bar and can send it back through the retry loop (request_changes)
#: BEFORE the PR opens — closing the "green but untrustworthy" hole a spectator-PO
#: can't see. On by default (it costs one Claude call per successful code
#: task); a project may opt out via its registry `review_gate` override.
REVIEW_GATE_ENABLED = True
#: review applies only to code-producing kinds (a diff to read); review_repository
#: is read-only, and onboard writes only DRAFT artifacts — the four comprehension
#: docs plus a create-only-when-absent `.devcontainer/Dockerfile` — which ship as
#: `docs`-typed deliveries behind a human merge (the backstop for that
#: build-relevant file), so neither needs the pre-PR adversarial review gate.
_REVIEWABLE_KINDS = ("implement_feature", "fix_bug")

#: ``BRANCH_STALE_THRESHOLD`` is defined next to the staleness probe in
#: :mod:`devclaw.task_git` (imported above) so the prep-time refuse guard and the
#: tick-path dispatch skip share ONE predicate; re-exported here for callers/tests.
#: "strict" → a frontend change with no browser suite at all (`absent`) blocks,
#: forcing E2E adoption; "flexible" (default) lets `absent` fall through with a
#: loud log so a not-yet-E2E'd project isn't wedged. Mirrors CI_GATE_MODE. The
#: per-project override is resolved in _browser_gate_mode (registry seam); a PR
#: flips this fleet default (formerly DEVCLAW_GOAL_BROWSER_GATE_MODE, #410 —
#: never set off-default; project-scoped config now lives in the registry seam).
BROWSER_GATE_MODE = "flexible"


class _ProjectOverrides(Protocol):
    """The one method the queue uses on the project registry (wired late via
    set_registry; a Protocol so tests keep passing plain fakes)."""

    def resolve_override(self, project_id: "str | None", key: str, default): ...

#: the execution engine — orchestration depends on this seam, not on the agent
RunnerFn = Engine


class TaskQueue(_NotifyMixin, SettleMixin, AdmissionMixin):
    @staticmethod
    def _derive_engine_kind(runner: "RunnerFn") -> str:
        """Map a runner function to a short label for the trace ("stub" /
        "sandcastle" / "host"). Falls back to the function's qualified name so
        unknown custom runners are still identifiable."""
        qualname = getattr(runner, "__qualname__", "") or getattr(runner, "__name__", "")
        if "run_sandcastle" in qualname:
            return "sandcastle"
        if "run_host" in qualname:
            return "host"
        if "stub_engine" in qualname or qualname.startswith("stub"):
            return "stub"
        return qualname or "unknown"

    @property
    def engine_kind(self) -> str:
        return self._engine_kind

    def __init__(
        self,
        store: StateStore,
        runner: Optional[RunnerFn] = None,
        on_settle: Optional[Callable[[], None]] = None,
        reviewer: Optional[Callable[..., Awaitable[dict]]] = None,
        reachability_judge: Optional[Callable[..., Awaitable[dict]]] = None,
    ) -> None:
        self._store = store
        # This queue's sandbox-owner id (derived from its state-DB path): stamps
        # every launched sandbox and scopes the startup sweep, so two devclaw
        # processes sharing one docker daemon (live service + a measure/eval
        # run) never reap each other's in-flight containers. realpath'd so a
        # symlink/relative respelling of the same DB can't mint a new id and
        # strand the old id's orphans unreapable.
        self._sandbox_owner: str = sandbox_owner_id(os.path.realpath(store.db_path))
        #: per-pump host-RAM budget for sandbox launches (None off the sandcastle
        #: path) — set fresh by every _pump; declared here so the type is honest.
        self._mem_budget: "int | None" = None
        # Injectable for tests; defaults to the real sandbox engine.
        self._runner: RunnerFn = runner or run_sandcastle
        # A short engine-kind label for trace events ("stub" / "sandcastle" /
        # "host") — derived from the runner's qualified name so
        # silently mis-wired sandboxes can be spotted in the timeline.
        self._engine_kind: str = self._derive_engine_kind(self._runner)
        # The pre-PR review gate's cognition (diff → verdict). Injectable so tests
        # stub the Claude call; defaults to the real review_gate (host-side
        # claude) — the single adversarial reviewer wrapped in the cognition-
        # timeout degradation ladder.
        self._reviewer: Callable[..., Awaitable[dict]] = reviewer or review_gate
        # The browser-gate reachability escape valve's cognition (diff + repo
        # context → reachable verdict). Injectable so tests stub the Claude call;
        # defaults to the real grounded judge. Consulted ONLY when the mechanical
        # browser gate is about to block a no-browser-run frontend change — never
        # on idle / backend / passing paths (the zero-token guard).
        self._reachability_judge: Callable[..., Awaitable[dict]] = (
            reachability_judge or judge_reachability
        )
        # Optional in-process hook fired whenever a task reaches a
        # terminal state — the goal layer wires its heartbeat-wake here so a
        # finished engine run triggers an immediate goal tick (replacing the old
        # cross-service HTTP /wake). Must be cheap + non-throwing.
        self._on_settle: Optional[Callable[[], None]] = on_settle
        #: retain background task refs so they aren't garbage-collected mid-run
        self._bg: set[asyncio.Task] = set()
        #: task_id -> the live asyncio.Task running its engine, so cancel() can
        #: reach in and tear a specific run down (the docker subprocess dies via
        #: the runner's finally). Only ever holds genuinely in-flight tasks.
        self._running_tasks: dict[str, asyncio.Task] = {}
        #: the heartbeat tick task (started by the server, not in tests)
        self._tick_task: Optional[asyncio.Task] = None
        #: optional project registry, wired post-construction (see set_registry) —
        #: used ONLY to resolve a per-project review_gate override. None is fine:
        #: the gate falls back to the devclaw-wide REVIEW_GATE_ENABLED default.
        self._registry: "Optional[_ProjectOverrides]" = None
        #: last operator-hold reason _pump logged (normalized: local-time suffix
        #: stripped), so a persistent hold logs ONCE per reason, not every tick.
        self._hold_logged: Optional[str] = None

    def set_on_settle(self, hook: Optional[Callable[[], None]]) -> None:
        """Register the terminal-state hook (the goal layer's heartbeat wake)."""
        self._on_settle = hook

    def set_registry(self, registry: "Optional[_ProjectOverrides]") -> None:
        """Wire the project registry after construction (the registry is built
        after the queue in server/_state.py). Used only to resolve the
        per-project ``review_gate`` override."""
        self._registry = registry

    def _fire_settle(self) -> None:
        if self._on_settle is not None:
            try:
                self._on_settle()
            except Exception as err:  # noqa: BLE001 — a bad hook must never break a run
                sys.stderr.write(f"task-queue: on_settle hook failed: {err}\n")

    def _spawn(self, coro: Awaitable) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    # ---- cancellation (deliberate abort) --------------------------------

    def _abort_live_task(self, task_id: str) -> None:
        """Tear down the in-flight execution of one task, if any. The DB row must
        already be 'cancelled' (terminal) BEFORE this — so the CancelledError that
        propagates out of the run can't be re-settled as 'failed' (mark_failed
        guards on pending/running). The runner's finally kills the container."""
        task = self._running_tasks.get(task_id)
        if task is not None and not task.done():
            task.cancel()

    def cancel_task(self, task_id: str) -> bool:
        """Abort one task. Marks it 'cancelled' (no-op if already terminal), then
        tears down its live run. Returns True iff the task was actually
        pending/running (i.e. abortable)."""
        moved = self._store.mark_task_cancelled(task_id)
        if not moved:
            return False  # already done/failed/cancelled — nothing to abort
        task = self._store.get_task(task_id)
        self._store.append_event(
            task_id=task_id,
            program_id=task.program_id if task else None,
            type="cancelled",
            source="devclaw",
            payload_json=json.dumps({"reason": "cancelled by client"}),
        )
        self._abort_live_task(task_id)
        # A slot may have freed — reconcile.
        self._pump()
        return True

    async def drain(self) -> None:
        """Await all in-flight background work. Used by tests for determinism.

        The ``sleep(0)`` is load-bearing: when every task in ``_bg`` is already
        done, ``gather`` resolves without yielding to the loop, so the
        ``add_done_callback`` discards never run and ``_bg`` never shrinks.
        Yielding once lets those call_soon callbacks fire so the loop ends.
        """
        while self._bg:
            await asyncio.gather(*list(self._bg), return_exceptions=True)
            await asyncio.sleep(0)

    # ---- submission -----------------------------------------------------

    def submit(
        self,
        *,
        kind: TaskKind,
        workspace_dir: str,
        goal: str,
        notify_url: Optional[str] = None,
        verify_cmd: Optional[str] = None,
        deliver: bool = False,
        title: Optional[str] = None,
        parent_goal_id: Optional[str] = None,
        scaffold: bool = False,
        strictness: str = "trust",
        base_branch: Optional[str] = None,
        target_branch: Optional[str] = None,
        project_id: Optional[str] = None,
        pump: bool = True,
    ) -> str:
        """Create a task row (status 'pending') and, by default, immediately
        reconcile execution against it (claim + launch, up to the caps).

        ``base_branch`` / ``target_branch`` (v1-helper-resurface P1, PR-2) are
        the direct-dispatch branch targets: the launch step preps the workspace
        onto ``target_branch`` (creating it off ``base_branch`` when absent on
        origin) and delivery must land on it; ``base_branch`` is validated
        against origin before the engine runs and becomes the PR base / diff
        range. Both None (every goal-path caller) ⇒ the unpinned path — no
        prep, no validation, no extra delivery kwargs.

        ``pump=False`` (PR7 — the dispatch/pump split): create the row ONLY,
        no claim, no launch. ``_pump()`` synchronously claims PENDING work —
        including UNRELATED tasks — and spawns real ``asyncio`` execution for
        it; a caller that wraps ``submit()`` in its own atomic unit (the goal
        heartbeat's dispatch transaction) cannot let that unit's eventual
        rollback leave a phantom container running against a row that no
        longer exists. ``pump=False`` callers are responsible for pumping
        later (``pump()``/``kick()``, or simply the queue's own periodic
        ``start_ticking`` heartbeat, which self-heals a missed pump within
        one ``TICK_SECONDS``)."""
        task_id = str(uuid.uuid4())
        self._store.create_task(
            id=task_id,
            kind=kind,
            workspace_dir=workspace_dir,
            goal=goal,
            notify_url=notify_url,
            verify_cmd=verify_cmd,
            deliver=deliver,
            title=title,
            parent_goal_id=parent_goal_id,
            scaffold=scaffold,
            strictness=strictness,
            base_branch=base_branch,
            target_branch=target_branch,
            project_id=project_id,
        )
        if pump:
            self._pump()
        return task_id

    def pump(self) -> None:
        """Public wrapper over the reconcile-from-DB-state core (PR7's
        dispatch/pump split). Callers that submitted with ``pump=False``
        invoke this AFTER their own atomic unit commits — e.g.
        ``InProcessEngine.kick()``, called by the goal heartbeat right after
        its dispatch transaction commits. Idempotent + cheap-idle-guarded,
        same as every other ``_pump()`` call site."""
        self._pump()

    # ---- crash recovery + heartbeat -------------------------------------

    def recover(self) -> int:
        """One-time crash recovery — call at startup, BEFORE ticking/serving.

        A task left ``running`` by a dead process has no live execution behind
        it, so reset it to ``pending`` to be re-run; log each reap. Returns the
        number of tasks reaped.

        Also reaps the dead process's leaked sandbox CONTAINERS: the row reset
        below re-runs the task in a new container, but the original keeps
        running (``--rm`` only fires when its own docker client exits) with
        nothing left to stop it. recover() runs before this process launches
        anything, so every ``devclaw.sandbox``-labeled container is by
        definition orphaned. No-op when docker is unavailable (stub/host engine
        environments, CI).
        """
        swept = sweep_orphan_sandboxes(self._sandbox_owner)
        if swept:
            sys.stderr.write(
                f"task-queue: reaped {swept} orphaned sandbox container(s)\n"
            )
        reaped = self._store.reset_running_to_pending()
        for tid in reaped:
            t = self._store.get_task(tid)
            self._store.append_event(
                task_id=tid,
                program_id=t.program_id if t else None,
                type="reaped",
                source="devclaw",
                payload_json=json.dumps(
                    {"reason": "orphaned running task reset to pending on startup"}
                ),
            )
        if reaped:
            sys.stderr.write(f"task-queue: recovered {len(reaped)} orphaned running task(s)\n")
        return len(reaped)

    def start_ticking(self) -> None:
        """Start the heartbeat. Pumps immediately (resumes recovered work), then
        every TICK_SECONDS. Idempotent."""
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
            except Exception as err:  # a bad tick must never kill the heartbeat
                sys.stderr.write(f"task-queue: tick pump failed: {err}\n")
            await asyncio.sleep(TICK_SECONDS)

    # ---- the reconcile core ---------------------------------------------

    def _pump(self) -> None:
        """Reconcile execution against DB state: launch what's runnable up to the
        global cap. Synchronous
        and atomic (no awaits between reading counts and claiming), so concurrent
        callers can't over-launch; ``claim_pending`` is the final guard. Returns
        fast when there's no work (cheap-idle guard)."""
        # Global quota pause: a usage/rate limit is account-wide, so hold ALL
        # dispatch until it lifts. The tick loop calls _pump every TICK_SECONDS,
        # so dispatch auto-resumes within one tick of the pause expiring.
        until, reason = self._store.global_pause()
        pause_expired = False
        if until:
            if _now_ms() < until:
                return
            self._store.clear_global_pause()
            pause_expired = True
        # Operator controls (manual pause toggle / daily run-window): hold ALL new
        # launches while active. In-flight tasks run to completion; the tick loop
        # re-checks every TICK_SECONDS, so dispatch resumes when the window opens.
        # Checked BEFORE announcing a pause-expiry "resuming": a quota reset can
        # land outside the run window (2026-07-20: reset 6am UTC, window closed
        # 4am UTC), and logging "resuming" then holding silently reads as a
        # missed auto-resume. The hold is logged once per reason, not per tick.
        blocked, why = operator_block(
            self._store.operator_hold(), self._store.get_run_schedule(), _now_ms()
        )
        if blocked:
            hold_key = why.split(" (local ")[0]  # window reason carries a per-minute local time
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
        running = self._store.count_running()
        # Host-memory admission budget: measured ONCE per pump, and only HERE —
        # after the cheap-idle guard above, so an idle tick never reads /proc and
        # the zero-cost-idle invariant holds. Only the real docker sandbox runner
        # overcommits host RAM; under a stub/host runner (tests, dev, the CI suite)
        # the gate is inert (None ⇒ fail open) and no /proc read happens off the
        # sandbox path — so the suite is byte-unaffected by whatever RAM CI has.
        self._mem_budget = (
            host_mem_available_bytes() if self._engine_kind == "sandcastle" else None
        )
        self._mem_deny_logged = False

        # Standalone pending tasks (no deps) — oldest first, up to the global cap.
        # Workspace circuit-breaker skips dispatch to a workspace whose recent
        # failure run tripped a hold; siblings on other workspaces keep flowing.
        if running < GLOBAL_MAX_CONCURRENT:
            for t in self._store.list_pending_standalone(limit=GLOBAL_MAX_CONCURRENT):
                if running >= GLOBAL_MAX_CONCURRENT:
                    break
                if self._workspace_break_active(t.workspace_dir):
                    continue
                need = self._effective_sandbox_mem_bytes(t.project_id)
                if not self._mem_can_launch(need):
                    break  # not enough host RAM for another sandbox this tick
                if self._store.claim_pending(t.id):
                    self._mem_commit_launch(need)
                    running += 1
                    self._launch(t.id, t.kind, t.workspace_dir, t.goal, None)

    def _launch(
        self,
        task_id: str,
        kind: TaskKind,
        workspace_dir: str,
        goal: str,
        program_id: Optional[str],
    ) -> None:
        task = self._spawn(self._execute(task_id, kind, workspace_dir, goal, program_id))
        # Index it for cancel(); drop the ref the moment it settles so the map
        # only ever names genuinely in-flight runs.
        self._running_tasks[task_id] = task
        def _drop_running(_t: "asyncio.Task", tid: str = task_id) -> None:
            self._running_tasks.pop(tid, None)

        task.add_done_callback(_drop_running)

    def _review_gate_enabled(self, project_id: Optional[str]) -> bool:
        """Whether the pre-PR review gate runs for a task owned by ``project_id``:
        the owning project's ``review_gate`` override if set, else the
        devclaw-wide ``REVIEW_GATE_ENABLED`` default. No registry wired, or no
        owning project (None) → the default. Keyed by the project reference key
        (#524 P3), not a workspace-path scan."""
        if self._registry is None:
            return REVIEW_GATE_ENABLED
        return self._registry.resolve_override(
            project_id, "review_gate", REVIEW_GATE_ENABLED
        )

    def _sandbox_image(self, project_id: Optional[str]):
        """Per-task sandbox image (ADR 0005) for a task owned by ``project_id``:
        the owning project's ``sandbox_image`` override if set, else None — the
        engine then applies its own DEVCLAW_SANDBOX_IMAGE default (the queue
        deliberately does not know the engine's default; docker-less engines
        ignore the field). The escape hatch + migration bridge for stacks the
        mise path doesn't cover yet. No registry wired / no project → None."""
        if self._registry is None:
            return None
        return self._registry.resolve_override(project_id, "sandbox_image", None)

    def _sandbox_sizing(self, project_id):
        """Per-task sandbox sizing (spec 020 US4) for a task owned by
        ``project_id``: ``(sandbox_memory, sandbox_cpus)`` — each the owning
        project's override if set, else None (the engine then applies its own
        DEVCLAW_SANDBOX_MEMORY / DEVCLAW_SANDBOX_CPUS defaults). Same shape
        and rationale as ``_sandbox_image`` above."""
        if self._registry is None:
            return None, None
        return (
            self._registry.resolve_override(project_id, "sandbox_memory", None),
            self._registry.resolve_override(project_id, "sandbox_cpus", None),
        )

    def _effective_sandbox_mem_bytes(self, project_id) -> int:
        """The bytes launch admission must account for THIS task (spec 020
        US4/FR-010): the project's override when set, else the instance
        default — so a large per-project cap cannot overcommit the host by
        being admitted at default-size."""
        mem, _ = self._sandbox_sizing(project_id)
        return _parse_mem(mem) if mem else SANDBOX_MEMORY_BYTES

    def _browser_gate_mode(self, project_id: Optional[str]) -> str:
        """Browser-gate stance (``flexible``|``strict``) for a task owned by
        ``project_id``: the owning project's ``browser_gate_mode`` override if
        set, else the devclaw-wide ``BROWSER_GATE_MODE`` default. No registry
        wired / no project → the default. Same resolver seam as
        ``_review_gate_enabled``."""
        if self._registry is None:
            return BROWSER_GATE_MODE
        return self._registry.resolve_override(
            project_id, "browser_gate_mode", BROWSER_GATE_MODE
        )

    async def _review_failure(
        self, kind: TaskKind, goal: str, diff: str, workspace_dir: str,
        *, scaffold: bool = False, project_id: Optional[str] = None,
    ) -> Optional[str]:
        """Run the pre-PR adversarial review gate on the change's diff. Returns the
        request-changes feedback (→ fed back into the retry loop like a gate fail),
        or None to let the task ship. Fails open ONLY for the by-design cases —
        a disabled gate, a non-code kind, a SCAFFOLD task, an empty diff. A
        reviewer CRASH fails CLOSED: a crash is not an approval, and the old
        failing-open path meant any internal reviewer error shipped the change
        unreviewed with a line on stderr nobody reads. A quota/rate-limit crash
        text is classified by the caller's quota guard and PAUSES (requeue +
        resume) instead of failing — the correct semantics for "the reviewer
        couldn't run right now".

        SCAFFOLD (L3, #222): a generated-scaffolding task (``ng new`` /
        ``dotnet new`` boilerplate) skips this ADVERSARIAL gate — its diff is
        generator output, not hand-authored logic, and an oversized generated
        diff crashes the review model. This is safe because it's a NARROW bypass:
        the caller has ALREADY passed the change through the verify/build gate and
        the test-integrity scan before reaching here, and neither of those is
        gated on ``scaffold``. So even a MIS-tagged real code task is at worst
        "unreviewed but still must build + pass tests", never "ships broken"."""
        if not self._review_gate_enabled(project_id) or kind not in _REVIEWABLE_KINDS:
            return None
        if scaffold:
            sys.stderr.write(
                "task-queue: scaffold task — skipping adversarial review gate "
                "(verify gate + test-integrity already enforced)\n"
            )
            return None
        if not diff.strip():
            return None
        # Ground the reviewer in the ACTUAL task workspace so it judges the real
        # repo, not the control-plane repo host-side claude was launched from
        # (live-found 2026-07-13: a lone ci.yml diff on the .NET/Angular
        # closeloop-bench got reviewed as if it were devclaw's Python/React repo).
        # Best-effort and collected OUTSIDE the try: it never raises, so a git
        # hiccup gathering context can't trip the fail-closed reviewer path below.
        repo_context = await _review_repo_context(workspace_dir)
        try:
            review = await self._reviewer(
                goal=goal, kind=kind, diff=diff, repo_context=repo_context
            )
        except Exception as err:  # noqa: BLE001 — fail closed, never silently approve
            # Carry the model's RAW response into the failure string: a usage
            # limit comes back as plain prose ("You've hit your session limit ·
            # resets 5:20pm"), which extract_json turns into a bare "No JSON
            # object found" PlannerError with the prose only in err.raw. The
            # caller's quota guard classifies the failure STRING — without the
            # raw text it reads a quota hit as a permanent gate defect and fails
            # the task ("split the diff") instead of pausing dispatch
            # (live-found 2026-07-14: 7 tasks across two goals).
            detail = f"{err.__class__.__name__}: {err}"
            raw = getattr(err, "raw", None)
            if isinstance(raw, str) and raw.strip():
                detail += f" — model response: {raw.strip()[:500]}"
            sys.stderr.write(f"task-queue: {_REVIEW_CRASH_MARKER} {detail}\n")
            return (
                f"{_REVIEW_CRASH_MARKER} "
                f"{detail}. The diff was never reviewed, "
                "so it must not ship on the gate's silence."
            )
        if review.get("verdict") == "request_changes":
            sys.stderr.write(
                f"task-queue: review gate requested changes "
                f"({len(review.get('blocking', []))} blocking issue(s))\n"
            )
            return format_feedback(review)
        return None

    # ---- notify ---------------------------------------------------------
    # _notify_task / _post_with_retries live in
    # devclaw.task_notify._NotifyMixin (mixed into this class above).
