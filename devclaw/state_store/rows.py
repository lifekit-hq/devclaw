"""Pure data + row mappers for the state store.

No shared state, no connection — just the ``Task``/``Program``/``TaskEvent``
dataclasses, their wire-shape ``to_dict`` (camelCase, to match the original
TypeScript output), the ``sqlite3.Row`` → dataclass mappers, the status/kind
literals, and the shared busy-timeout constant.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Literal, Optional

# cancelled — deliberately aborted by a client (distinct from 'failed', which is
#   an execution error). Terminal, so crash recovery (which only revives
#   'running' rows) never resurrects it — an abort stays aborted across restarts.
TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]
TaskKind = Literal[
    "implement_feature", "fix_bug", "review_repository", "onboard", "validate_product"
]
# Programs hold a DAG of tasks decomposed from a single high-level goal.
def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Task:
    id: str
    kind: TaskKind
    status: TaskStatus
    workspace_dir: str
    goal: str
    notify_url: Optional[str]
    result_json: Optional[str]
    error: Optional[str]
    created_at: int
    started_at: Optional[int]
    completed_at: Optional[int]
    program_id: Optional[str]
    depends_on: list[str]
    order_idx: Optional[int]
    #: the spec milestone this task serves (set by plan-from-spec; else None)
    milestone: Optional[str]
    #: optional verify-gate command run after the agent finishes; its exit code
    #: decides done-vs-failed (the agent's self-report is not trusted). None → no gate.
    verify_cmd: Optional[str]
    #: deliver the change as a branch/PR after a successful run (open_pr tasks)
    deliver: bool
    #: the delivered PR URL (or None if not delivered / only a local branch)
    pr_url: Optional[str]
    #: Caller-chosen PR title. Optional; when None, delivery falls back to
    #: the engineer's own commit subject or the goal-derived heuristic.
    title: Optional[str] = None
    #: The durable goal that owns this task. Set when the goal heartbeat
    #: dispatches a task; None for standalone user-initiated dispatches
    #: (``dispatch_task``). Orthogonal to ``program_id`` (ephemeral DAG-run
    #: pointer) — a task can carry both, one, or neither.
    parent_goal_id: Optional[str] = None
    #: How many times this task was requeued by a usage-limit pause. Bounds the
    #: pause→requeue→re-run loop: a permanently-failing task whose error text
    #: happens to match the quota/rate regexes would otherwise loop forever
    #: (the workspace breaker only counts *failed* rows, and a paused task
    #: never becomes one).
    pause_count: int = 0
    #: True when this task is *generated scaffolding* (L3, #222) — set from the
    #: decomposer-tagged ChecklistItem.scaffold via the goal dispatch path. It
    #: makes the queue skip ONLY the adversarial review gate (a huge generated
    #: diff crashes it and shouldn't be diff-reviewed anyway). The verify/build
    #: gate + test-integrity scan STILL run — a scaffold task that doesn't build
    #: or that guts tests still fails. Defaulted so existing rows/tests are
    #: unaffected.
    scaffold: bool = False
    #: The PlannedTask key this program-child row was persisted from (ADR 0003
    #: stage 2). For a one-shot goal's program the key IS the checklist item
    #: id — the settle path's child→item join. None for standalone tasks and
    #: rows that predate the column.
    plan_key: Optional[str] = None
    #: The gate-baseline sha captured at this task's FIRST run (the pre-run
    #: HEAD the post-run gates diff against). Persisted so a pause→requeue
    #: re-run re-uses the ORIGINAL base: the pause path lands a wip snapshot
    #: commit on the branch, so re-capturing HEAD on resume made the half-done
    #: work itself the baseline and the gates judged only the post-resume
    #: leftovers (closeloop-bench b6d53bbd, 2026-07-19). None for rows that
    #: predate the column or tasks that haven't run.
    pre_run_sha: Optional[str] = None
    #: the goal's gate strictness dial SNAPSHOTTED at dispatch (ADR 0007), set
    #: from Goal.strictness via the goal dispatch path. The settle cascade reads
    #: it to decide a dial-able gate failure's consequence: "strict" blocks,
    #: "trust" advises-and-ships. Snapshotting on the row means a mid-flight
    #: dial flip applies to the NEXT dispatch, not a task already running.
    #: Always set: the column is NOT NULL DEFAULT 'trust', so every row —
    #: including every row written before the dial existed — carries a value.
    strictness: str = "trust"
    #: Caller-chosen PR base for a direct ``dispatch_task`` (v1-helper-resurface
    #: P1, PR-2). Validated against origin at launch; threaded into
    #: ``deliver_change(base_branch=...)`` (diff range + ``gh pr create
    #: --base``). None (the goal path, which pins neither) ⇒ the remote
    #: default branch.
    base_branch: Optional[str] = None
    #: Caller-pinned delivery branch for a direct ``dispatch_task`` (same seam):
    #: the launch step preps the workspace ONTO it, and delivery must land on
    #: it — a delivery that lands anywhere else fails the task (the
    #: continue-this-branch contract never silently degrades into a
    #: fresh-branch PR). None ⇒ today's auto-derived branch.
    target_branch: Optional[str] = None
    #: the owning project's reference key (#524 P3), stamped at dispatch. The
    #: per-project override knobs (review_gate, sandbox_image, browser_gate_mode)
    #: resolve BY this id, not by a workspace-path scan. None for the goal path
    #: (goals carry their own project_id) and for a task with no owning project.
    project_id: Optional[str] = None
    #: LEGACY fan-out lane metadata (spec 010 US3; the lane was retired by
    #: spec 022 US3). Nothing writes or reads it anymore — the column survives
    #: only on historical rows.
    lane_json: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "workspaceDir": self.workspace_dir,
            "goal": self.goal,
            "notifyUrl": self.notify_url,
            "resultJson": self.result_json,
            "error": self.error,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "programId": self.program_id,
            "dependsOn": self.depends_on,
            "orderIdx": self.order_idx,
            "milestone": self.milestone,
            "verifyCmd": self.verify_cmd,
            "deliver": self.deliver,
            "prUrl": self.pr_url,
            "title": self.title,
            "parentGoalId": self.parent_goal_id,
            "pauseCount": self.pause_count,
            "scaffold": self.scaffold,
            "preRunSha": self.pre_run_sha,
            "projectId": self.project_id,
        }


@dataclass
class TaskEvent:
    id: int
    task_id: str
    program_id: Optional[str]
    type: str
    source: str
    payload_json: str
    ts: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "taskId": self.task_id,
            "programId": self.program_id,
            "type": self.type,
            "source": self.source,
            "payloadJson": self.payload_json,
            "ts": self.ts,
        }


def _row_to_task(r: sqlite3.Row) -> Task:
    depends_on: list[str] = []
    if r["depends_on"]:
        try:
            parsed = json.loads(r["depends_on"])
            if isinstance(parsed, list):
                depends_on = [x for x in parsed if isinstance(x, str)]
        except json.JSONDecodeError:
            # tolerate a corrupt depends_on cell — treat as no deps
            pass
    return Task(
        id=r["id"],
        kind=r["kind"],
        status=r["status"],
        workspace_dir=r["workspace_dir"],
        goal=r["goal"],
        notify_url=r["notify_url"],
        result_json=r["result_json"],
        error=r["error"],
        created_at=r["created_at"],
        started_at=r["started_at"],
        completed_at=r["completed_at"],
        program_id=r["program_id"],
        depends_on=depends_on,
        order_idx=r["order_idx"],
        milestone=r["milestone"],
        verify_cmd=r["verify_cmd"],
        deliver=bool(r["deliver"]),
        pr_url=r["pr_url"],
        title=r["title"] if "title" in r.keys() else None,
        parent_goal_id=(
            r["parent_goal_id"] if "parent_goal_id" in r.keys() else None
        ),
        pause_count=(
            r["pause_count"] if "pause_count" in r.keys() and r["pause_count"] is not None else 0
        ),
        scaffold=(
            bool(r["scaffold"]) if "scaffold" in r.keys() and r["scaffold"] is not None else False
        ),
        plan_key=r["plan_key"] if "plan_key" in r.keys() else None,
        pre_run_sha=r["pre_run_sha"] if "pre_run_sha" in r.keys() else None,
        strictness=(
            r["strictness"] if "strictness" in r.keys() and r["strictness"] else "trust"
        ),
        base_branch=r["base_branch"] if "base_branch" in r.keys() else None,
        target_branch=r["target_branch"] if "target_branch" in r.keys() else None,
        project_id=r["project_id"] if "project_id" in r.keys() else None,
        lane_json=r["lane_json"] if "lane_json" in r.keys() else None,
    )




# ---- failure-class bucketing (eval_outcomes projection, ADR 0006) -----------
# Purely MECHANICAL string bucketing of a settled task's error text into a
# short class label — never an LLM call (the zero-token guard extends to the
# projection: classification carried the last two root-cause diagnoses without
# cognition, so the projection derives its classes the same way). Checked in
# priority order; first hit wins. The phrases are the stable marker strings the
# settle paths already emit (task_queue's _WORKER_BLOCKED_MARKER /
# _REVIEW_CRASH_MARKER, quality/task_gates' _verify_failure_summary, the
# timeout + pause-bound +
# delivery messages), so bucketing here can't drift from the wording without a
# test catching it. Basket report errors ride the same buckets — the reports
# store the identical settle-path texts.
_FAILURE_CLASS_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    # A MECHANICAL SETUP failure (toolchain-not-provisioned, git clone/fetch/clean
    # failure, target-branch prep failure) is something the worker CANNOT fix by
    # re-running the same instruction — first in priority so a later bucket can't
    # mis-claim it. Routes the goal loop to the damped mechanical:prep breaker
    # instead of an amnesiac re-dispatch storm (#379: the finance-sentry-ui goal
    # re-hit the identical trust/prep failure 119×). Markers are the CODE-OWNED
    # settle-path strings (runner toolchain error + engine.workspace WorkspaceError
    # + _prep_branch_target). The external Claude-CLI trust-guard wording is
    # deliberately NOT matched here — a bare "trust" substring would false-positive
    # on legitimate content, violating the fail-closed spirit; add it only once the
    # exact guard string is confirmed from a real incident log.
    ("mechanical_setup", ("toolchain_provision_failed", "clone failed:",
                          "fetch failed:", "clean -fdx failed",
                          "could not prepare target_branch")),
    ("blocked:worker", ("worker reported blocked:",)),
    ("review_crash", ("review gate crashed",)),
    ("review_rejected", ("code review requested changes",)),
    ("browser_gate_failed", ("browser gate (failing closed)",)),
    ("test_integrity", ("test-integrity",)),
    ("verify_failed", ("verify gate failed", "verify gate timed out")),
    ("timeout", ("wall-clock timeout",)),
    # A worker-conversation context overflow is deterministic at the QUEUE level
    # (a same-conversation retry replays the overflow) but a GOAL-level fresh
    # session may legitimately take a smaller bite — named so telemetry and the
    # advance brief's failure context can speak about the class.
    ("context_overflow", ("prompt is too long",)),
    # The sandbox memory cap killed the agent (runner-stamped kernel evidence,
    # spec 020). Deterministic at the queue level like the overflow above; the
    # goal layer keys its ONE adapted re-dispatch (FR-002a) on this class.
    ("sandbox_oom", ("sandbox oom-killed",)),
    ("delivery_failed", ("gate passed but delivery failed",)),
    ("no_result_line", ("no result line",)),
    # AUTH before the rate/quota bucket, mirroring loom.limits' priority: an
    # auth-flavored pause-bound failure is a login problem, not a cap.
    ("auth", ("failed to authenticate", "authentication required",
              "oauth session expired", "please run /login")),
    ("rate_limited", ("usage-limit pauses", "usage limit", "rate limit",
                      "out of extra usage", "out of usage", "quota")),
)


def derive_failure_class(error: Optional[str]) -> str:
    """Bucket a settled-failed task's error text into a short mechanical class
    (``review_rejected``, ``verify_failed``, ``timeout``, ``rate_limited``,
    ``blocked:worker``, …). Pure string matching — zero LLM, deterministic,
    best-effort: anything unrecognized lands in the ``engine_error`` catch-all
    rather than raising. Case-insensitive so wording-case drift can't unbucket
    a class silently."""
    text = (error or "").lower()
    for label, needles in _FAILURE_CLASS_RULES:
        if any(n in text for n in needles):
            return label
    return "engine_error"


def _row_to_event(r: sqlite3.Row) -> TaskEvent:
    return TaskEvent(
        id=r["id"],
        task_id=r["task_id"],
        program_id=r["program_id"],
        type=r["type"],
        source=r["source"],
        payload_json=r["payload_json"],
        ts=r["ts"],
    )


#: How long a blocked writer waits for the lock before raising
#: ``sqlite3.OperationalError: database is locked``. WAL gives concurrent reads +
#: a single writer, but the default busy_timeout is 0 — so a *separate* process
#: (e.g. the ``devclaw`` CLI) writing while the server holds the write lock fails
#: instantly instead of waiting its turn. A few seconds lets contending writers
#: queue politely. Shared default with ``project_registry`` (same db file).
SQLITE_BUSY_TIMEOUT_MS = 5000
