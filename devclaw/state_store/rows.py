"""Pure data + row mappers for the state store — no connection, no state."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Literal, Optional

TaskStatus = Literal["pending", "running", "done", "failed", "cancelled"]
TaskKind = Literal["implement_feature", "fix_bug", "review_repository"]

#: How a session ended, as the host observed it (spec 046). The session's own
#: exit line (DELIVERED / DONE / BLOCKED / NOTHING) is parsed from its final
#: message; the rest are host outcomes. ``INTERRUPTED`` means "resume next tick".
EXIT_DELIVERED = "DELIVERED"
EXIT_DONE = "DONE"
EXIT_BLOCKED = "BLOCKED"
EXIT_NOTHING = "NOTHING"
EXIT_INTERRUPTED = "INTERRUPTED"
EXIT_REFUSED = "REFUSED"      # a host gate refused the delivery (gate inputs, binaries, span)
EXIT_REVIEW = "REVIEW"        # a read-only done-gate review session
EXITS = (EXIT_DELIVERED, EXIT_DONE, EXIT_BLOCKED, EXIT_NOTHING,
         EXIT_INTERRUPTED, EXIT_REFUSED, EXIT_REVIEW)


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class Task:
    id: str
    kind: TaskKind
    status: TaskStatus
    workspace_dir: str
    goal: str
    result_json: Optional[str]
    error: Optional[str]
    created_at: int
    started_at: Optional[int]
    completed_at: Optional[int]
    verify_cmd: Optional[str]
    deliver: bool
    pr_url: Optional[str]
    parent_goal_id: Optional[str] = None
    #: usage-limit pause → requeue count (bounds the pause loop)
    pause_count: int = 0
    #: the pre-run HEAD the span is measured from (spec 013)
    pre_run_sha: Optional[str] = None
    target_branch: Optional[str] = None
    project_id: Optional[str] = None
    #: how the session ended (one of :data:`EXITS`); None while it runs
    exit: Optional[str] = None
    #: the exit line's text (the question for BLOCKED, the fact for INTERRUPTED)
    exit_detail: Optional[str] = None
    #: the owner was told about this session's outcome (one ping per block)
    notified: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "workspaceDir": self.workspace_dir,
            "goal": self.goal,
            "resultJson": self.result_json,
            "error": self.error,
            "createdAt": self.created_at,
            "startedAt": self.started_at,
            "completedAt": self.completed_at,
            "verifyCmd": self.verify_cmd,
            "deliver": self.deliver,
            "prUrl": self.pr_url,
            "parentGoalId": self.parent_goal_id,
            "pauseCount": self.pause_count,
            "preRunSha": self.pre_run_sha,
            "targetBranch": self.target_branch,
            "projectId": self.project_id,
            "exit": self.exit,
            "exitDetail": self.exit_detail,
        }


@dataclass
class TaskEvent:
    id: int
    task_id: str
    type: str
    source: str
    payload_json: str
    ts: int

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "taskId": self.task_id,
            "type": self.type,
            "source": self.source,
            "payloadJson": self.payload_json,
            "ts": self.ts,
        }


@dataclass
class Goal:
    """A durable goal (spec 046): the issue is the contract, the branch is the
    delivery, GitHub holds the state. The host stores identity plus the last
    world fingerprint it handed a session — an observation, never a judgment."""

    id: str
    project_id: str
    workspace_dir: str
    repo_url: str
    objective: str
    issues: list[int]
    branch: str
    created_at: int
    closed_at: Optional[int] = None
    #: ``achieved`` | ``cancelled`` | None while open
    outcome: Optional[str] = None
    #: the world fingerprint the last session was given (JSON), or None
    last_seen_json: Optional[str] = None
    last_seen_at: Optional[int] = None

    @property
    def open(self) -> bool:
        return self.outcome is None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "projectId": self.project_id,
            "workspaceDir": self.workspace_dir,
            "repoUrl": self.repo_url,
            "objective": self.objective,
            "issues": list(self.issues),
            "branch": self.branch,
            "createdAt": self.created_at,
            "closedAt": self.closed_at,
            "outcome": self.outcome,
            "lastSeenAt": self.last_seen_at,
        }


@dataclass
class Decision:
    id: int
    goal_id: str
    text: str
    comment_url: str
    made_at: int

    def to_dict(self) -> dict:
        return {"id": self.id, "goalId": self.goal_id, "text": self.text,
                "commentUrl": self.comment_url, "madeAt": self.made_at}


def _col(r: sqlite3.Row, name: str, default=None):
    return r[name] if name in r.keys() and r[name] is not None else default


def _row_to_task(r: sqlite3.Row) -> Task:
    return Task(
        id=r["id"], kind=r["kind"], status=r["status"],
        workspace_dir=r["workspace_dir"], goal=r["goal"],
        result_json=r["result_json"], error=r["error"],
        created_at=r["created_at"], started_at=r["started_at"],
        completed_at=r["completed_at"], verify_cmd=r["verify_cmd"],
        deliver=bool(r["deliver"]), pr_url=r["pr_url"],
        parent_goal_id=_col(r, "parent_goal_id"),
        pause_count=int(_col(r, "pause_count", 0)),
        pre_run_sha=_col(r, "pre_run_sha"),
        target_branch=_col(r, "target_branch"),
        project_id=_col(r, "project_id"),
        exit=_col(r, "exit"),
        exit_detail=_col(r, "exit_detail"),
        notified=bool(_col(r, "notified", 0)),
    )


def _row_to_event(r: sqlite3.Row) -> TaskEvent:
    return TaskEvent(id=r["id"], task_id=r["task_id"], type=r["type"],
                     source=r["source"], payload_json=r["payload_json"], ts=r["ts"])


def _row_to_goal(r: sqlite3.Row) -> Goal:
    import json

    try:
        issues = [int(x) for x in json.loads(r["issues_json"] or "[]")]
    except (ValueError, TypeError):
        issues = []
    return Goal(
        id=r["id"], project_id=r["project_id"], workspace_dir=r["workspace_dir"],
        repo_url=r["repo_url"] or "", objective=r["objective"] or "",
        issues=issues, branch=r["branch"], created_at=r["created_at"],
        closed_at=r["closed_at"], outcome=r["outcome"],
        last_seen_json=r["last_seen_json"], last_seen_at=r["last_seen_at"],
    )


def _row_to_decision(r: sqlite3.Row) -> Decision:
    return Decision(id=r["id"], goal_id=r["goal_id"], text=r["text"],
                    comment_url=r["comment_url"] or "", made_at=r["made_at"])


#: How long a blocked writer waits for the lock before raising. WAL gives
#: concurrent reads + a single writer; a separate process (the CLI) writing
#: while the server holds the lock queues politely instead of failing.
SQLITE_BUSY_TIMEOUT_MS = 5000
