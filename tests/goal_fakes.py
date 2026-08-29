"""Shared goal-layer test doubles — no network, no claude, deterministic clock."""

from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from devclaw.goal.models import Action, Goal, InFlight, PollResult
from devclaw.loom import trace as _trace


def register_tmp_project(
    registry,
    workspace_dir,
    *,
    project_id: str = "tmp-proj",
    repo_url: str | None = None,
    git_init: bool = True,
    **knobs,
) -> str:
    """Register a throwaway project row and return its ``project_id`` — the
    migration helper for tests that dispatch work.

    Spec 003 (#520) made ``project_id`` the dispatch contract and added a
    git-checkout preflight at admission, so a dispatched workspace must be a real
    git repo. This helper ``git init``s the workspace (unless ``git_init=False``,
    for tests that WANT to exercise the non-git preflight rejection), mirroring
    the real-git fixture style in ``test_review_gate.py``. ``**knobs`` (automerge=…,
    review_gate=…, sandbox_image=…) pass straight through to ``registry.create``."""
    ws = Path(workspace_dir)
    ws.mkdir(parents=True, exist_ok=True)
    if git_init and not (ws / ".git").exists():
        subprocess.run(["git", "init", "-q", str(ws)], check=True)
        (ws / "README.md").write_text("tmp project\n")
    registry.create(
        id=project_id, name=project_id, workspace_dir=str(ws),
        repo_url=repo_url, **knobs,
    )
    return project_id


class FakeClaude:
    """A claude_caller that returns a canned response and counts calls.

    The call count IS the quota assertion — an idle tick must leave it at 0.
    Used for both the planner caller and the evaluator caller. Records into the
    active tracer (if one is set) under the given ``role`` so the trace harness
    sees the same shape it would in live mode.
    """

    def __init__(self, response: str = "{}", *, role: str = "fake") -> None:
        self.response = response
        self.calls = 0
        self.last_prompt = ""
        self.role = role

    async def __call__(self, prompt: str) -> str:
        self.calls += 1
        self.last_prompt = prompt
        _trace.record_cognition(
            role=self.role, model="(stub)", prompt=prompt, response=self.response, latency_ms=0,
        )
        return self.response


class FakeEngine:
    """In-process engine double — records dispatches, returns canned polls."""

    def __init__(
        self, poll_result: PollResult | None = None, dispatch_ref: InFlight | None = None,
        poll_exc: Exception | None = None,
        db_size_alert_msg: str | None = None,
        problems: list[dict] | None = None,
        db_size_bytes_val: int = 0,
    ) -> None:
        self.poll_result = poll_result
        self.dispatch_ref = dispatch_ref
        #: when set, poll raises it — models the engine losing the ref's row
        #: (e.g. GoalEngineError "unknown task_id" after a DB loss/restore).
        self.poll_exc = poll_exc
        self.dispatched: list[tuple[Action, Goal, str]] = []
        self.polls = 0
        #: self-triage / DB-size-alarm seams. Default None/empty/0 so a plain
        #: FakeEngine reports NO alert — the alarm getattr seam returns None and
        #: _maybe_alert_db_size is a no-op, exactly as before this seam existed.
        self.db_size_alert_msg = db_size_alert_msg
        self.problems = problems or []
        self.db_size_bytes_val = db_size_bytes_val

    def check_db_size_alert(self) -> str | None:
        return self.db_size_alert_msg

    def list_problems(
        self, *, category: str | None = None, limit: int = 100, include_issue: bool = False
    ) -> list[dict]:
        return self.problems

    def db_size_bytes(self) -> int:
        return self.db_size_bytes_val

    async def dispatch(self, action: Action, goal: Goal, notify_url: str) -> InFlight:
        self.dispatched.append((action, goal, notify_url))
        return self.dispatch_ref or InFlight("devclaw", action.tool, "task_x", "task", action.goal)

    async def poll(self, ref: InFlight) -> PollResult:
        self.polls += 1
        if self.poll_exc is not None:
            raise self.poll_exc
        assert self.poll_result is not None, "poll called but no poll_result configured"
        return self.poll_result


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, text: str) -> bool:
        self.sent.append(text)
        return True


async def fake_prepare(
    workspace_dir: str, repo_url: str | None = None, branch: str | None = None,
) -> str:
    """No-op workspace prep for tick tests. Returns the requested branch when
    one is passed (mirrors the real prepare_workspace return for goal-branch
    mode) so a settle-side assertion can check what branch was prepped."""
    return branch or "main"


class Clock:
    """Injectable, advanceable clock."""

    def __init__(self, t: datetime | None = None) -> None:
        self.t = t or datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        from datetime import timedelta

        self.t = self.t + timedelta(seconds=seconds)


