"""The world a goal lives in, read fresh every tick (spec 046, pillar 5).

The host holds nothing it concluded. What it hands a session — and what it
compares to decide whether anything happened — is this observation: the
PR's head and state, the CI rollup, the newest instruction and the newest
devclaw record on the threads, the credentials that probe green, the issues'
states. :meth:`World.fingerprint` is the part that must MOVE for a session
to spawn; everything else is context for the prompt.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..state_store import Goal
from . import github as _gh


@dataclass(frozen=True)
class World:
    pr: _gh.PrFacts
    issues: tuple[_gh.Issue, ...]
    comments: tuple[_gh.Comment, ...]
    green_credentials: tuple[str, ...] = ()
    read_error: str = ""

    # ---- the derived facts the tick rule reads -----------------------------

    @property
    def last_instruction(self) -> Optional[_gh.Comment]:
        instr = [c for c in self.comments if c.is_instruction]
        return instr[-1] if instr else None

    @property
    def last_instruction_id(self) -> int:
        c = self.last_instruction
        return c.id if c else 0

    def records(self, kind: str) -> tuple[_gh.Comment, ...]:
        return tuple(c for c in self.comments if c.marker == kind)

    @property
    def last_block_id(self) -> int:
        """The newest devclaw record that stops the goal: a ``block`` comment,
        or a ``verdict`` that did not achieve."""
        ids = [c.id for c in self.records("block")]
        ids += [c.id for c in self.records("verdict") if c.field("achieved") != "1"]
        return max(ids) if ids else 0

    @property
    def blocked(self) -> bool:
        """Blocked = a devclaw stop newer than the owner's last instruction."""
        return self.last_block_id > self.last_instruction_id

    def block_exists(self, *, task_id: str = "", head: str = "") -> bool:
        for c in self.records("block"):
            if task_id and c.field("task") == task_id:
                return True
            if head and c.field("head") == head:
                return True
        return False

    def verdicts_for_head(self, head: str) -> tuple[_gh.Comment, ...]:
        return tuple(c for c in self.records("verdict") if head and c.field("head") == head)

    @property
    def fingerprint(self) -> dict:
        return {
            "pr": self.pr.state,
            "head": self.pr.head_sha,
            "ci": self.pr.ci,
            "instruction": self.last_instruction_id,
            "record": max([c.id for c in self.comments if c.marker] + [0]),
            "credentials": list(self.green_credentials),
            "issues": [[i.number, i.state] for i in self.issues],
        }

    def fingerprint_json(self) -> str:
        return json.dumps(self.fingerprint, sort_keys=True)


#: injectable readers (tests pass fakes; production binds the gh-backed ones)
PrReader = Callable[[str, str], Awaitable[_gh.PrFacts]]
IssueReader = Callable[[str, int], Awaitable[_gh.Issue]]
CommentsReader = Callable[[str, int], Awaitable[list[_gh.Comment]]]
CredentialProbe = Callable[[], tuple[str, ...]]


@dataclass
class WorldReader:
    pr: PrReader = _gh.pr_facts
    issue: IssueReader = _gh.fetch_issue
    comments: CommentsReader = _gh.list_comments
    credentials: CredentialProbe = field(default=lambda: ())

    async def read(self, goal: Goal) -> World:
        pr = await self.pr(goal.repo_url, goal.branch)
        issues: list[_gh.Issue] = []
        error = ""
        for n in goal.issues:
            try:
                issues.append(await self.issue(goal.repo_url, n))
            except _gh.IssueError as exc:
                error = str(exc)
        numbers = list(goal.issues) + ([pr.number] if pr.number else [])
        comments: list[_gh.Comment] = []
        for n in numbers:
            comments.extend(await self.comments(goal.repo_url, n))
        comments.sort(key=lambda c: c.id)
        try:
            creds = await asyncio.to_thread(self.credentials)
        except Exception:  # noqa: BLE001 — a probe hiccup is "unknown", never a wedge
            creds = ()
        return World(pr=pr, issues=tuple(issues), comments=tuple(comments),
                     green_credentials=tuple(creds), read_error=error)
