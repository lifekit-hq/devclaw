"""Settle — the execute/settle path of the queue, split out as a mixin.

:class:`SettleMixin` carries the per-task execution spine: ``_execute`` (branch
prep -> run -> deliver -> settle), ``_run_and_settle`` (the retry loop + the
three-axis settle cascade — its ordering comments are load-bearing and moved
here INTACT), the branch-target prep, the per-attempt event sink, the
trust-mode gate-advisory recorder, and the browser-gate reachability escape
valve. The module-level helpers it resolves as globals (``_capture_change``,
``_git_diff``, ``deliver_change``, the fast-fail markers, ``TASK_MAX_RETRIES``
& friends) live HERE now — tests that patch those seams patch THIS namespace.

Split out of ``TaskQueue`` as a mixin on the SAME instance — every method here
runs against the ``self._store`` / ``self._merge_queue`` / ``self._runner``
the base ``TaskQueue`` owns, so the single-writer / fail-closed semantics are
byte-identical to the pre-split monolith. This module must never import
``devclaw.task_queue`` at runtime (the dependency points the other way).
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

from .. import config as _config
from .. import project_manifest as _manifest
from .. import validation_loop as _validation
from ..delivery import deliver_change, delivery_failed, devclaw_commit_title
from ..delivery.integrate import commit_lane, integrate_lane
from ..engine import EngineEvent, EngineRequest
from ..loom.limits import classify_failure, pause_seconds
from ..quality.browser_gate import browser_run_verdict
from ..quality.change_advisories import change_advisories
from ..quality.gate_policy import Consequence, gate_consequence
from ..quality.gate_pipeline import GateInput, run_pipeline
from ..quality.task_gates import (
    _BrowserGate,
    _IntegrityGate,
    _MaterializeGate,
    _ReviewGate,
    _ScopeGate,
    _VerifyGate,
    _has_playwright_config,
)
# Module globals on purpose (like deliver_change) so tests patch them on THIS
# namespace: the direct-dispatch branch-target wire (v1-helper-resurface PR-2)
# preps a caller-pinned target_branch before the engine runs.
from ..engine.workspace import (
    WorkspaceError,
    _default_branch as _workspace_default_branch,
    prepare_workspace,
)
from ..state_store import TaskKind, _now_ms
# The git ``_sync`` helpers are module globals here so the async wrappers below
# (patched by tests on THIS namespace) resolve them at call time.
from ..task_git import (
    BRANCH_STALE_THRESHOLD,
    _base_branch_error_sync,
    _git_commit_exists_sync,
    _git_diff_sync,
    _git_head_sync,
    _review_repo_context_sync,
    _wip_snapshot_sync,
    branch_staleness_sync as _branch_staleness_sync,
)
from ..task_change import (
    ERROR as _CHANGE_ERROR,
    NO_CHANGE as _CHANGE_NONE,
    NO_REPO as _CHANGE_NO_REPO,
    CHANGE as _CHANGE_SOME,
    ChangeSet,
    materialization_message,
    materialize_worktree_sync,
)

if TYPE_CHECKING:
    from ..engine import Engine
    from ..loom.merge_queue import MergeQueue
    from ..state_store import StateStore, Task

#: per-task wall-clock cap (seconds). A run that exceeds it is cancelled — which
#: tears down its sandbox via run_sandcastle's finally — and the task is marked
#: failed, so a hung agent fails CLEANLY instead of burning Pro/Max quota forever
#: (the live smoke leaked a container on exactly this — a silent post-init hang).
#: It's a coarse backstop: a no-progress timer would kill a silent hang faster,
#: but this also catches busy-loops. <=0 disables. Generous default so a
#: legitimately long feature build isn't reaped mid-flight — 1800s proved NOT
#: generous enough for real program work (2026-07-09: a mid-stack closeloop
#: implement_feature doing honest work was reaped at 30min, failing the whole
#: program).
TASK_TIMEOUT_S = _config.TASK_TIMEOUT_S
#: how many times to RE-RUN a task that fails its verify gate (or errors), each
#: time with the failure fed back into the goal, before escalating. The gate
#: catches a bad result; retry gives the agent a bounded second chance to
#: self-correct (a fix that didn't fully land, a transient error). 0 disables.
#: NOT applied to timeouts — a stuck run would likely just hang again.
TASK_MAX_RETRIES = _config.TASK_MAX_RETRIES
#: Stable marker prefixed on the feedback string when the review gate CRASHED —
#: it couldn't produce a verdict at all (e.g. an oversized/unparseable diff makes
#: the review model return non-JSON). The retry loop treats this differently from
#: a genuine ``request_changes``: a crash is not a defect the agent can fix by
#: re-running (the same diff re-crashes the gate identically), so it fails FAST
#: instead of burning the retry budget and then the goal-level re-dispatch loop.
_REVIEW_CRASH_MARKER = "review gate crashed (failing closed):"
#: Stable marker prefixed on the failure string when the WORKER itself honestly
#: self-reported it cannot finish (result ``status == "blocked"`` — a missing
#: capability, contradictory/impossible instructions). Like the review-crash
#: marker, this fails FAST and CLOSED: a re-run reproduces the same block
#: identically, so retrying only burns the budget and then the goal-level
#: re-dispatch loop. It is NEVER treated as an approval (never settles ``done``);
#: the reason rides the failure so the goal layer surfaces it to the owner.
_WORKER_BLOCKED_MARKER = "worker reported BLOCKED:"
#: Substring the engine surfaces when the worker's conversation OVERFLOWED the
#: model context window (full shape: ``Conversation run failed for id=...:
#: Internal error: Prompt is too long``). Unlike the two markers above this one
#: is not prefixed by us — it rides mid-string inside the engine's error — so
#: it is matched with ``in``, not ``startswith``; anchoring on the engine's
#: ``Internal error:`` prefix restores the misroute shield the other markers
#: get from ``startswith`` (a target repo's verify output that merely SAYS
#: "Prompt is too long" must not be routed here). The overflow is DETERMINISTIC
#: for a given task scope, and the retry prompt APPENDS the prior failure
#: history to the instruction, so a re-run is strictly LARGER and overflows
#: again. Fails FAST + CLOSED like the review-crash path: never done, never
#: paused, no retry — the task must be re-dispatched with smaller scope.
_PROMPT_TOO_LONG_MARKER = "Internal error: Prompt is too long"
#: The reasoned escape valve for the browser gate's one false positive (a UI
#: change not rendered in the running app — see quality/reachability.py). Always
#: ON: it is strictly safe (can only RELAX a would-be block, and only on an
#: affirmatively-grounded `reachable == "no"`). A disabled browser gate makes
#: this moot (never consulted). (Formerly DEVCLAW_GOAL_BROWSER_REACHABILITY, #410.)
BROWSER_REACHABILITY_ENABLED = True
#: how many usage-limit pause→requeue cycles a single task gets before it is
#: FAILED instead of requeued again. A permanently-failing task whose error text
#: happens to match the quota/rate regexes would otherwise loop pause→requeue→
#: re-run forever — the workspace breaker never sees it (a paused task never
#: becomes a `failed` row). The global pause is still set either way: the
#: account really is limited; only the doomed task stops riding it.
MAX_PAUSE_REQUEUES = 5

#: _run_and_settle returns this when a task was paused for a quota limit (not
#: settled): the task is back to 'pending' and the global pause holds dispatch.
class _PausedSentinel:
    pass


_PAUSED = _PausedSentinel()


async def _git_diff(host_dir: str, base: str = "", head: str = "") -> "str | None":
    """Async wrapper — runs the blocking git diff in a thread so it never blocks
    the event loop or trips the asyncio-subprocess child-watcher hang. Looks up
    :func:`_git_diff_sync` as a module global so tests can patch it here.

    ``None`` means git could not answer — NOT an empty change (spec 013)."""
    return await asyncio.to_thread(_git_diff_sync, host_dir, base, head)


async def _materialize_worktree(
    host_dir: str, base: str, *, task_id: str, message: str
) -> dict:
    """Async wrapper around :func:`~devclaw.task_change.materialize_worktree_sync`
    — same thread-offload rationale as :func:`_git_diff`, and a module global so
    tests can patch it here."""
    return await asyncio.to_thread(
        materialize_worktree_sync, host_dir, base, task_id=task_id, message=message
    )


async def _capture_change(
    workspace_dir: str, base: str, *, task_id: str, message: str
) -> ChangeSet:
    """**The** answer to "what did the agent change?" (spec 013, #630).

    Materialize once — stage everything the agent left and write it into a
    commit — then render the ``base..head`` range as a unified diff. Every
    consumer (each gate, the change-size projection, the advisory checks,
    delivery) reads THIS object. There is no second computation to disagree
    with, which is the whole mechanism: the property "the exact span the gates
    judged and delivery ships" used to be a sentence in a docstring backed by a
    request to a language model in a worker skill.

    Never raises — a crash becomes a :data:`~devclaw.task_change.ERROR`
    ChangeSet, which the materialize gate fails CLOSED on (#186). An empty
    result is never quietly reported as "no change".
    """
    try:
        mat = await _materialize_worktree(
            workspace_dir, base, task_id=task_id, message=message
        )
    except Exception as err:  # noqa: BLE001 — undeterminable ⇒ loud, not silent
        return ChangeSet(
            status=_CHANGE_ERROR, base_sha=base,
            reason=f"{err.__class__.__name__}: {err}",
        )
    if mat["status"] == _CHANGE_ERROR:
        return ChangeSet(status=_CHANGE_ERROR, base_sha=base, reason=mat["reason"])

    head = mat["head"]
    common = {
        "base_sha": base, "head_sha": head,
        "agent_authored": bool(mat["agent_authored"]),
        "materialized": bool(mat["materialized"]),
    }
    try:
        diff = await _git_diff(workspace_dir, base, head)
    except Exception as err:  # noqa: BLE001
        diff = None
        mat["reason"] = mat["reason"] or f"{err.__class__.__name__}: {err}"
    if diff is None:
        if mat["status"] == _CHANGE_NO_REPO:
            # Not a repository: nothing can be published from here either
            # (delivery fails loudly on the same condition), so there is no
            # judged-vs-shipped divergence to close. Reported, never silent.
            return ChangeSet(
                status=_CHANGE_NO_REPO, base_sha=base,
                reason=mat["reason"] or f"{workspace_dir} is not a git repository",
            )
        return ChangeSet(
            status=_CHANGE_ERROR,
            reason=(
                f"git could not diff {base[:8] or '(no base)'}..{head[:8] or '(no head)'} "
                f"in {workspace_dir}"
            ),
            **common,
        )
    if mat["status"] == _CHANGE_NO_REPO:
        # A patched/stubbed diff seam answered for a non-repo workspace (the
        # stubbed-engine test shape). Honour the answer, keep the outcome
        # labelled so nothing upstream reads it as a real delivered increment.
        return ChangeSet(
            status=_CHANGE_NO_REPO, base_sha=base, diff=diff,
            reason=mat["reason"] or f"{workspace_dir} is not a git repository",
        )
    status = _CHANGE_SOME if diff.strip() else _CHANGE_NONE
    return ChangeSet(status=status, diff=diff, **common)


def _diff_stats(diff: str) -> dict | None:
    """Files/insertions/deletions counted from unified-diff TEXT — the exact
    span the gates judged and delivery ships, with no extra git call. Since
    spec 013 that sentence is a mechanism rather than an aspiration: the text is
    the materialized ``base..head`` range, the same object delivery publishes.
    Pure so it's unit-testable; ``None`` on an empty/blank diff (nothing to
    count). Feeds the settle-time DeliveryEvent → the per-goal run summary.

    Binary files are counted separately and reported under ``binary``. A binary
    file contributes no ``+``/``-`` lines to a unified diff, so a span carrying
    one is a BOUNDED view of its own size — and a bounded view must say so
    (FR-009) instead of silently under-reporting."""
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


def _attach_diff_stats(result: dict, diff: str) -> None:
    """Best-effort, never-raises: stamp the gate-time diff stats onto the task
    result so they flow to the settle-time DeliveryEvent. A stats hiccup must
    never fail a task that already passed every gate."""
    try:
        stats = _diff_stats(diff)
        if stats is not None:
            result["diff_stats"] = stats
    except Exception as err:  # noqa: BLE001 — observability, not correctness
        sys.stderr.write(f"task-queue: diff-stats capture failed: {err}\n")


#: Task kinds that exist to WRITE code. An empty span from one of these is a
#: real "the agent accomplished nothing" signal (FR-014). A read-only kind
#: legitimately changes nothing — its deliverable is a report — so it keeps
#: counting as a delivery (FR-011).
_CODE_WRITING_KINDS = frozenset({"implement_feature", "fix_bug", "onboard"})


def _attach_change(
    result: dict, change: ChangeSet, *, kind: str, workspace_dir: str,
    verify_cmd: Optional[str],
) -> None:
    """Stamp the ONE judged span onto the task result (spec 013): its two
    references, its size, its no-change flag, and the mechanical advisories that
    read it. Best-effort, never-raises — a bookkeeping hiccup must not fail a
    task that already passed every gate."""
    try:
        result["change"] = {
            "status": change.status,
            "base_sha": change.base_sha,
            "head_sha": change.head_sha,
            "agent_authored": change.agent_authored,
            "materialized": change.materialized,
        }
        if change.reason:
            result["change"]["reason"] = change.reason
        if change.is_no_change and kind in _CODE_WRITING_KINDS:
            # Explicit, distinguishable outcome: the task settles successfully,
            # publishes nothing, and is reported upstream as NO PROGRESS rather
            # than as a delivered increment (FR-006/FR-014). Plain success is
            # the false-green being closed — an upstream poller cannot otherwise
            # tell "nothing needed doing" from "the agent accomplished nothing".
            result["no_change"] = True
        if change.status == _CHANGE_NO_REPO:
            sys.stderr.write(
                f"task-queue: no git repository at {workspace_dir} — the agent's "
                f"change could not be materialized ({change.reason}); nothing can "
                f"be published from this workspace\n"
            )
    except Exception as err:  # noqa: BLE001 — observability, not correctness
        sys.stderr.write(f"task-queue: change capture failed: {err}\n")
    _attach_diff_stats(result, change.diff)
    try:
        notes = change_advisories(
            change.diff, workspace_dir=workspace_dir, verify_cmd=verify_cmd or "",
        )
        if notes:
            result.setdefault("hook_warnings", []).extend(
                f"[change-advisory] {n}" for n in notes
            )
    except Exception as err:  # noqa: BLE001 — advisory, never a task failure
        sys.stderr.write(f"task-queue: change advisories failed: {err}\n")


def _attach_gate_advisory(result: dict, gate_id: str, reason: str) -> None:
    """Stamp a `trust`-mode advisory (a dial-able gate that failed but shipped,
    ADR 0007) onto the task result so it flows to delivery → the PR body. Never
    raises — a shipped-with-caveat task must not fail over its own annotation."""
    try:
        advisories = result.setdefault("gate_advisories", [])
        advisories.append({"gate": gate_id, "reason": reason})
    except Exception as err:  # noqa: BLE001 — observability, not correctness
        sys.stderr.write(f"task-queue: gate-advisory capture failed: {err}\n")


async def _git_head(host_dir: str) -> str:
    """Async wrapper — same thread-offload rationale as :func:`_git_diff`."""
    return await asyncio.to_thread(_git_head_sync, host_dir)


async def _git_commit_exists(host_dir: str, sha: str) -> bool:
    """Async wrapper — same thread-offload rationale as :func:`_git_diff`."""
    return await asyncio.to_thread(_git_commit_exists_sync, host_dir, sha)


async def _base_branch_error(host_dir: str, base_branch: str) -> Optional[str]:
    """Async wrapper for the dispatch-surface base validation (PR-2 advisory):
    None when ``origin/<base_branch>`` resolves after a fetch, else the
    actionable message the task fails with."""
    return await asyncio.to_thread(_base_branch_error_sync, host_dir, base_branch)


async def _branch_staleness(host_dir: str, base_branch: str) -> Optional[dict]:
    """Async wrapper around :func:`task_git.branch_staleness_sync`.
    Returns ``{"commits_behind": int, "commits_ahead": int}`` or ``None`` on
    any probe failure — callers treat None as "proceed unchanged" (best-effort)."""
    return await asyncio.to_thread(_branch_staleness_sync, host_dir, base_branch)


async def _wip_snapshot(host_dir: str, task_id: str) -> str:
    """Async wrapper — same thread-offload rationale as :func:`_git_diff`."""
    return await asyncio.to_thread(_wip_snapshot_sync, host_dir, task_id)


async def _review_repo_context(host_dir: str) -> str:
    """Async wrapper — same thread-offload rationale as :func:`_git_diff`. Looks
    up :func:`_review_repo_context_sync` as a module global so tests can patch it
    here."""
    return await asyncio.to_thread(_review_repo_context_sync, host_dir)


def _lane_key(lane: dict) -> str:
    """The merge-queue ordering key for a lane — the shared branch/workspace it
    integrates onto, so lanes of different goals never wait on each other."""
    return str(lane.get("integrate_into") or lane.get("key") or "")


def _lane_position(lane: dict) -> int:
    """The lane's index in the plan. Integration order is the PLAN's, not
    arrival's, so a re-run of the same group produces the same history. A
    missing/garbled position sorts first rather than crashing the settle path."""
    try:
        return int(lane.get("position") or 0)
    except (TypeError, ValueError):
        return 0


class SettleMixin:
    if TYPE_CHECKING:
        # The composing class (TaskQueue) owns these; declared under
        # TYPE_CHECKING so the seam is checked, never run.
        _store: StateStore
        _merge_queue: MergeQueue
        _runner: Engine
        _reachability_judge: Callable[..., Awaitable[dict]]
        _sandbox_owner: str

        def _pump(self) -> None: ...
        def _fire_settle(self) -> None: ...
        def _sandbox_image(self, project_id: Optional[str]): ...
        def _browser_gate_mode(self, project_id: Optional[str]) -> str: ...
        def _check_and_trip_breaker(self, workspace_dir: str, task_id: str) -> None: ...
        async def _notify_task(self, task: Task) -> None: ...
        # The review gate's seam (quality/task_gates._ReviewGate calls it on
        # this mixin's composed instance); the method itself stays in
        # devclaw.task_queue next to its REVIEW_GATE_ENABLED default.
        async def _review_failure(
            self, kind: TaskKind, goal: str, diff: str, workspace_dir: str,
            *, scaffold: bool = False, project_id: Optional[str] = None,
        ) -> Optional[str]: ...

    async def _prep_branch_target(
        self,
        workspace_dir: str,
        *,
        base_branch: Optional[str],
        target_branch: Optional[str],
    ) -> Optional[str]:
        """Direct-dispatch branch-target prep (v1-helper-resurface P1, PR-2).
        Returns None on success, else the failure message the task settles
        with — the engine must not run when the pinned contract can't be set up.

        Two steps, both loud:
        - ``base_branch`` set → verify it resolves as ``origin/<base>`` after a
          fetch (advisory b: a bogus base fails HERE with an actionable
          message, instead of surfacing downstream as diff-range/PR-base skew).
        - ``target_branch`` set → ``prepare_workspace(branch=target_branch)``
          puts the workspace ON it (created off ``base_branch`` when it doesn't
          exist on origin yet — proposal O3), mirroring how the goal layer
          preps ``goal/<id>`` before each action.

        Assumes an already-known workspace (proposal O7): no repo_url is
        passed, so a non-repo workspace fails with prepare_workspace's own
        actionable message rather than cloning from scratch (that ergonomic is
        P3)."""
        # A target equal to the base (or to the remote default) would put
        # the workspace ON the base itself and delivery's branch-reuse mode
        # would then `git push` unreviewed commits STRAIGHT to it — failing
        # only afterwards on `gh pr create` (head == base), i.e. loud but
        # already irreversible. Reject the contract up front, BEFORE any
        # fetch: devclaw never pushes a base/default branch directly.
        if target_branch and base_branch and target_branch == base_branch:
            return (
                f"target_branch '{target_branch}' equals base_branch — "
                "delivery would push unreviewed commits straight to the "
                "base itself. Pin a work branch (e.g. feat/…) to continue, "
                "or omit target_branch for a fresh derived branch."
            )
        if base_branch:
            err = await _base_branch_error(workspace_dir, base_branch)
            if err:
                return err
        if target_branch:
            default = await _workspace_default_branch(workspace_dir)
            if target_branch == default:
                return (
                    f"target_branch '{target_branch}' is the repository's "
                    "default branch — devclaw never pushes the default branch "
                    "directly (every change ships as a PR). Pin a work branch "
                    "(e.g. feat/…), or omit target_branch for a fresh derived "
                    "branch."
                )
            try:
                await prepare_workspace(
                    workspace_dir, branch=target_branch, base_branch=base_branch
                )
            except WorkspaceError as err:
                return f"could not prepare target_branch '{target_branch}': {err}"
            except Exception as err:  # never wedge the queue on a prep surprise
                return (
                    f"could not prepare target_branch '{target_branch}': "
                    f"{err.__class__.__name__}: {err}"
                )
            if base_branch:
                staleness = await _branch_staleness(workspace_dir, base_branch)
                if (
                    staleness is not None
                    and staleness["commits_ahead"] == 0
                    and staleness["commits_behind"] >= BRANCH_STALE_THRESHOLD
                ):
                    return (
                        f"branch '{target_branch}' is hard-stale: 0 commits ahead of"
                        f" '{base_branch}' but {staleness['commits_behind']} commits"
                        f" behind (threshold {BRANCH_STALE_THRESHOLD}). Rebase the"
                        f" branch onto the current '{base_branch}' before dispatching."
                    )
        return None

    async def _run_validation_task(
        self,
        task_id: str,
        workspace_dir: str,
        goal: str,
        *,
        project_id: Optional[str],
    ) -> None:
        """Spec 015 US2 — the ``validate_product`` spine. One engine run, no
        retries (there is no agent whose behavior a retry could change), no
        gates, no delivery. The HOST files every failure as a spec-014 finding
        (the sandbox holds no GitHub credential), restores the workspace so
        boot/seed artifacts never become commits, and settles the task with
        the run record as its detail. Red suites settle ``done`` (findings ARE
        the output); only contract/boot/infra failures settle ``failed``."""
        slug = await asyncio.to_thread(_validation.repo_slug_for_workspace, workspace_dir)

        async def _file_and_record(report: "object") -> tuple[list, str]:
            findings = _validation.findings_from_report(report)
            outcomes: list = []
            if findings and slug:
                outcomes = await _validation.file_validation_findings(
                    self._store, slug, findings
                )
            elif findings and not slug:
                # dev/stub workspace with no remote — nowhere to file; loud.
                sys.stderr.write(
                    f"validation: {len(findings)} finding(s) for {workspace_dir} "
                    "but the workspace has no origin remote — not filed\n"
                )
            return outcomes, _validation.run_record_line(report, outcomes)

        # 1) resolve the declared contract from the merged base (trust boundary)
        try:
            contract = await asyncio.to_thread(
                _manifest.resolve_validation_contract, workspace_dir
            )
        except _manifest.ManifestError as exc:
            contract = None
            contract_error: Optional[str] = str(exc)
        else:
            contract_error = None
        if contract is None:
            missing: dict = {
                "contract_ran": False, "boot": None, "suites": None,
                "browser_report": None, "failing_tests": [], "partial": False,
                "note": "missing contract: "
                        + (contract_error or "devclaw.json declares no validation key"),
            }
            _, record = await _file_and_record(missing)
            self._store.mark_failed(
                task_id,
                "validation run has no usable contract — declare validation.boot "
                f"and validation.suites in devclaw.json ({contract_error or 'key absent'}). "
                f"{record}",
            )
            return

        # 2) one engine run, wall-clock bounded like every task
        request = EngineRequest(
            kind="validate_product",
            workspace_dir=workspace_dir,
            goal=goal,
            verify_cmd=None,
            sandbox_image=self._sandbox_image(project_id),
            owner_id=self._sandbox_owner,
            validation={"boot": contract.boot, "suites": contract.suites},
        )
        try:
            if TASK_TIMEOUT_S > 0:
                result = await asyncio.wait_for(self._runner(request), timeout=TASK_TIMEOUT_S)
            else:
                result = await self._runner(request)
        except asyncio.TimeoutError:
            self._store.mark_failed(
                task_id,
                f"validation run exceeded the {TASK_TIMEOUT_S:.0f}s wall clock — "
                "sandbox torn down; split the suites or raise DEVCLAW_TASK_TIMEOUT_S.",
            )
            return
        except Exception as err:  # noqa: BLE001 — infra crash is loud, no retry
            self._store.mark_failed(task_id, f"validation runner error: {err}")
            return

        # 3) restore the workspace — a validation run never mutates the repo
        #    (FR-005); boot/seed artifacts are discarded, loudly on failure.
        def _restore() -> Optional[str]:
            import subprocess as _sp
            for args in (("reset", "--hard"), ("clean", "-fd")):
                proc = _sp.run(["git", "-C", workspace_dir, *args],
                               capture_output=True, text=True, timeout=120)
                if proc.returncode != 0:
                    return f"git {' '.join(args)}: {proc.stderr.strip()}"
            return None
        restore_err = await asyncio.to_thread(_restore)
        if restore_err:
            sys.stderr.write(f"validation: workspace restore failed: {restore_err}\n")

        if result.get("status") != "ok":
            self._store.mark_failed(
                task_id, f"validation run failed: {result.get('error', 'unknown error')}"
            )
            return

        # 4) findings + run record; verdict per the report
        report = result.get("validation_report")
        outcomes, record = await _file_and_record(report)
        rpt: dict = report if isinstance(report, dict) else {}
        boot = rpt.get("boot")
        note = str(rpt.get("note") or "")
        infra_failed = (
            not isinstance(report, dict)
            or note.startswith("missing contract")
            or not isinstance(boot, dict)
            or not boot.get("passed")
        )
        if infra_failed:
            self._store.mark_failed(
                task_id,
                f"validation could not prove the running product — {record}. "
                "Fix the boot/contract; the finding is filed.",
            )
        else:
            self._store.mark_done(task_id, record)

    async def _execute(
        self,
        task_id: str,
        kind: TaskKind,
        workspace_dir: str,
        goal: str,
        program_id: Optional[str],
    ) -> None:
        # The task is already 'running' (claim_pending set it); just run + settle.
        # An open_pr task (standalone OR program-child) must NOT be observable as
        # 'done' until its change is delivered — otherwise a poller (goalclaw)
        # reads done-without-PR the instant the gate passes and re-dispatches
        # an already-shipped item. So for that path we defer the done-flip:
        # run delivery while the task is still 'running', then settle 'done'
        # WITH the pr_url in one write.
        #
        # Program tasks inherit their ``deliver`` flag from the parent program's
        # ``open_pr`` (set at submit_program time; see ``_persist_plan``) — the
        # standing-goal reviewable-slice contract, closing the 2026-07-03
        # closeloop-mission-v2 defect where the activity-timeline program
        # pushed straight to main.
        row = self._store.get_task(task_id)
        if kind == "validate_product":
            # Spec 015: its own spine — no branch prep, no retry loop, no gate
            # chain, no delivery. A validation run reads the product; it never
            # ships anything.
            await self._run_validation_task(
                task_id, workspace_dir, goal,
                project_id=(row.project_id if row else None),
            )
            return
        deliver = bool(row and row.deliver)
        # Branch-target wire (v1-helper-resurface P1, PR-2) — DIRECT path only:
        # goal-path and program-child rows never carry these, so for them every
        # line below is inert (no prep subprocess, unpinned deliver_change call).
        base_branch = (row.base_branch or None) if row else None
        target_branch = (row.target_branch or None) if row else None
        # Owning project's reference key (#524 P3) — the per-project knobs
        # (sandbox_image, browser_gate_mode, review_gate) resolve by this id, not
        # by a workspace-path scan. None for a task with no project.
        project_id = (row.project_id if row else None)
        # Fan-out lane (spec 010 US3): this task is one of a planned parallel
        # group, so it ran in its OWN checkout and its work has to be integrated
        # onto the shared goal branch — serially, in plan order — before it can
        # ship. None for every ordinary task, which leaves the path below
        # byte-identical.
        lane = row.lane() if row else None

        prep_failure: Optional[str] = None
        if (base_branch or target_branch) and not (row and row.pause_count > 0):
            # Validate the base + prep the pinned branch BEFORE the engine runs
            # (mirrors the goal layer prepping goal/<id> at dispatch). Skipped
            # on a pause-resume re-run: the workspace deliberately survives a
            # requeue untouched (see _run_and_settle's resume brief) — re-prep
            # would reset the branch to its origin tip and wipe the wip
            # snapshot; base/target were already validated on the first run.
            prep_failure = await self._prep_branch_target(
                workspace_dir, base_branch=base_branch, target_branch=target_branch
            )
        if prep_failure is not None:
            # Fail loudly and FAST — the engine never runs against a workspace
            # that isn't on the contract the caller pinned (a bogus base would
            # otherwise surface downstream as silent diff-range/PR-base skew).
            self._store.mark_failed(task_id, prep_failure)
            self._check_and_trip_breaker(workspace_dir, task_id)
            success: "dict | _PausedSentinel | None" = None
        else:
            success = await self._run_and_settle(
                task_id, kind, workspace_dir, goal, defer_done=deliver,
                project_id=project_id,
            )
        if success is _PAUSED:
            # Paused for a quota limit — task is back to 'pending', global pause
            # holds dispatch. Don't deliver/notify/settle; the gated _pump will
            # redispatch it (fresh attempts) once the pause expires.
            if lane:
                # Release this lane's merge-queue slot: it is not going to
                # integrate on this run, and the lanes behind it must not wait
                # for a task that is back in the pending pool. On the resumed run
                # it takes a fresh turn (the queue never rewinds, so a later
                # position is admitted immediately).
                await self._merge_queue.skip(_lane_key(lane), _lane_position(lane))
            self._pump()
            return
        if lane and not (deliver and success is not None):
            # This lane will never reach integration — the gate failed, the run
            # errored, or it was dispatched without a delivery contract. Release
            # its merge-queue slot rather than wedge the lanes behind it: a
            # failing increment must fail ALONE, which is exactly the spec's
            # Independent Test (one lane strays and fails while its sibling
            # lands).
            await self._merge_queue.skip(_lane_key(lane), _lane_position(lane))
        no_change = bool(isinstance(success, dict) and success.get("no_change"))
        if deliver and success is not None and no_change:
            # Spec 013 FR-014: the agent produced an empty span. That is a
            # first-class outcome, not a delivery — the task settles done,
            # publishes nothing, and the goal layer counts it as no progress
            # (feeding the no-progress watchdog) instead of as a shipped
            # increment. Failing it would punish a run that was correct to do
            # nothing; plain success is the false-green being closed.
            sys.stderr.write(
                f"task-queue: task {task_id} produced no change — settling done "
                f"with nothing to publish\n"
            )
            self._store.mark_done(task_id, json.dumps(success))
        elif deliver and success is not None:
            # Gate passed; the task is still 'running'. Turn the change into a
            # branch/PR, then make 'done' observable — with pr_url already on
            # the row. Pass the kind (→ conventional-commit title) + the gate
            # verdict (→ PR body) so the delivered PR describes itself.
            verify = success.get("verify") if isinstance(success, dict) else None
            # ADR 0007: any trust-mode gate advisory rides into the PR body so the
            # human sees it at the merge boundary (the backstop for advisory gates).
            advisories = success.get("gate_advisories") if isinstance(success, dict) else None
            pr_url = None
            failure: Optional[str] = None
            delivery: dict = {}
            # Serial integration (FR-102). A lane's work lives in its own
            # checkout; the merge queue admits lanes onto the shared goal branch
            # ONE at a time and strictly in plan order, so two increments that
            # ran concurrently still land in a reproducible sequence. Delivery
            # then runs from the SHARED workspace, so the goal branch and its
            # single cumulative PR behave exactly as they do for a sequential
            # increment. Integration failure fails this lane and only this lane.
            delivery_dir = workspace_dir
            if lane:
                integrate_into = str(lane.get("integrate_into") or "") or workspace_dir
                label = str(lane.get("label") or goal)[:120]
                integration_error: Optional[str] = None
                try:
                    async with self._merge_queue.turn(
                        _lane_key(lane), _lane_position(lane)
                    ):
                        integration_error = await commit_lane(
                            workspace_dir, label=label, task_id=task_id
                        )
                        if integration_error is None and integrate_into != workspace_dir:
                            integration_error = await integrate_lane(
                                lane_dir=workspace_dir, into_dir=integrate_into,
                                label=label, task_id=task_id,
                            )
                except Exception as err:  # noqa: BLE001 — never ship on a crash (#186)
                    integration_error = (
                        f"lane integration could not complete: "
                        f"{err.__class__.__name__}: {err}"
                    )
                if integration_error is not None:
                    sys.stderr.write(
                        f"task-queue: lane integration failed task={task_id}: "
                        f"{integration_error}\n"
                    )
                    self._store.mark_failed(task_id, integration_error)
                    self._check_and_trip_breaker(workspace_dir, task_id)
                    if program_id is None:
                        self._fire_settle()
                    self._pump()
                    return
                delivery_dir = integrate_into
            # Only-when-set on purpose (blank-safe): the unpinned call shape
            # stays byte-identical for goal/program tasks AND for every test stub
            # of deliver_change that does not accept the branch-target kwargs.
            branch_kwargs: dict = {}
            if base_branch:
                branch_kwargs["base_branch"] = base_branch
            if target_branch:
                branch_kwargs["target_branch"] = target_branch
            # Spec 013 FR-005: hand delivery the artifact the gates judged, so it
            # publishes that object instead of recomputing its own view of the
            # change. Only for a real materialized span — a workspace that is not
            # a repository has nothing to publish and takes the self-discovering path,
            # where delivery fails loudly on the same condition.
            change_record = success.get("change") if isinstance(success, dict) else None
            if isinstance(change_record, dict) and change_record.get("head_sha") and (
                change_record.get("status") == _CHANGE_SOME
            ):
                branch_kwargs["judged_head"] = change_record["head_sha"]
                branch_kwargs["agent_authored"] = bool(change_record.get("agent_authored"))
            try:
                delivery = await deliver_change(
                    workspace_dir=delivery_dir, task_id=task_id, goal=goal,
                    kind=kind, verify=verify,
                    title=(row.title if row else None),
                    advisories=advisories,
                    **branch_kwargs,
                )
                pr_url = delivery.get("pr_url")
                failure = delivery_failed(delivery)
                sys.stderr.write(f"task-queue: delivery task={task_id}: {delivery}\n")
            except Exception as err:  # deliver_change promises not to raise; belt+suspenders
                failure = f"{err.__class__.__name__}: {err}"
                sys.stderr.write(f"task-queue: delivery failed task={task_id}: {err}\n")
            if isinstance(success, dict):
                # The delivery verdict is grounded evidence — persist it with the
                # result so the goal poller reads the PR/branch/push state, not
                # just a bare pr_url column.
                success["delivery"] = delivery
            # Pinned-target miss is LOUD (PR-2 advisory a): the caller asked to
            # continue target_branch; a delivery that landed anywhere else broke
            # that contract even if it opened a perfectly green PR — settling
            # 'done' here would silently degrade "continue this branch" into a
            # fresh-branch PR. A benign no-op (nothing shipped, branch None)
            # is not a miss — nothing landed anywhere.
            landed = delivery.get("branch")
            target_miss = bool(
                failure is None and target_branch and landed and landed != target_branch
            )
            if target_miss:
                failure = (
                    f"pinned target_branch '{target_branch}' was missed: delivery "
                    f"landed on '{landed}'"
                    + (f" (PR: {pr_url})" if pr_url else "")
                    + " — the continue-this-branch contract must not silently "
                    "degrade into a fresh-branch PR"
                )
            if failure is not None and (target_miss or not pr_url):
                # A requested delivery that BROKE must not settle 'done': a
                # done-without-PR row reads as shipped to every poller upstream
                # (the goal layer plans its next action off it — the exact
                # false-green the defer_done mechanism exists to prevent).
                # Benign no-PR outcomes (nothing to ship, local-only repo) are
                # not failures — delivery_failed() filters those out above.
                self._store.mark_failed(
                    task_id, f"gate passed but delivery failed: {failure}"
                )
                self._check_and_trip_breaker(workspace_dir, task_id)
            else:
                # 'done' becomes observable only now, atomically with pr_url.
                self._store.mark_done(task_id, json.dumps(success), pr_url=pr_url)
        if program_id is None:
            final = self._store.get_task(task_id)
            if final and final.notify_url:
                await self._notify_task(final)
            self._fire_settle()  # a standalone task settled → wake the goal layer
        # A global slot freed; this program may now be complete or have newly-ready
        # tasks; another program may be able to start. Re-pump.
        self._pump()

    # ---- shared runner --------------------------------------------------

    def _record_gate_advisory(
        self, goal_id: Optional[str], task_id: str, gate_id: str, reason: str
    ) -> None:
        """Record a `trust`-mode dial-able-gate advisory loud (ADR 0007): the
        change shipped past a browser/review finding rather than wedging. Goes
        to the problems catalog as ``recovered`` (devclaw carried on past it) +
        stderr, so lost quality stays visible without failing the night.
        Best-effort — a recording hiccup must never fail an already-shipped task."""
        try:
            sys.stderr.write(
                f"task-queue: gate advisory ({gate_id}) shipped under trust — "
                f"task={task_id}: {reason[:200]}\n"
            )
            self._store.record_problem(
                category="gate",
                kind=f"{gate_id} advisory (trust)",
                message=reason,
                recovered=True,
                goal_id=goal_id or "",
                task_id=task_id,
            )
        except Exception as err:  # noqa: BLE001 — observability, not correctness
            sys.stderr.write(f"task-queue: gate-advisory record failed: {err}\n")

    def _append_task_event(
        self, task_id: str, program_id: Optional[str], event: EngineEvent
    ) -> None:
        """Persist one engine event onto the append-only StateStore log, tagged
        with its ``task_id`` + ``program_id``. Passed to the engine per attempt
        as ``functools.partial(self._append_task_event, task_id, program_id)``
        (was an inline closure in ``_run_and_settle`` capturing exactly those two
        vars + ``self``; lifting it keeps that byte-identical). Event writes must
        NEVER crash the run — a persistence hiccup logs to stderr and is swallowed."""
        try:
            self._store.append_event(
                task_id=task_id,
                program_id=program_id,
                type=event.type,
                source=event.source,
                payload_json=json.dumps(event.payload),
                ts=int(event.ts) if isinstance(event.ts, (int, float)) else _now_ms(),
            )
        except Exception as err:  # event writes must never crash the run
            sys.stderr.write(f"task-queue: append_event failed task={task_id}: {err}\n")

    async def _run_and_settle(
        self, task_id: str, kind: TaskKind, workspace_dir: str, goal: str,
        *, defer_done: bool = False, project_id: Optional[str] = None,
    ) -> "dict | _PausedSentinel | None":
        """Run the agent (with retries) and settle the task. Returns None once the
        task is settled (done/failed/timeout). When ``defer_done`` is set and the
        gate passes, it does NOT mark the task done — it returns the winning result
        dict and leaves the task 'running', so the caller can deliver then settle
        'done' atomically (see _execute). Failures/timeouts always settle here."""
        # ── The settle cascade juggles THREE ORTHOGONAL AXES. Do not conflate
        #    them, and do not reorder the routing below — the ordering is
        #    load-bearing (2026-07-20 night-incident regression surface, #407).
        #
        #    Axis 1 — GATE VERDICT: verify → test_integrity → scope → review → browser,
        #      a STRICT SHORT-CIRCUIT chain over ONE computed diff. review runs
        #      only if integrity passed; browser only if both passed. Flattening
        #      it recomputes the diff and surfaces lower-priority findings.
        #    Axis 2 — FAILURE-STRING CLASSIFICATION: classify_failure() reads the
        #      terminal failure text to pause on quota/auth (usage-limit path).
        #    Axis 3 — MARKER-BASED FAST-FAIL ROUTING: _WORKER_BLOCKED_MARKER,
        #      _REVIEW_CRASH_MARKER and _PROMPT_TOO_LONG_MARKER route specific
        #      failures without a retry.
        #
        #    LOAD-BEARING ORDERING (below): _WORKER_BLOCKED_MARKER is checked
        #    BEFORE classify_failure, which is checked BEFORE _REVIEW_CRASH_MARKER
        #    and _PROMPT_TOO_LONG_MARKER.
        #    The SAME review-crash string is a PAUSE or a FAST-FAIL depending on
        #    which classifier claims it first — reordering silently changes the
        #    outcome. Leave the order as written.
        #
        # Resolve program_id + the verify gate once so on_event doesn't re-query.
        row = self._store.get_task(task_id)
        program_id = row.program_id if row else None
        verify_cmd = row.verify_cmd if row else None
        # L3 (#222): a scaffold task skips ONLY the adversarial review gate below.
        # The verify gate (checked first) and test-integrity scan are NOT gated on
        # this flag — they run for scaffold and non-scaffold tasks alike, so an
        # over-tagged real code task still fails if it doesn't build or guts tests.
        scaffold = bool(row.scaffold) if row else False
        # ADR 0007: the goal's gate strictness dial, snapshotted on the row at
        # dispatch. Read here so the settle cascade below can decide whether a
        # dial-able gate failure (browser / adversarial review) BLOCKS (strict)
        # or advises-and-ships (trust). Always-hard gates ignore it.
        strictness = row.strictness if row else "trust"
        parent_goal_id = row.parent_goal_id if row else None
        # The declared file scope this increment was dispatched under (spec 010
        # FR-101) — set only for a fan-out lane. Read here so the scope gate can
        # hold the lane to its pinned contract.
        _lane = row.lane() if row else None
        lane_scope: "tuple[str, ...]" = tuple(
            str(g) for g in (_lane or {}).get("scopes", ()) if str(g).strip()
        )

        # Resumed-after-interruption brief. ``pause_count > 0`` means a previous
        # attempt of THIS task was cut off by a usage limit and requeued — the
        # T0.6 counter is the durable interruption signal, so no schema change
        # is needed here. The workspace survives the requeue untouched (nothing
        # re-preps between requeue and re-run), so its partial progress is still
        # there — possibly as a wip snapshot commit — and a re-run handed the
        # pristine goal would restart from scratch or duplicate/conflict with
        # the half-done edits. The brief is prepended to EVERY attempt this run
        # makes (a resumed task can also retry: brief prefix + goal + retry
        # suffix compose), and stays distinct from the retry-feedback suffix.
        pause_count = row.pause_count if row else 0
        resume_brief = "" if pause_count <= 0 else (
            f"[Resuming after a usage-limit interruption (pause {pause_count})] "
            "A previous attempt was cut off mid-work. The workspace still "
            "contains its partial progress — possibly as a "
            "'wip(devclaw): interrupted…' commit. Inspect "
            "`git status` and `git log` first and CONTINUE from whatever state "
            "is actually there; do not redo work that is already present.\n\n"
        )

        # Per-attempt event sink: the lifted _append_task_event bound method,
        # pre-bound to this task's (task_id, program_id) so the engine calls it
        # with just the event. Same three captured vars as the old closure.
        on_event = functools.partial(self._append_task_event, task_id, program_id)

        # The pinned base of the judged span (spec 013). Materialization writes
        # the post-run counterpart when the run ends; the change is the range
        # between the two. Captured ONCE before the attempt loop, not per
        # attempt: delivery ships everything ahead of this ref, so every retry's
        # gates must judge the same cumulative span it will ship (FR-012/FR-013 —
        # promoting a rejected attempt to the new base would let gate-REJECTED
        # content reach a PR without ever being re-judged).
        #
        # And once per TASK, not per run: a usage-limit pause commits the dirty
        # tree as a wip snapshot and requeues, so by the resumed run HEAD *is*
        # the half-done work. Re-capturing here made the wip commit the
        # baseline — the gates then judged only the post-resume leftovers and
        # rejected fully-present work as "no deliverable in the diff"
        # (closeloop-bench b6d53bbd, 2026-07-19). The first run persists the
        # captured base on the task row; every later re-run of the same task
        # (pause-resume, crash-recovery requeue) re-uses it, degrading to a
        # fresh capture when the persisted sha no longer resolves (e.g. the
        # workspace was re-cloned meanwhile).
        # The message devclaw writes IF it has to author the commit (the worker
        # recorded nothing). Resolved through delivery's own title resolver, so a
        # worker that never commits gets the identical title, branch slug and
        # message it gets today — materialization changes WHEN the commit is
        # written, not what it says (FR-008).
        materialize_msg = materialization_message(
            devclaw_commit_title(
                planner_title=(row.title if row else None),
                goal=goal, kind=kind, task_id=task_id,
            ),
            task_id,
        )

        pre_run_sha = ""
        stored_base = row.pre_run_sha if row else None
        if stored_base and await _git_commit_exists(workspace_dir, stored_base):
            pre_run_sha = stored_base
        if not pre_run_sha:
            pre_run_sha = await _git_head(workspace_dir)
            if pre_run_sha and row:
                self._store.set_task_pre_run_sha(task_id, pre_run_sha)

        # Retry-on-fail completes the reliability triad (verify + RETRY + human): a
        # gate-fail or a transient agent error is re-run, each time with the failure
        # fed back into the goal so the agent can self-correct, up to a bounded cap;
        # then it's escalated (the notify fires on the terminal state). Timeouts are
        # NOT retried — a stuck run would likely just hang again — they escalate now.
        attempts = 1 + max(0, TASK_MAX_RETRIES)
        last_failure = "unknown error"
        # Every prior failed attempt this run, in order. The retry prompt used
        # to carry only the single most-recent failure (an overwritten string),
        # so attempt 3 never learned what attempt 1 tried — and could burn its
        # budget repeating a mistake already fed back once (the gnhf-style
        # notes.md continuity gap, intra-dispatch half). An on-disk notes file
        # can't carry this instead: the retry-isolation reset above wipes
        # uncommitted files by design.
        attempt_failures: list[str] = []
        # ADR 0007 — set on any attempt whose failure was a DIAL-ABLE gate
        # finding (browser / adversarial review), with that attempt's result +
        # diff captured alongside. Reset each attempt so it reflects only the
        # FINAL attempt at exhaustion, where under `trust` it advises-and-ships.
        dialable_finding: Optional[tuple[str, str]] = None
        last_gate_result: Optional[dict] = None
        last_gate_change: Optional[ChangeSet] = None
        # A retry KEEPS the workspace (spec 013 FR-012, ruled 2026-08-22). The
        # loop used to rewind to ``pre_run_sha`` and ``clean -fdx`` between
        # attempts, so the gates would diff a clean base — a compensation for
        # the gates guessing what state the agent had left the tree in. They no
        # longer guess: every attempt is materialized and judged IN FULL against
        # the pinned ``pre_run_sha`` (FR-013), so no content can reach
        # publication having been judged only as a delta against a rejected
        # attempt. What the rewind actually cost was the work the agent got
        # mostly right — it turned "fix your own output" into "rewrite from
        # scratch" on every retry.
        for attempt in range(attempts):
            dialable_finding = None
            if attempt == 0:
                attempt_goal = f"{resume_brief}{goal}"
            else:
                # Numbered history of EVERY failed attempt this run (not just
                # the latest) so the agent can rule out whole approaches, not
                # re-discover them one retry at a time.
                history = "\n".join(
                    f"  Attempt {i}: {f}"
                    for i, f in enumerate(attempt_failures, 1)
                )
                attempt_goal = (
                    f"{resume_brief}{goal}\n\n[Automatic retry {attempt}/{attempts - 1}] Your previous "
                    f"attempt did not pass verification. What went wrong in each "
                    f"prior attempt this run:\n{history}\n\n"
                    f"First re-run the failing command to confirm the failure still "
                    f"reproduces. If it does not reproduce, the test is flaky — fix "
                    f"the flakiness itself (or re-run verify) instead of hunting a "
                    f"phantom bug in your change. If it does reproduce, diagnose the "
                    f"cause and fix it; do not repeat any of these mistakes."
                )
            request = EngineRequest(
                kind=kind,
                workspace_dir=workspace_dir,
                goal=attempt_goal,
                on_event=on_event,
                verify_cmd=verify_cmd,
                sandbox_image=self._sandbox_image(project_id),
                owner_id=self._sandbox_owner,
            )
            try:
                # Wall-clock guard: on timeout, wait_for cancels the runner coroutine,
                # which propagates into run_sandcastle's finally → docker rm -f, so the
                # sandbox is torn down. Same cancellation path explicit cancel_task uses
                # (CancelledError is not an Exception, so a real cancel still propagates).
                if TASK_TIMEOUT_S > 0:
                    result = await asyncio.wait_for(self._runner(request), timeout=TASK_TIMEOUT_S)
                else:
                    result = await self._runner(request)
            except asyncio.TimeoutError:
                self._store.mark_failed(
                    task_id,
                    f"task exceeded the {TASK_TIMEOUT_S:.0f}s wall-clock timeout with no "
                    f"terminal result — sandbox torn down. Raise DEVCLAW_TASK_TIMEOUT_S "
                    f"if this was a legitimately long task.",
                )
                self._check_and_trip_breaker(workspace_dir, task_id)
                return None
            except Exception as err:
                last_failure = str(err)  # unexpected runner error — retryable
            else:
                if result.get("status") != "ok":
                    last_failure = result.get("error", "unknown error")
                    if result.get("status") == "rate_limited" and result.get("retry_after"):
                        # the engineer parsed an explicit reset hint — prefer it
                        last_failure = f"rate limit; retry-after: {result['retry_after']}s"
                    elif result.get("status") == "blocked":
                        # Honest worker self-report: the engineer said it genuinely
                        # cannot complete this task as specified. Carry the reason
                        # under the marker the no-retry branch below keys on — fail
                        # CLOSED (never `done`) and fail FAST (a re-run re-blocks
                        # identically), surfacing the reason instead of looping.
                        reason = (result.get("reason") or "").strip() or "no reason given"
                        last_failure = f"{_WORKER_BLOCKED_MARKER} {reason}"
                else:
                    # "done" means the verify gate passed, not that the agent said
                    # so — then the checks that READ the change. Axis 1 (the gate
                    # verdict) runs as an ORDERED, short-circuit PIPELINE over ONE
                    # shared, MATERIALIZED span (spec 013):
                    # verify → materialize → test_integrity → scope → review → browser.
                    # run_pipeline stops at the FIRST non-ok verdict, and that
                    # short-circuit IS the strict ordering the cascade always had —
                    # review runs only if integrity passed, browser only if both
                    # passed. The span is materialized lazily inside GateInput and
                    # memoised, so it is captured AT MOST ONCE and only when a
                    # span-reading gate runs — the verify gate never asks for it, so
                    # a verify failure still short-circuits before any git runs.
                    #
                    # NB: git runs in THIS (host/server) process, so the diff needs
                    # the workspace path as we see it — NOT the docker-bind host path
                    # (`_translate_workspace_path` maps container→host for the
                    # sandbox `-v` mount; using it here pointed git at `/srv/...`,
                    # which doesn't exist in our mount namespace, so the diff came
                    # back empty and BOTH read-the-change guards silently no-op'd in
                    # the deployed container). Use workspace_dir directly.
                    gate_input = GateInput(
                        kind=kind,
                        goal=goal,
                        workspace_dir=workspace_dir,
                        verify=result.get("verify"),
                        scaffold=scaffold,
                        browser_mode=self._browser_gate_mode(project_id),
                        # Spec 016 US2: the declared surface kind, read from
                        # devclaw.json at the MERGED base — host-side, never
                        # the worker-writable worktree/goal branch (FR-009).
                        # A malformed base manifest raises here and settles
                        # the task failed — a gate input that cannot be
                        # determined fails CLOSED (#186), never defaults.
                        surface=await asyncio.to_thread(
                            _manifest.resolve_surface, workspace_dir
                        ),
                        change_fn=lambda: _capture_change(
                            workspace_dir, pre_run_sha,
                            task_id=task_id, message=materialize_msg,
                        ),
                        project_id=project_id,
                        # Spec 010 FR-103: a fan-out lane is bound by the file
                        # scope the HOST pinned at dispatch, not by whether the
                        # worker got round to checking its task row off. Empty
                        # for every ordinary increment, whose contract (if any)
                        # the scope gate reads from its own claim on the plan.
                        declared_scope=lane_scope,
                    )
                    # ADR 0007 / review-gate-repositioning (spec 001): the
                    # per-increment adversarial diff review is a STRICT-ONLY gate.
                    # Under `trust` (the default, companion mode) it is dropped from
                    # the chain entirely — not run, so zero `claude` calls and no
                    # crash surface — because the human reviews every PR and the
                    # goal-level done-gate re-catches its unique findings one cycle
                    # later. Composition stays in the orchestrator (strictness is in
                    # scope here); the gate itself never learns the dial, honoring
                    # gate_pipeline's "policy never lives in a gate". verify /
                    # test_integrity stay always-hard; browser stays dial-able.
                    gates: list = [
                        _VerifyGate(), _MaterializeGate(), _IntegrityGate(),
                        _ScopeGate(),
                    ]
                    if strictness != "trust":
                        gates.append(_ReviewGate(self))
                    gates.append(_BrowserGate(self))
                    verdict = await run_pipeline(gate_input, tuple(gates))
                    if verdict is not None:
                        # The first failing gate — feed its reason back through the
                        # SAME retry loop as a gate fail. For a DIAL-ABLE gate (review
                        # / browser) also remember the finding + this attempt's
                        # result/diff so, if it survives every retry, the exhaustion
                        # path can advise-and-ship under `trust` (crash/quota variants
                        # return earlier via the marker/classify routing below and
                        # never reach exhaustion). The always-hard gates (verify /
                        # test_integrity) never set dialable, so the dial can never
                        # loosen them.
                        last_failure = verdict.reason or "gate failed (no reason recorded)"
                        if verdict.dialable:
                            dialable_finding = (verdict.gate_id, last_failure)
                            last_gate_result = result
                            last_gate_change = await gate_input.change()
                    elif defer_done:
                        # every gate passed — caller delivers, then settles 'done'
                        # WITH pr_url atomically (see _execute).
                        _attach_change(
                            result, await gate_input.change(), kind=kind,
                            workspace_dir=workspace_dir, verify_cmd=verify_cmd,
                        )
                        return result
                    else:
                        _attach_change(
                            result, await gate_input.change(), kind=kind,
                            workspace_dir=workspace_dir, verify_cmd=verify_cmd,
                        )
                        self._store.mark_done(task_id, json.dumps(result))
                        return None
            # Worker honest-block: the engineer self-reported it cannot finish
            # (missing capability, contradictory/impossible instructions). Fail
            # FAST + CLOSED (never ship, never settle `done` — invariant #186) and
            # do NOT retry: a re-run reproduces the same block identically, so a
            # retry only burns the budget and then the goal-level re-dispatch loop.
            # The reason rides the failure so the goal layer surfaces it to the
            # owner (poll.detail → the planner's next-tick context). Checked BEFORE
            # classify_failure so an unlucky reason wording can't be misrouted into
            # the pause path — a block is never a quota event.
            if last_failure.startswith(_WORKER_BLOCKED_MARKER):
                self._store.mark_failed(
                    task_id,
                    f"{last_failure} — the worker reports it cannot complete this "
                    "task as specified. Not auto-retried: a re-run reproduces the "
                    "same block. Needs a human — adjust the goal/instructions or "
                    "supply the missing capability.",
                )
                self._check_and_trip_breaker(workspace_dir, task_id)
                return None
            # Quota guard: a usage/rate limit must NOT be retried-now (that burns
            # the remaining quota on the same doomed call). Pause ALL dispatch and
            # requeue this task; the tick loop auto-resumes when the pause expires.
            # now_utc lets the classifier turn Claude's ABSOLUTE reset wording
            # ("resets 10pm (UTC)") into a real hint; a stated hint is trusted
            # past the default re-probe cap (pause_seconds' stated policy).
            # AUTH rides the same path (2026-07-20 night incident) — an expired
            # login dooms every call exactly like a cap, so requeue + pause; the
            # kind routes it onto the fixed AUTH_PAUSE_S re-probe cadence and
            # the goal layer words the owner ping as "re-login needed".
            cls = classify_failure(last_failure, now_utc=datetime.now(timezone.utc))
            if cls.is_pausing:
                backoff = pause_seconds(cls.retry_after_s, stated=cls.stated, kind=cls.kind)
                self._store.set_global_pause(
                    _now_ms() + backoff * 1000, f"{cls.kind.value}: {last_failure[:160]}"
                )
                task = self._store.get_task(task_id)
                if task is not None and task.pause_count >= MAX_PAUSE_REQUEUES:
                    # This one task has ridden the pause loop to its bound — fail
                    # it with the real reason so the breaker (and a human) can
                    # see it, instead of requeueing forever. The global pause
                    # above still holds: the account IS limited.
                    self._store.mark_failed(
                        task_id,
                        f"exceeded {MAX_PAUSE_REQUEUES} usage-limit pauses; "
                        f"last: {last_failure}",
                    )
                    self._check_and_trip_breaker(workspace_dir, task_id)
                    sys.stderr.write(
                        f"task-queue: task {task_id} hit {cls.kind.value} after "
                        f"{task.pause_count} pause-requeues — failing (bound "
                        f"{MAX_PAUSE_REQUEUES} reached), dispatch still paused "
                        f"~{backoff}s\n"
                    )
                    return None
                # Preserve the interrupted attempt's partial work BEFORE the
                # requeue: commit the dirty tree as a wip snapshot so it can't
                # be wiped by a later workspace reset/clean. Best-effort — any
                # snapshot failure logs and the pause path proceeds regardless.
                try:
                    snapshot = await _wip_snapshot(workspace_dir, task_id)
                except Exception as err:  # noqa: BLE001 — never block the pause
                    snapshot = f"crashed: {err.__class__.__name__}: {err}"
                if snapshot == "committed":
                    sys.stderr.write(
                        f"task-queue: task {task_id} wip snapshot committed "
                        f"before pause requeue\n"
                    )
                else:
                    sys.stderr.write(
                        f"task-queue: task {task_id} wip snapshot skipped "
                        f"({snapshot})\n"
                    )
                self._store.requeue_task(task_id)
                sys.stderr.write(
                    f"task-queue: task {task_id} hit {cls.kind.value} — pausing dispatch "
                    f"~{backoff}s, requeued (not failed)\n"
                )
                return _PAUSED
            # A review-gate CRASH (the reviewer couldn't produce a verdict — an
            # oversized/unparseable diff makes the review model return non-JSON) is
            # NOT a defect the agent can fix by retrying: re-running produces the
            # same diff and re-crashes the gate identically, burning the retry budget
            # and then the goal-level re-dispatch loop. Fail FAST + fail CLOSED (never
            # ship unreviewed) with an actionable reason, instead of looping. Quota-
            # shaped reviewer crashes are handled above (they PAUSE, not fail).
            if last_failure.startswith(_REVIEW_CRASH_MARKER):
                self._store.mark_failed(
                    task_id,
                    f"{last_failure} Not auto-retried: a diff too large or "
                    "unreviewable for the gate must be split into smaller commits or "
                    "reviewed by a human — retrying re-crashes the gate identically.",
                )
                self._check_and_trip_breaker(workspace_dir, task_id)
                return None
            # Context overflow: the worker's conversation exceeded the model's
            # context window ("Conversation run failed for id=...: Internal
            # error: Prompt is too long"). NOT a defect a retry can fix — the
            # overflow is deterministic for this task's scope, and the retry
            # prompt APPENDS the failure history to the instruction, so a
            # re-run is strictly LARGER and overflows again: "(failed after 2
            # attempts)" was pure quota burn. Fail FAST + fail CLOSED (never
            # ships, never pauses — classify_failure above already returned
            # REAL for this wording) with an actionable reason, same treatment
            # as the review-crash fast-fail above. Substring match: the marker
            # rides mid-string inside the engine's error, not as our prefix.
            if _PROMPT_TOO_LONG_MARKER in last_failure:
                self._store.mark_failed(
                    task_id,
                    f"{last_failure} — the worker conversation overflowed the "
                    "model context. Not auto-retried: the overflow is "
                    "deterministic and a retry replays the same task plus its "
                    "failure history, so it overflows again. Re-dispatch this "
                    "task with a smaller scope — slice the work into smaller "
                    "pieces touching fewer files.",
                )
                self._check_and_trip_breaker(workspace_dir, task_id)
                return None
            if attempt < attempts - 1:
                attempt_failures.append(last_failure)
                sys.stderr.write(
                    f"task-queue: task {task_id} attempt {attempt + 1}/{attempts} failed; "
                    f"retrying with all {len(attempt_failures)} prior failure(s) fed back\n"
                )
        # ADR 0007 — advise-and-ship under `trust`. The final attempt failed on a
        # DIAL-ABLE gate finding (browser / adversarial review) that survived
        # every retry. Crash / quota / worker-block variants already returned
        # above, so a finding reaching HERE is a genuine reviewable one — under
        # `trust` we deliver it with the finding surfaced in the PR body (the
        # human merge is the backstop) instead of wedging. `strict` (and the
        # always-hard gates, which never set dialable_finding) fall through to
        # mark_failed unchanged. Uses the captured last-attempt result+diff.
        if (
            dialable_finding is not None
            and last_gate_result is not None
            and gate_consequence(dialable_finding[0], strictness) is Consequence.ADVISE
        ):
            gate_id, reason = dialable_finding
            self._record_gate_advisory(parent_goal_id, task_id, gate_id, reason)
            _attach_gate_advisory(last_gate_result, gate_id, reason)
            if last_gate_change is not None:
                _attach_change(
                    last_gate_result, last_gate_change, kind=kind,
                    workspace_dir=workspace_dir, verify_cmd=verify_cmd,
                )
            if defer_done:
                # caller (_execute) delivers, then settles 'done' with pr_url.
                return last_gate_result
            self._store.mark_done(task_id, json.dumps(last_gate_result))
            return None
        # every attempt failed — escalate.
        suffix = f" (failed after {attempts} attempts)" if attempts > 1 else ""
        self._store.mark_failed(task_id, f"{last_failure}{suffix}")
        self._check_and_trip_breaker(workspace_dir, task_id)
        return None

    async def _browser_reachability_clears(
        self, verify: Optional[dict], diff: str, workspace_dir: str
    ) -> bool:
        """The reasoned escape valve for the browser gate. Returns True ONLY when
        an independent, grounded judge affirmatively determines the UI this diff
        changes is NOT rendered in the running app (nothing for a browser to
        exercise → the full-app E2E requirement is a false positive). Every other
        outcome returns False so the gate's block stands — the fail-closed spine:

        - disabled (`BROWSER_REACHABILITY_ENABLED` off) → False.
        - the block is a REAL browser failure (`ran_failed`) — a suite ran and a
          test failed → hard evidence, never overridable → False. Only a NO-RUN
          block (`never_ran` / `absent`) is a candidate for the false positive.
        - the judge says `reachable` is `yes` or `unknown` → False.
        - the judge raises (parse error, quota, timeout, crash) → False.

        So it can only ever RELAX a would-be block, never create or harden one,
        and only on proof. Consulted by settle ONLY when the mechanical gate is
        already about to block a frontend change — so no cognition fires on idle,
        backend, or passing paths (the zero-token guard)."""
        if not BROWSER_REACHABILITY_ENABLED:
            return False
        # Recompute the mechanical verdict so we override ONLY a no-run block — a
        # `ran_failed` (a browser test actually failed) is evidence, not a false
        # positive, and must never be reasoned away. This also means the judge is
        # not even called for `ran_failed` (zero token on that path too).
        config_present = _has_playwright_config(workspace_dir)
        verdict = browser_run_verdict(verify, diff, config_present=config_present)
        if verdict.state not in ("never_ran", "absent"):
            return False
        # Ground the judge in the ACTUAL task workspace (routes, imports, files) —
        # best-effort, collected OUTSIDE the try so a git hiccup degrades to no
        # context (→ the judge answers `unknown` → block stands), never a crash.
        repo_context = await _review_repo_context(workspace_dir)
        try:
            result = await self._reachability_judge(
                diff=diff, repo_context=repo_context
            )
        except Exception as err:  # noqa: BLE001 — fail closed, never wave a UI change through
            sys.stderr.write(
                f"task-queue: browser-reachability judge crashed, block stands "
                f"({err.__class__.__name__}: {err})\n"
            )
            return False
        if result.get("reachable") == "no":
            sys.stderr.write(
                "task-queue: browser-gate reachability override — the changed UI "
                "is not rendered in the running app, so the full-app browser run "
                f"is N/A: {result.get('rationale', '')[:200]}\n"
            )
            return True
        return False