def seed_marker_repo(base_dir):
    """A real on-disk git repo with .NET/Angular markers (the closeloop-bench
    shape — cf. test_review_gate.py's grounding regression) so a repo-context
    snapshot collected from it carries real probe lines (``global.json: file``,
    ``pyproject.toml: missing``, a ``frontend`` top-level dir). Returns the
    repo path — pass it as ``seed_goal(..., workspace_dir=str(repo))``."""
    import subprocess

    repo = base_dir / "marker-repo"
    repo.mkdir(parents=True)

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    _git("init", "-q", "-b", "main")
    _git("config", "user.email", "t@t")
    _git("config", "user.name", "t")
    (repo / "global.json").write_text('{"sdk": {"version": "9.0.315"}}\n')
    (repo / "frontend").mkdir()
    (repo / "frontend" / "angular.json").write_text("{}\n")
    _git("add", "-A")
    _git("commit", "-q", "-m", "init")
    return repo


def seed_goal(
    goals_dir, goal_id: str = "demo", *, cadence: str = "1d", backlog: list[str] | None = None,
    repo_url: str | None = "https://example.com/demo.git",
    workspace_dir: str = "/repos/demo",
    done_when: str = "all backlog items merged",
    mode: str | None = None,
    project_id: str | None = None,
    out_of_scope: list[str] | None = None,
    invariants: list[str] | None = None,
    established: list[str] | None = None,
    issue_refs: list[int] | None = None,
) -> None:
    """Write a minimal goal.yaml under goals_dir/<goal_id>/.

    ``repo_url`` defaults to a fake existing-repo URL (the existing-repo shape
    most tests historically exercise). Pass ``repo_url=None`` to seed a
    from-scratch goal. (The investigating/world-research phases this default
    used to route between were removed with the host-cognition chain, spec 008
    shrink — every goal executes directly; the worker grounds itself
    in-sandbox.)

    ``workspace_dir`` defaults to a shared fake path; override it when a test
    needs distinct goals to resolve to distinct projects (e.g. per-project
    automerge — see test_goal_tick.py's tick_all merger_resolver tests).

    **Seeding two goals with the default workspace puts them on the SAME
    project**, so the single-writer project hold (spec 010 P1) makes the second
    one QUEUE instead of dispatching. That is correct production behaviour, but
    it silently starves any test whose goals are meant to be independent — pass
    an explicit per-goal ``workspace_dir`` unless contention is the point.

    The three saga slots (spec 012 US2) default to ABSENT keys — the shape of
    every goal.yaml authored before that schema, and the shape the whole suite
    is written against. Pass a list (``[]`` included) to seed a slot-authored
    goal; the difference between an absent key and an empty list is exactly
    what keeps live prose-authored goals rendering unchanged.

    Pass a standing-shaped ``done_when`` ("this is a standing goal") to
    exercise the standing-goal done-gate contract.
    """
    import yaml

    d = goals_dir / goal_id
    d.mkdir(parents=True, exist_ok=True)
    doc = {
        "objective": "Drive the demo repo to done.",
        "cadence": cadence,
        "engine": "devclaw",
        "workspace_dir": workspace_dir,
        "repo_url": repo_url,
        "verify_cmd": "pytest -q",
        "open_pr": True,
        "done_when": done_when,
        "backlog": backlog or ["add a /health endpoint", "add request logging"],
    }
    if mode is not None:
        doc["mode"] = mode
    if project_id is not None:
        doc["project_id"] = project_id
    if issue_refs is not None:
        doc["issue_refs"] = list(issue_refs)
    for key, value in (
        ("out_of_scope", out_of_scope),
        ("invariants", invariants),
        ("established", established),
    ):
        if value is not None:
            doc[key] = list(value)
    (d / "goal.yaml").write_text(yaml.safe_dump(doc))


class FakeIssueFetcher:
    """Scripted issue fetch for the referenced lane (spec 019): map issue
    number → :class:`~devclaw.goal.issue_ref.IssueSnapshot` (or an Exception
    to raise). ``.calls`` is the zero-fetch idle-guard counter — the sibling
    of ``FakeClaude.calls``: it must stay 0 on idle and blocked tick paths."""

    def __init__(self, issues: dict | None = None):
        self._issues = dict(issues or {})
        self.calls = 0

    async def __call__(self, repo_url: str, number: int):
        from devclaw.goal.issue_ref import IssueRefError

        self.calls += 1
        v = self._issues.get(number)
        if isinstance(v, Exception):
            raise v
        if v is None:
            raise IssueRefError(f"scripted: no such issue #{number}")
        return v
