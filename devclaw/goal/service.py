"""The goal layer, wired — the folded-in goalclaw, now a subsystem of devclaw.

Owns the durable goals under ``DEVCLAW_GOALS_DIR``, drives them across a heartbeat
(a resident asyncio loop, woken either by the interval or — in-process — by a task
settling), and exposes the steer/observe surface the MCP tools wrap
(create/get/list/steer/evaluate). Dispatch is in-process via :class:`InProcessEngine`,
so there is no HTTP, no bearer token, and no ``/wake`` endpoint anymore.

Cognition (the planner + the evaluator) is injected; for the live service it
binds devclaw's ``claude --print`` callers at the goal-planner / evaluator tiers.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Optional

from ..advance_brief import display_goal as _display_goal
from . import delivery_strategy as _delivery_strategy
from . import evaluator as goal_evaluator
from . import merge as goal_merge
from . import remote_checks as goal_remote_checks
from . import summary as goal_summary
from . import triage as goal_triage
from .engine import InProcessEngine
from .evaluator import ClaudeCaller
from .models import Goal, GoalStatus
from .notify import HttpNotifier, Notifier, NullNotifier
from .store import GoalStore
from .tick import AUTODEPLOY_ENABLED, VERIFY_DONE, sweep_orphaned_refs, tick_all, tick_goal
from .transitions import Event
from ..dispatch_gate import next_window_open_ms, operator_block, schedule_blocks
from ..loom import trace as _trace
from ..state_store import StateStore, _now_ms
from ..task_queue import TaskQueue
from ..engine.workspace import prepare_workspace
from .. import trend_detector as _trend_detector_mod


def _iso_utc(ms: int) -> str:
    """UTC ISO-8601 for an epoch-ms value — the shape the goal-status
    timestamps already use on the read surfaces."""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()

if TYPE_CHECKING:
    from ..project_registry import ProjectRegistry


# Telemetry: opt-out via env. Default ON in production so heartbeats leave a
# durable trace in the sqlite traces table; set to "0" for tests / local runs
# where the per-tick PersistentTracer would just be noise. Tests inject their
# own tracer directly when they want to assert on events.
_TRACE_PERSIST_ENABLED = True


@dataclass(frozen=True)
class GoalConfig:
    goals_dir: Path
    notify_url: str
    tick_seconds: int
    verify_done: bool
    #: three-way: True/False pins the fleet; None (the default) = conditional —
    #: deploy on completion only if the workspace has an app surface (#554).
    autodeploy: "Optional[bool]" = AUTODEPLOY_ENABLED

    @staticmethod
    def from_env() -> "GoalConfig":
        raw = os.environ.get("DEVCLAW_GOALS_DIR", "~/memory/goals")
        return GoalConfig(
            goals_dir=Path(os.path.expanduser(raw)),
            notify_url=os.environ.get("DEVCLAW_GOAL_NOTIFY_URL", ""),
            tick_seconds=int(os.environ.get("DEVCLAW_GOAL_TICK_SECONDS", "900")),
            verify_done=VERIFY_DONE,
            autodeploy=AUTODEPLOY_ENABLED,
        )


def _should_repoke(outcomes: "dict[str, str]") -> bool:
    """Whether the heartbeat should immediately re-tick after this sweep.

    ``advanced``: a lifecycle transition (discovery→executing, firming→executing)
    happened without dispatching a task — the next planning tick should start
    now, not a full heartbeat interval later.

    ``conflict`` (T1/PR4+): a tick's write was abandoned because another writer
    landed mid-tick. The writers that matter most (steer_goal, evaluate_goal
    with corrections) poke the loop themselves, but a writer that doesn't
    (e.g. evaluate_goal returning no corrections, whose telemetry write still
    bumps the version) would otherwise leave the conflicted goal's pending
    work — steering, a just-finished action's detail — waiting out the full
    interval. Retrying immediately is bounded: the retry re-reads fresh state,
    and a successful re-plan consumes the very work that made it fire.
    """
    return any(v in ("advanced", "conflict") for v in outcomes.values())


class GoalService:
    def __init__(
        self,
        queue: TaskQueue,
        store: StateStore,
        config: Optional[GoalConfig] = None,
        *,
        evaluator_caller: Optional[ClaudeCaller] = None,
        summary_caller: Optional[ClaudeCaller] = None,
        triage_caller: Optional[ClaudeCaller] = None,
        notifier: Optional[Notifier] = None,
        project_registry: "Optional[ProjectRegistry]" = None,
    ) -> None:
        self._cfg = config or GoalConfig.from_env()
        # Wire the goal store onto the SHARED StateStore (the one that owns
        # devclaw.db) via the Tranche 1 `state=` seam, so goal_status lives
        # beside the tasks table — the atomic-dispatch join later PRs need, and
        # one fewer database to migrate. (Tests keep constructing
        # GoalStore(tmp_path) with no `state=`, self-creating a private
        # .goal-state.db, so they stay hermetic and unchanged.)
        self._goal_store = GoalStore(self._cfg.goals_dir, state=store)
        self._queue = queue
        self._store = store  # task/event store — read by tail_goal for live events
        self._engine = InProcessEngine(queue, store)
        self._evaluator_caller = evaluator_caller
        self._summary_caller = summary_caller
        self._triage_caller = triage_caller
        #: used only to resolve per-project automerge overrides (see _merger).
        #: None is fine — automerge just falls back to the global default.
        self._project_registry = project_registry
        self._notifier: Notifier = notifier or (
            HttpNotifier(self._cfg.notify_url) if self._cfg.notify_url else NullNotifier()
        )
        #: the goal heartbeat task + its in-process wake event
        self._loop_task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        #: trend detector — lazily constructed on first heartbeat that needs it
        #: so tests (which set DEVCLAW_TREND_ENABLED=0 or stub differently) and
        #: cold-starts don't import claude bindings prematurely.
        self._trend_detector_inst: "Optional[_trend_detector_mod.TrendDetector]" = None
        #: heartbeat freshness (#494) — stamped by tick_all on every completed
        #: full pass, read by /health + /node.json. In-memory on purpose: the
        #: signal is "THIS process's loop completed a pass", so it must die
        #: with the process rather than outlive it in the db.
        self.started_at_ms: int = _now_ms()
        self.last_tick_at_ms: Optional[int] = None

    @property
    def tick_seconds(self) -> int:
        """Heartbeat interval — exposed so the health surfaces can self-describe
        the staleness threshold instead of consumers duplicating config."""
        return self._cfg.tick_seconds

    # ---- cognition callers (bound on first real use) -----------------------

    def _evaluator(self) -> ClaudeCaller:
        if self._evaluator_caller is None:
            self._evaluator_caller = goal_evaluator.default_caller()
        return self._evaluator_caller

    def _summary(self) -> "Optional[ClaudeCaller]":
        """Cheap plain-language summarizer for owner-facing notifications. Off if
        DEVCLAW_GOAL_PLAIN_SUMMARY=0 (then owner messages send raw). Bound lazily."""
        if not goal_summary.PLAIN_SUMMARY_ENABLED:
            return None
        if self._summary_caller is None:
            self._summary_caller = goal_summary.default_caller()
        return self._summary_caller

    def _triage(self) -> "Optional[ClaudeCaller]":
        """Cognition caller for the propose-only self-triage interceptor. Off
        (returns None → every eligible owner ping stays on the raw path) when
        DEVCLAW_SELF_TRIAGE=0. Bound lazily so tests / cold-starts don't import
        the claude bindings prematurely — same shape as _summary()."""
        if not goal_triage.enabled():
            return None
        if self._triage_caller is None:
            self._triage_caller = goal_triage.default_caller()
        return self._triage_caller

    def _merger(self, goal: "Optional[Goal]" = None) -> "Optional[goal_merge.Merger]":
        """The auto-merger for hands-off delivery (decision 2) — resolved for
        THIS goal's repo: its owning project's ``automerge`` override if one is
        set, else the devclaw-wide ``DEVCLAW_GOAL_AUTOMERGE`` default. Merging
        to the default branch is opt-in either way. ``goal=None`` (e.g. the
        firming phase, which never merges) just falls back to the global
        default since there's no project to look up."""
        project_id = goal.project_id if goal is not None else None
        if not goal_merge.resolve_automerge(self._project_registry, project_id):
            return None
        strategy = goal_merge.resolve_merge_strategy(self._project_registry, project_id)
        return goal_merge.default_merger(strategy)

    def _merger_resolver(self) -> "Callable[[Goal], Optional[goal_merge.Merger]]":
        """Bound for tick_all, which ticks every goal in one sweep and needs a
        fresh per-goal automerge decision rather than one value for the whole
        fleet (a project override for goal A must not leak onto goal B)."""
        return self._merger

    def _remote_checker(self) -> "Optional[goal_remote_checks.RemoteChecker]":
        """Grounded remote-checks verification at the done-gate (the 2026-07-06
        benchmark fix). On by default; DEVCLAW_GOAL_REMOTE_CHECKS=0 disables —
        the checker itself fails open on infra errors, so opting out is only
        for environments with no gh at all."""
        if not goal_remote_checks.REMOTE_CHECKS_ENABLED:
            return None
        return goal_remote_checks.default_checker()

    def _verify_done(self, goal: "Optional[Goal]" = None) -> bool:
        """The done-gate re-check policy for THIS goal's repo: its owning
        project's ``verify_done`` override if set, else the devclaw-wide
        ``DEVCLAW_GOAL_VERIFY_DONE`` default (carried on the config). ``goal=None``
        or no registry → the global default."""
        default = self._cfg.verify_done
        if self._project_registry is None or goal is None:
            return default
        return self._project_registry.resolve_override(
            goal.project_id, "verify_done", default
        )

    def _verify_done_resolver(self) -> "Callable[[Goal], bool]":
        """Per-goal ``verify_done`` for tick_all's sweep — a project override
        for one goal must not leak onto another (same reason as
        :meth:`_merger_resolver`)."""
        return self._verify_done

    def _autodeploy(self, goal: "Optional[Goal]" = None) -> "Optional[bool]":
        """The on-complete auto-deploy policy for THIS goal's repo: its owning
        project's explicit ``autodeploy`` override if set, else the devclaw-wide
        default (carried on the config). Three-way on purpose: an explicit
        ``True``/``False`` (project pin) is honored as-is; ``None`` — nothing
        pinned anywhere — means CONDITIONAL, and the done-gate deploys only if
        the workspace has an app surface the preview launcher can serve (#554,
        see tick_donegate._auto_deploy). A pure library never gets a preview
        container unless its project pins ``autodeploy=on``."""
        default = self._cfg.autodeploy
        if self._project_registry is None or goal is None:
            return default
        return self._project_registry.resolve_override(
            goal.project_id, "autodeploy", default
        )

    def _autodeploy_resolver(self) -> "Callable[[Goal], Optional[bool]]":
        """Per-goal ``autodeploy`` for tick_all's sweep (same reason as
        :meth:`_merger_resolver`)."""
        return self._autodeploy

    def backfill_project_ids(self) -> int:
        """One-time migration (#524 P3): stamp ``project_id`` onto goals written
        before the field existed, resolving each goal's owning project by the
        LEGACY workspace-path match (``find_by_workspace_dir`` — its sole
        surviving caller, the runtime joins are all id-keyed now). Without this,
        a long-lived goal in flight at deploy time would lose its owning
        project's pinned knobs (automerge/verify_done/autodeploy) — e.g. the live
        ledger goal's ``automerge`` — and fall to the devclaw-wide defaults.

        Idempotent and zero-token: a goal that already has ``project_id``, or
        whose workspace matches no registered project, is skipped. A corrupt
        goal.yaml is skipped, never blocks startup. Returns the count stamped.
        Called once at startup (see server lifecycle)."""
        if self._project_registry is None:
            return 0
        stamped = 0
        for gid in self._goal_store.list_goal_ids():
            try:
                g = self._goal_store.load_goal(gid)
            except Exception:
                continue  # a half-written / corrupt goal.yaml never blocks startup
            if g.project_id:
                continue
            project = self._project_registry.find_by_workspace_dir(g.workspace_dir)
            if project is not None:
                self._goal_store.set_project_id(gid, project.id)
                stamped += 1
        return stamped

    def _trend_detector(self) -> "Optional[_trend_detector_mod.TrendDetector]":
        """The cross-session trend detector. ``None`` when disabled via
        ``DEVCLAW_TREND_ENABLED=0``. Constructed lazily so tests / cold starts
        don't import the claude bindings until something actually needs them.

        The detector is wired with narrow handles — it can write only to
        ``trends.md``, the sqlite ``meta`` table (cooldown timestamps), the
        ``traces`` table (observability), and the notifier. It has no handle
        to ``GoalStore`` writes, ``TaskQueue.submit``, or any other surface
        that would let it modify goals or AGENTS.md. The boundary is
        structural — see ``devclaw/trend_detector.py`` for the rule."""
        if not _trend_detector_mod.TREND_ENABLED:
            return None
        if self._trend_detector_inst is None:
            from ..llm_call import claude_with_model

            claude_caller = claude_with_model(
                _trend_detector_mod.TREND_MODEL, role="trend-detector",
            )

            # Fire-and-forget notify shim: TrendDetector calls notifier_send
            # synchronously, but Notifier.send is async. asyncio.create_task
            # detaches the send so the detector doesn't have to await; payload
            # is rendered into a single owner-readable line.
            notifier_inst = self._notifier

            def _notify_send(payload: dict) -> None:
                action = payload.get("proposed_action") or "(none)"
                text = (
                    f"📈 trend: {payload['signal']} ({payload['scope']}) — "
                    f"{payload['observation']}\n"
                    f"proposed action: {action}\n"
                    f"see: {payload['path']}"
                )
                try:
                    asyncio.create_task(notifier_inst.send(text))
                except RuntimeError:
                    # No running loop (e.g. called from sync context in tests).
                    # Drop silently — trends.md still has the entry.
                    pass

            self._trend_detector_inst = _trend_detector_mod.TrendDetector(
                state_store=self._store,
                goals_dir=self._cfg.goals_dir,
                claude_caller=claude_caller,
                notifier_send=_notify_send,
            )
        return self._trend_detector_inst

    def read_trends(self, scope: str = "harness_self", limit_chars: int = 5000) -> dict:
        """Read recent trend observations from ``trends.md`` for a given scope.

        ``scope='harness_self'`` → the global harness-self file (defaults into
        Denys's vault per ``DEVCLAW_TREND_HARNESS_SELF_FILE``).

        Anything else is treated as a workspace path → reads
        ``<scope>/.devclaw/trends.md``.

        The actual read is delegated to ``trend_detector.read_trends_text`` so
        the same primitive feeds both this MCP wrapper and the per-tick prompt
        injection in ``goal/tick.py``."""
        from ..trend_detector import HARNESS_SELF_TRENDS_PATH, read_trends_text

        if scope == "harness_self":
            path = HARNESS_SELF_TRENDS_PATH
        else:
            path = Path(scope) / ".devclaw" / "trends.md"
        text = read_trends_text(scope, limit_chars)
        return {"scope": scope, "path": str(path), "trends": text}

    # ---- the heartbeat -----------------------------------------------------

    def start(self) -> None:
        """Start the resident goal heartbeat. Idempotent. Called by the server
        after the task queue starts ticking."""
        if self._loop_task is None or self._loop_task.done():
            self._wake = asyncio.Event()
            self._loop_task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass
            self._loop_task = None

    def poke(self) -> None:
        """Wake the heartbeat NOW — wired to the task queue's on-settle hook so a
        finished engine task triggers an immediate goal tick (the in-process
        replacement for the old HTTP /wake). Safe to call from the event loop."""
        if self._wake is not None:
            self._wake.set()

    async def _loop(self) -> None:
        n = len(self._goal_store.list_goal_ids())
        sys.stderr.write(
            f"goal-layer: heartbeat {self._cfg.tick_seconds}s over {self._cfg.goals_dir} "
            f"({n} goal(s))\n"
        )
        assert self._wake is not None
        # Once-per-service-start orphan sweep (Tranche 1/PR7): re-adopts a
        # goal's lost in-flight task/program ref (STATUS.md truncated by a
        # crash mid-write, or leftover state from a pre-PR7 build). PR7's
        # atomic dispatch makes losing a ref mid-flight structurally
        # impossible on THIS build going forward, so this no longer needs to
        # run every tick — see tick.sweep_orphaned_refs / _readopt_orphaned_ref.
        # A sweep crash must not kill the heartbeat, same as a tick crash.
        try:
            swept = await sweep_orphaned_refs(self._goal_store, self._engine)
            if swept:
                sys.stderr.write(
                    f"goal-layer: startup sweep re-adopted {len(swept)} orphaned "
                    f"ref(s): {swept}\n"
                )
        except Exception as exc:  # noqa: BLE001 — a sweep crash must not kill the loop
            sys.stderr.write(f"goal-layer: startup sweep crashed: {exc}\n")
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._cfg.tick_seconds)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()
            try:
                outcomes = await self.tick_all()
                if _should_repoke(outcomes):
                    self.poke()
            except Exception as exc:  # noqa: BLE001 — a tick crash must not kill the loop
                sys.stderr.write(f"goal-layer: tick crashed: {exc}\n")
            # The cycle-window close report (ADR 0006 decision 3) — a mechanical,
            # ZERO-LLM scheduled edge, independent of any goal's activity. Placed
            # AFTER tick_all so it never precedes the cheap idle gates; its own
            # try so a hiccup (bad clock, notifier outage) never kills the loop.
            try:
                await self._maybe_emit_cycle_report()
            except Exception as exc:  # noqa: BLE001 — never kill the heartbeat
                sys.stderr.write(f"goal-layer: cycle-report edge crashed: {exc}\n")

    def _make_tracer(self, goal_id: str) -> "Optional[_trace.PersistentTracer]":
        """Per-goal-tick PersistentTracer that writes into the sqlite traces
        table. Tests flip _TRACE_PERSIST_ENABLED off when tracing is noise.
        Each tick gets a fresh ``trace_id`` so the full causal chain of one
        wakeup can be replayed via ``get_trace(goal_id)``.

        ``goals_dir`` is plumbed so cognition calls in the tick leave a full
        prompt+response transcript under ``<goal_dir>/transcripts/`` (T0.5) —
        the service already resolves the dir for GoalStore, so this is the
        cleanest seam to the goal *directory* the tracer can't derive from the
        goal id alone.
        """
        if not _TRACE_PERSIST_ENABLED:
            return None
        return _trace.PersistentTracer(
            store=self._store,
            trace_id=str(uuid.uuid4()),
            goal_id=goal_id,
            label=f"tick-{goal_id}",
            goals_dir=self._cfg.goals_dir,
        )

    async def tick_all(self) -> dict:
        outcomes = await tick_all(
            store=self._goal_store, engine=self._engine,
            evaluator_caller=self._evaluator(),
            notifier=self._notifier, notify_url="",
            verify_done=self._cfg.verify_done,
            verify_done_resolver=self._verify_done_resolver(),
            autodeploy=self._cfg.autodeploy, autodeploy_resolver=self._autodeploy_resolver(),
            summary_caller=self._summary(), merger_resolver=self._merger_resolver(),
            tracer_factory=self._make_tracer,
            trend_detector=self._trend_detector(),
            remote_checker=self._remote_checker(),
            triage_caller=self._triage(),
            mergeability_probe=goal_merge.pr_conflicting,
        )
        # Freshness stamp (#494) — only on a COMPLETED pass: a perpetually
        # crashing tick leaves this stale, which is exactly the signal an
        # external dead-man watcher needs to see.
        self.last_tick_at_ms = _now_ms()
        return {gid: o.value for gid, o in outcomes.items()}

    async def tick_one(self, goal_id: str) -> str:
        goal = self._goal_store.load_goal(goal_id)
        with _trace.tracer_scope(self._make_tracer(goal_id)):
            outcome = await tick_goal(
                goal_id, store=self._goal_store, engine=self._engine,
                evaluator_caller=self._evaluator(),
                notifier=self._notifier, notify_url="",
                verify_done=self._verify_done(goal),
                autodeploy=self._autodeploy(goal),
                summary_caller=self._summary(), merger=self._merger(goal),
                trend_detector=self._trend_detector(),
                remote_checker=self._remote_checker(),
                mergeability_probe=goal_merge.pr_conflicting,
            )
        return outcome.value

    async def _maybe_emit_cycle_report(self) -> Optional[str]:
        """The scheduled-edge owner (ADR 0006 decision 3): once per per-cycle
        run-window close, assemble the cycle's slice from existing rows and push
        it through the notifier. Returns the ``cycle_key`` it emitted, or None
        when the window hasn't closed / was already reported.

        ZERO LLM — cheap SQL + timestamp math only:
          1. compute the most-recent CLOSED window (pure clock math);
          2. an existence check on ``cycle_reports`` (the PK is the once-per-cycle
             idempotency guard) short-circuits every wakeup after the first;
          3. only past that gate does it read eval_outcomes/problems and write.

        The write goes THROUGH the store (single-writer: ``cycle_reports`` is
        only ever written by ``record_cycle_report``). ``sent_at`` is NULL when
        the notifier doesn't confirm the push (unconfigured / failed) — a
        log-only report, never an error."""
        from . import cycle_report as _nr

        now = _now_ms()
        win = _nr.most_recent_closed_window(now)
        if win is None:  # unresolvable schedule (bad tz/time) — skip, never crash
            return None
        cycle_key, start_ms, end_ms = win
        if self._store.cycle_report_exists(cycle_key):
            return None  # already reported this cycle (idempotent)

        report = _nr.assemble_cycle_report(self._store, cycle_key, start_ms, end_ms)
        # Push best-effort; NullNotifier / a relay outage returns False → log-only.
        sent = False
        try:
            sent = await self._notifier.send(report.summary)
        except Exception as exc:  # noqa: BLE001 — a notifier hiccup is never fatal
            sys.stderr.write(f"goal-layer: cycle-report notify failed: {exc}\n")
        self._store.record_cycle_report(
            cycle_key=cycle_key,
            window_start_ms=start_ms,
            window_end_ms=end_ms,
            clean=report.clean,
            idle=report.idle,
            wedges_json=json.dumps(report.wedges),
            pauses_json=json.dumps(report.pauses),
            summary=report.summary,
            sent_at=(now if sent else None),
        )
        if not sent:
            # Log-only path: the report still exists in the table; surface it so a
            # notifier-less run still leaves a trace.
            sys.stderr.write(f"goal-layer: cycle report {cycle_key} (log-only):\n{report.summary}\n")

        # Self-issue-filing Stage 1 (docs/proposals/self-issue-filing.md): at this
        # SAME once-per-cycle edge (past the cycle_report_exists idempotency gate,
        # so it fires once per cycle, never per tick), turn recurring problems into
        # GitHub issues on the devclaw repo and age out stale ones. ZERO LLM.
        # Env-gated (DEVCLAW_SELF_REPO unset ⇒ no-op, shells nothing — the default
        # and every test path), and best-effort: a GitHub hiccup logs and is
        # swallowed here, it never wedges the cycle edge (fail-loud-not-fatal).
        try:
            from . import self_issue as _si

            si = await _si.run_self_issue_filing(
                self._store, cycle_key=cycle_key,
                start_ms=start_ms, end_ms=end_ms, now_ms=now,
            )
            line = si.report_line()
            if line:
                sys.stderr.write(f"goal-layer: cycle {cycle_key} {line}\n")
        except Exception as exc:  # noqa: BLE001 — filing never fails the cycle edge
            sys.stderr.write(f"goal-layer: self-issue filing failed: {exc}\n")

        # Self-issue-filing STAGE 2 (P2 — FIX pickup, proposal §5A): at this SAME
        # once-per-cycle edge, turn a human-`accepted` self-filed issue into ONE
        # `one_shot` self-fix goal that opens a PR for HUMAN review — NO auto-merge
        # (the tiered classifier is deferred to P2.1/P2.2). ZERO LLM to detect (a
        # `gh issue list` + pure selection); env-gated on DEVCLAW_SELF_REPO (unset ⇒
        # no-op, shells nothing) and best-effort — a pickup hiccup never wedges the
        # cycle edge. Goal creation stays here (`self.create_goal`, injected).
        try:
            from . import self_issue as _si2

            picked = await _si2.run_self_fix_pickup(self.create_goal)
            pline = picked.report_line()
            if pline:
                sys.stderr.write(f"goal-layer: cycle {cycle_key} {pline}\n")
        except Exception as exc:  # noqa: BLE001 — pickup never fails the cycle edge
            sys.stderr.write(f"goal-layer: self-fix pickup failed: {exc}\n")
        return cycle_key

    # ---- steer / observe surface (wrapped by MCP tools) --------------------

    def create_goal(
        self, goal_id: str, *, objective: str, workspace_dir: str,
        cadence: str = "1d", repo_url: Optional[str] = None,
        verify_cmd: Optional[str] = None, open_pr: bool = True,
        done_when: str = "", backlog: Optional[list[str]] = None,
        spec: str = "",
        mode: str = "long_lived",
        strictness: str = "trust",
        project_id: Optional[str] = None,
    ) -> dict:
        # Chef admission ("verified on all sides"). Goals that fail structural
        # checks are REJECTED with a structured condition list — the caller
        # (waiter or upstream chain) must fix and re-file. Warnings still flow
        # through to the result dict as before. See devclaw/goal/admission.py.
        from .admission import GoalAdmissionRejected, verify_goal as _verify

        if mode not in ("long_lived", "one_shot"):
            raise ValueError(f"unknown goal mode {mode!r} — expected 'long_lived' or 'one_shot'")
        if strictness not in ("trust", "strict"):
            raise ValueError(f"unknown strictness {strictness!r} — expected 'trust' or 'strict'")

        admission = _verify(
            objective=objective, workspace_dir=workspace_dir, done_when=done_when,
            backlog=backlog, repo_url=repo_url, verify_cmd=verify_cmd, spec=spec,
        )
        if not admission.admitted:
            raise GoalAdmissionRejected(admission)

        goal = self._goal_store.create_goal(
            goal_id, objective=objective, workspace_dir=workspace_dir, cadence=cadence,
            repo_url=repo_url, verify_cmd=verify_cmd, open_pr=open_pr,
            done_when=done_when, backlog=backlog, mode=mode, strictness=strictness,
            project_id=project_id,
        )
        # The waiter may have grilled scope before filing the order — persist the
        # spec it landed on so the evaluator judges done against the shared contract.
        if spec and spec.strip():
            self._goal_store.write_spec(goal_id, spec)
        # ONE execution path (spec 008 shrink): both modes start executing —
        # the worker plans via speckit in-sandbox; the investigating/firming
        # detour is gone. "Executing" must be PERSISTED, not implied: a NULL
        # lifecycle reads-as-executing on every display surface, but
        # delivery_strategy.resolve_strategy requires the EXPLICIT
        # ``executing`` string to put the goal on its ``goal/<id>``
        # accumulation branch — NULL silently downgrades a fresh goal to
        # per-action reset-to-main delivery, the exact amnesia #486 exists to
        # kill (live-found: ledger night 1, 2026-08-10 — three unmerged
        # scaffold PRs, main never moved).
        self._goal_store.save_status(goal_id, GoalStatus(lifecycle="executing"))
        self._goal_store.append_log(goal_id, "goal created")
        self.poke()  # advance it on the next loop turn without waiting a full interval
        result = self.get_goal(goal_id)
        if admission.warnings:
            # Keep the historical string-list shape so existing callers /
            # tests / dashboards don't break — warnings were already prose.
            result["warnings"] = [c.message for c in admission.warnings]
        return result

    def verify_goal(
        self, *, objective: str, workspace_dir: str,
        repo_url: Optional[str] = None, verify_cmd: Optional[str] = None,
        done_when: str = "", backlog: Optional[list[str]] = None,
        spec: str = "",
    ) -> dict:
        """Pre-flight check the waiter calls before ``create_goal`` so the
        customer sees fixable conditions BEFORE thinking the order was filed.
        Same validations as ``create_goal`` runs internally; never mutates
        state; returns the structured :class:`AdmissionResult` as a dict."""
        from .admission import verify_goal as _verify

        return _verify(
            objective=objective, workspace_dir=workspace_dir, done_when=done_when,
            backlog=backlog, repo_url=repo_url, verify_cmd=verify_cmd, spec=spec,
        ).to_dict()

    def _dispatch_hold(self, goal_id: Optional[str] = None) -> Optional[dict]:
        """Why NEW dispatch is held right now, or None when it can flow.

        Read-only projection for the status surfaces (get_goal / list_goals /
        tail_goal): a held instance must SAY so — a quota pause or a closed
        run-window otherwise renders as `in_flight`+`blocked_on: null`, i.e.
        indistinguishable from healthy idle (the 2026-07-20 silent window-hold).
        Precedence mirrors the write path: quota pause, then manual hold, then
        the global window, then the per-goal window. Never raises — a read
        surface degrades to None over a bad clock/schedule, it doesn't 500."""
        try:
            now = _now_ms()
            until, reason = self._store.global_pause()
            if until and now < until:
                return {"kind": "quota_pause", "reason": reason,
                        "until": _iso_utc(until)}
            hold = self._store.operator_hold()
            schedule = self._store.get_run_schedule()
            blocked, why = operator_block(hold, schedule, now)
            if not blocked and goal_id is not None:
                schedule = self._store.get_run_schedule(goal_id)
                blocked, why = schedule_blocks(schedule, now)
            if not blocked:
                return None
            out: dict = {
                "kind": "operator_hold" if hold[0] else "run_window",
                "reason": why,
            }
            if not hold[0]:
                nxt = next_window_open_ms(schedule, now)
                if nxt is not None:
                    out["until"] = _iso_utc(nxt)
            return out
        except Exception:  # noqa: BLE001 — display path; see docstring
            return None

    def has_goal(self, goal_id: str) -> bool:
        """Cheap existence check (no goal load). The console problem-lifecycle
        tracker uses it to tell a *filed* issue whose self-fix goal is running
        (``fixing``) apart from one merely sitting in the backlog — N2/#372. The
        join key is the deterministic ``self-fix-issue-<n>`` id."""
        return self._goal_store.exists(goal_id)

    def _delivery_view(self, goal_id: str) -> dict:
        """The resolved delivery strategy + goal branch (#495) — the single most
        load-bearing runtime decision per goal, previously visible nowhere but a
        workspace reflog on the VPS. Display path: ``resolve_strategy`` keeps its
        fail-loud ``on_corrupt="raise"`` semantics for the DELIVERY path, but a
        read surface must not 500 over a corrupt contract (the tick already
        blocks the goal loudly; ``blocked_on`` carries the signal) — so here a
        resolution failure degrades to an explicit ``"unresolvable"``, never to
        a silently-wrong strategy name."""
        try:
            strat = _delivery_strategy.resolve_strategy(self._goal_store, goal_id)
        except Exception:
            return {"delivery_strategy": "unresolvable", "goal_branch": None}
        return {
            "delivery_strategy": strat.name,
            "goal_branch": strat.goal_branch(goal_id),
        }

    def get_goal(self, goal_id: str) -> dict:
        if not self._goal_store.exists(goal_id):
            raise KeyError(goal_id)
        g = self._goal_store.load_goal(goal_id)
        s = self._goal_store.load_status(goal_id)
        return {
            "id": g.id,
            "objective": g.objective,
            "done_when": g.done_when,
            "cadence": g.cadence,
            "workspace_dir": g.workspace_dir,
            "backlog": g.backlog,
            "mode": g.mode,
            "strictness": g.strictness,
            "phase": s.phase,
            # RAW stored lifecycle (#496): a legacy row's NULL renders as null,
            # never coalesced to "executing" — resolve_strategy branches on the
            # raw value, and a display that coalesces actively misleads
            # diagnosis (the #493 bug lived exactly in that gap).
            "lifecycle": s.lifecycle,
            **self._delivery_view(goal_id),
            # Display guard (#550): rows written before the dispatch-side fix
            # may still store the raw advance brief — never surface it as the
            # goal's "next"; render the embedded objective instead.
            "next": _display_goal(s.next),
            "blocked_on": s.blocked_on,
            "blocked_kind": s.blocked_kind,
            "in_flight": (
                {"tool": s.in_flight.tool, "id": s.in_flight.id,
                 "is_done_check": s.in_flight.is_done_check}
                if s.in_flight else None
            ),
            "actions_dispatched": s.actions_dispatched,
            "progress": {"last_at": s.last_progress_at, "stalled": s.no_progress_notified},
            "direction": (
                {"verdict": s.last_eval_verdict, "at": s.last_eval_at, "note": s.last_eval_note}
                if s.last_eval_verdict else None
            ),
            "recent_log": self._goal_store.recent_log(goal_id, n=15),
            "phase_history": [dict(e) for e in s.phase_history],
            "dispatch_hold": self._dispatch_hold(goal_id),
        }

    def tail_goal(
        self,
        goal_id: str,
        *,
        log_lines: int = 40,
        deliveries_chars: int = 6000,
        event_limit: int = 30,
    ) -> dict:
        """The 'watch it run' surface — richer than get_goal, no SSH needed. On top
        of get_goal's phase/direction/log it returns the grounded deliveries tail
        (what each action actually shipped), the discovery brief + any waiter-
        provided spec, and the tail of the LIVE event stream from whatever
        task/program is in flight (so you can see the agent acting in near real
        time). Everything is bounded — read-only, never mutates the goal."""
        if not self._goal_store.exists(goal_id):
            raise KeyError(goal_id)
        g = self._goal_store.load_goal(goal_id)
        s = self._goal_store.load_status(goal_id)

        live_events: list[dict] = []
        if s.in_flight is not None:
            ref = s.in_flight
            kwargs = (
                {"task_id": ref.id} if ref.ref_kind == "task" else {"program_id": ref.id}
            )
            # list_events is ASC + LIMIT (first N); pull a wide window and tail it
            # in Python so we get the MOST RECENT events of a long-running task.
            evs = self._store.list_events(limit=10000, **kwargs)
            for e in evs[-event_limit:]:
                preview = (e.payload_json or "")[:200]
                live_events.append(
                    {"type": e.type, "source": e.source, "ts": e.ts, "preview": preview}
                )

        return {
            "id": g.id,
            "objective": g.objective,
            "done_when": g.done_when,
            "phase": s.phase,
            # RAW stored lifecycle (#496) — see get_goal; null is honest.
            "lifecycle": s.lifecycle,
            **self._delivery_view(goal_id),
            # Display guard (#550) — see get_goal: legacy rows may store the brief.
            "next": _display_goal(s.next),
            "blocked_on": s.blocked_on,
            "actions_dispatched": s.actions_dispatched,
            "in_flight": (
                {"tool": s.in_flight.tool, "id": s.in_flight.id,
                 "ref_kind": s.in_flight.ref_kind,
                 "is_done_check": s.in_flight.is_done_check}
                if s.in_flight else None
            ),
            "progress": {"last_at": s.last_progress_at, "stalled": s.no_progress_notified},
            "direction": (
                {"verdict": s.last_eval_verdict, "at": s.last_eval_at,
                 "note": s.last_eval_note}
                if s.last_eval_verdict else None
            ),
            "recent_log": self._goal_store.recent_log(goal_id, n=log_lines),
            "deliveries": self._goal_store.recent_deliveries(goal_id, chars=deliveries_chars),
            "spec": self._goal_store.read_spec(goal_id),
            "live_events": live_events,
            "dispatch_hold": self._dispatch_hold(goal_id),
        }

    def list_goals(self) -> list[dict]:
        # Includes `project_id` so project_registry.project_rollup (and the
        # server rollup twins) derive project↔goal association by the project
        # reference key (#524 P3) — re-keyed off the old normalized-workspace
        # match so a workspace rename or shared path can't drift it.
        out = []
        # Account-wide hold (quota pause / manual hold / global window) computed
        # ONCE — per-goal windows are get_goal detail, not worth N reads here.
        hold = self._dispatch_hold()
        for gid in self._goal_store.list_goal_ids():
            g = self._goal_store.load_goal(gid)
            s = self._goal_store.load_status(gid)
            out.append({
                "id": gid,
                "objective": g.objective[:140],
                "workspace_dir": g.workspace_dir,
                "project_id": g.project_id,
                "phase": s.phase,
                # RAW stored lifecycle (#496) — see get_goal; null is honest.
                "lifecycle": s.lifecycle,
                **self._delivery_view(gid),
                "blocked_on": s.blocked_on,
                "progress": {"last_at": s.last_progress_at, "stalled": s.no_progress_notified},
                "direction": s.last_eval_verdict,
                "actions_dispatched": s.actions_dispatched,
                "strictness": g.strictness,
                "dispatch_hold": hold,
            })
        return out

    def steer_goal(self, goal_id: str, message: str) -> dict:
        if not self._goal_store.exists(goal_id):
            raise KeyError(goal_id)
        self._goal_store.append_steering(goal_id, [message], source="denys")
        self._goal_store.append_log(goal_id, f"steered: {message[:160]}")
        # Steering unblocks a blocked goal — flip it to idle and clear the
        # dispatch counter so the cap doesn't re-trigger on the very next tick.
        # `s.phase == "blocked"` also matches firming-blocked (lifecycle=
        # "firming"): UNBLOCK from FIRMING_BLOCKED legally targets
        # FIRMING_IDLE, and replace(s, phase="idle") on a firming-lifecycle
        # status derives exactly that — one call covers both cases. A
        # TransitionConflict here (another writer landed between the load
        # above and this write) is left to propagate as a visible MCP error —
        # practically unreachable since nothing awaits between them.
        s = self._goal_store.load_status(goal_id)
        if s.phase == "blocked":
            # heal_attempts=0 / next_heal_at=None: a HUMAN lifting the block
            # restores the full mechanical auto-heal budget and clears the
            # prep-recheck backoff window (see tick_guards._autoheal_corrupt_doc
            # / _autoheal_prep) — the damping cap protects against unattended
            # flapping, and the owner just attended.
            # blocked_on="" so the answered question stops showing as live in
            # get_goal/list_goals/the console — resume_goal already clears it,
            # steer_goal used to leak it (a HUMAN answering via steer resolves
            # the reason exactly as a resume does). blocked_kind is cleared by
            # the store's non-blocked-write normalization; blocked_on is not, so
            # it must be cleared here explicitly.
            self._goal_store.transition(
                goal_id, Event.UNBLOCK,
                replace(s, phase="idle", blocked_on="", actions_dispatched=0,
                        heal_attempts=0, next_heal_at=None, donegate_rounds=0),
                expect=s,
            )
        self.poke()
        return {"goal_id": goal_id, "steered": True, "message": message}

    def set_strictness(self, goal_id: str, strictness: str) -> dict:
        """Flip the goal's gate strictness dial (ADR 0007) — the verb behind the
        console toggle, the HTTP API, and the MCP tool. A narrow single-field
        mutation (not a contract patch): dial-able gate failures either block
        (``strict``) or ship-with-a-caveat (``trust``). Applies to future
        dispatches. Raises ValueError on a bad value, KeyError on unknown goal.
        """
        if not self._goal_store.exists(goal_id):
            raise KeyError(goal_id)
        g = self._goal_store.set_strictness(goal_id, strictness)
        self._goal_store.append_log(goal_id, f"strictness set to {strictness}")
        self.poke()
        return {"goal_id": goal_id, "strictness": g.strictness}

    def resume_goal(self, goal_id: str) -> dict:
        """Recovery verb: "the blocker is cleared — re-attempt the SAME
        contract." Fires the existing UNBLOCK edge (BLOCKED → EXECUTING_IDLE)
        WITHOUT recording steering, so a pure resume never becomes a direction
        override in the next planner prompt (that's :meth:`steer_goal`'s job).
        Not a field patch either — the goal contract (objective / done_when /
        backlog) is untouched.

        Legible no-op instead of an error: a non-blocked goal has no UNBLOCK
        edge — return a message, never raise ``IllegalTransition`` (idempotent:
        a second resume is a no-op).
        """
        if not self._goal_store.exists(goal_id):
            raise KeyError(goal_id)
        s = self._goal_store.load_status(goal_id)
        if s.phase != "blocked":
            return {
                "goal_id": goal_id, "resumed": False,
                "message": f"goal is not blocked (phase={s.phase!r}) — nothing to resume",
            }
        was_blocked_on = s.blocked_on or ""
        # Same unblock write shape as steer_goal (actions_dispatched=0 so the
        # dispatch cap doesn't re-fire on the first re-plan), plus two resume-
        # specific fields: blocked_on cleared (the reason is resolved — don't
        # display it as live), and last_plan_at=None so cadence_due() reads
        # True on the next tick. A bare UNBLOCK would otherwise park the goal
        # until its cadence elapses — the tick's should_plan is
        # `work OR cadence_due`, and resume (unlike steering) adds no work.
        # A TransitionConflict propagates as a visible MCP error, exactly like
        # steer_goal — practically unreachable since nothing awaits between
        # the load above and this write.
        # heal_attempts=0 / next_heal_at=None: same as steer_goal — a HUMAN
        # lifting the block vouches for the goal, so the mechanical auto-heal
        # budget (and any prep-backoff window) is restored in full.
        self._goal_store.transition(
            goal_id, Event.UNBLOCK,
            replace(s, phase="idle", blocked_on="", actions_dispatched=0, last_plan_at=None,
                    heal_attempts=0, next_heal_at=None, donegate_rounds=0),
            expect=s,
        )
        self._goal_store.append_log(
            goal_id,
            f"resumed: blocker cleared ({was_blocked_on[:120]}) — re-attempting the same contract",
        )
        self.poke()
        return {"goal_id": goal_id, "resumed": True, "was_blocked_on": was_blocked_on}

    async def evaluate_goal(self, goal_id: str) -> dict:
        """Force a direction evaluation NOW (artifact-grounded) and return the
        verdict. Reports + steers (corrections → inbox); does not block on demand."""
        if not self._goal_store.exists(goal_id):
            raise KeyError(goal_id)
        g = self._goal_store.load_goal(goal_id)
        s = self._goal_store.load_status(goal_id)
        # Same grounding as the tick paths (triage F3): the workspace snapshot
        # + the agreed spec. The on-demand eval used to omit BOTH — its
        # "corrections" could describe the wrong repo and ignore the contract
        # the tick-path evaluator judges against.
        repo_context = await goal_evaluator._repo_context(g.workspace_dir)
        ev = await goal_evaluator.evaluate(
            g, s, self._goal_store.recent_log(goal_id),
            self._goal_store.recent_deliveries(goal_id),
            claude_caller=self._evaluator(),
            spec=self._goal_store.read_spec(goal_id),
            repo_context=repo_context,
        )
        now = self._goal_store.now_iso()
        # Telemetry-only (verdict/note) — column-only path, not a transition.
        self._goal_store.update_status_fields(
            goal_id, last_eval_verdict=ev.verdict, last_eval_at=now, last_eval_note=ev.rationale[:300],
        )
        self._goal_store.append_log(goal_id, f"on-demand direction: {ev.verdict} — {ev.rationale[:200]}")
        if ev.corrections:
            self._goal_store.append_steering(goal_id, ev.corrections, source="auto-eval")
            self.poke()
        return {
            "goal_id": goal_id, "verdict": ev.verdict,
            "rationale": ev.rationale, "corrections": ev.corrections,
            "question": ev.question,
        }

    def cancel_goal(self, goal_id: str) -> dict:
        """Abort a durable goal. Sets phase to 'cancelled' (terminal — skipped on
        every future tick) and tears down any in-flight task or program. Returns
        a graceful no-op response if the goal is already in a terminal phase."""
        if not self._goal_store.exists(goal_id):
            raise KeyError(goal_id)
        s = self._goal_store.load_status(goal_id)
        if s.phase in ("cancelled", "done"):
            return {
                "goal_id": goal_id,
                "cancelled": False,
                "phase": s.phase,
                "reason": f"goal is already in terminal phase '{s.phase}'",
            }
        if s.in_flight is not None:
            ref = s.in_flight
            if ref.ref_kind == "task":
                self._queue.cancel_task(ref.id)
            else:
                self._queue.cancel_program(ref.id)
        self._goal_store.transition(
            goal_id, Event.CANCEL, replace(s, phase="cancelled", in_flight=None), expect=s,
        )
        self._goal_store.append_log(goal_id, "goal cancelled")
        return {"goal_id": goal_id, "cancelled": True, "phase": "cancelled"}
