"""End-to-end regressions for planned fan-out (spec 010 US3, FR-101/FR-102/FR-103).

This module carries **the spec's own Independent Test**: two `[P]` tasks with
disjoint scopes execute concurrently, integrate serially, and a deliberately
out-of-scope edit in one increment fails that increment while the other lands.

Real git in tmp directories (the lane/integration mechanics are git mechanics —
stubbing them would test nothing), a stub runner in place of the agent, and a
stub delivery in place of the push. No docker, no claude, no network.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

from devclaw.queue import settle as queue_settle
from devclaw.delivery.integrate import commit_lane, integrate_lane
from devclaw.program_plan import PlannedTask
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue

GOAL_BRANCH = "goal/g1"

PLAN = """# Tasks

- [ ] T002 [P] [US1] Renderer (scope: src/widget/**)
- [ ] T003 [P] [US1] Store (scope: src/store/**)
"""


def _git(*args: str, cwd: str) -> str:
    p = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False
    )
    assert p.returncode == 0, f"git {' '.join(args)} failed: {p.stdout}{p.stderr}"
    return p.stdout.strip()


def _init_repo(path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    d = str(path)
    _git("init", "-q", "-b", "main", cwd=d)
    _git("config", "user.email", "t@example.com", cwd=d)
    _git("config", "user.name", "T", cwd=d)
    (path / "specs" / "010-feat").mkdir(parents=True)
    (path / "specs" / "010-feat" / "tasks.md").write_text(PLAN, encoding="utf-8")
    (path / "src").mkdir()
    (path / "src" / "base.py").write_text("BASE = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=d)
    _git("commit", "-q", "-m", "base", cwd=d)
    _git("checkout", "-q", "-b", GOAL_BRANCH, cwd=d)
    return d


def _clone(src: str, dst) -> str:
    _git("clone", "-q", "--branch", GOAL_BRANCH, src, str(dst), cwd=str(dst.parent))
    d = str(dst)
    _git("config", "user.email", "t@example.com", cwd=d)
    _git("config", "user.name", "T", cwd=d)
    return d


def _write_and_commit(ws: str, rel: str, body: str, message: str) -> None:
    import os

    path = os.path.join(ws, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    _git("add", "-A", cwd=ws)
    # ``--allow-empty``: since spec 013 a retry keeps the workspace (FR-012), so
    # a fake runner re-writing the same content on attempt 2 has nothing new to
    # record. That is the production shape too — the agent iterates on its own
    # output — and it must not blow up the fixture.
    _git("commit", "-q", "--allow-empty", "-m", message, cwd=ws)


# ---- the git mechanics -----------------------------------------------------


async def test_a_lane_commits_and_merges_into_the_shared_goal_branch(tmp_path):
    shared = _init_repo(tmp_path / "shared")
    lane = _clone(shared, tmp_path / "lane0")
    # the agent left its work UNcommitted — the lane commits it on the way in
    (tmp_path / "lane0" / "src" / "widget").mkdir(parents=True)
    (tmp_path / "lane0" / "src" / "widget" / "render.py").write_text("W = 1\n")

    assert await commit_lane(lane, label="Renderer", task_id="T002") is None
    assert (
        await integrate_lane(
            lane_dir=lane, into_dir=shared, label="Renderer", task_id="T002"
        )
        is None
    )
    assert (tmp_path / "shared" / "src" / "widget" / "render.py").exists()
    assert "Devclaw-Lane: T002" in _git("log", "-3", "--format=%B", cwd=shared)


async def test_an_already_committed_lane_needs_nothing_added(tmp_path):
    shared = _init_repo(tmp_path / "shared")
    lane = _clone(shared, tmp_path / "lane0")
    _write_and_commit(lane, "src/widget/render.py", "W = 1\n", "feat: renderer")
    before = _git("rev-parse", "HEAD", cwd=lane)

    assert await commit_lane(lane, label="Renderer", task_id="T002") is None
    assert _git("rev-parse", "HEAD", cwd=lane) == before  # no empty commit
    assert await integrate_lane(
        lane_dir=lane, into_dir=shared, label="Renderer", task_id="T002"
    ) is None
    assert (tmp_path / "shared" / "src" / "widget" / "render.py").exists()


async def test_a_conflicting_lane_fails_loudly_and_leaves_the_shared_branch_clean(tmp_path):
    shared = _init_repo(tmp_path / "shared")
    lane = _clone(shared, tmp_path / "lane0")
    # both edit the same line — the case disjoint declared scopes exist to prevent
    _write_and_commit(lane, "src/base.py", "BASE = 'lane'\n", "lane edit")
    _write_and_commit(shared, "src/base.py", "BASE = 'shared'\n", "shared edit")
    head_before = _git("rev-parse", "HEAD", cwd=shared)

    err = await integrate_lane(
        lane_dir=lane, into_dir=shared, label="Base", task_id="T002"
    )
    assert err is not None and "T002" in err
    assert "disjoint declared file scopes" in err
    # aborted: the goal branch is exactly as it was, and nothing is half-merged
    assert _git("rev-parse", "HEAD", cwd=shared) == head_before
    assert _git("status", "--porcelain", cwd=shared) == ""
    assert (tmp_path / "shared" / "src" / "base.py").read_text() == "BASE = 'shared'\n"


async def test_two_disjoint_lanes_integrate_serially_onto_one_goal_branch(tmp_path):
    """FR-102: both land, and they land in PLAN order — lane 1 integrating first
    would still leave the history in lane-0-then-lane-1 sequence."""
    shared = _init_repo(tmp_path / "shared")
    lane_a = _clone(shared, tmp_path / "laneA")
    lane_b = _clone(shared, tmp_path / "laneB")
    _write_and_commit(lane_a, "src/widget/render.py", "W = 1\n", "feat: renderer")
    _write_and_commit(lane_b, "src/store/db.py", "S = 1\n", "feat: store")

    assert await integrate_lane(
        lane_dir=lane_a, into_dir=shared, label="Renderer", task_id="T002"
    ) is None
    assert await integrate_lane(
        lane_dir=lane_b, into_dir=shared, label="Store", task_id="T003"
    ) is None

    assert (tmp_path / "shared" / "src" / "widget" / "render.py").exists()
    assert (tmp_path / "shared" / "src" / "store" / "db.py").exists()
    # Serial, in plan order: lane A fast-forwarded first, so when lane B merged,
    # A's tip was already the branch — HEAD's FIRST parent is A, its second is B.
    parents = _git("rev-list", "--parents", "-n", "1", "HEAD", cwd=shared).split()
    assert parents[1] == _git("rev-parse", "HEAD", cwd=lane_a)
    assert parents[2] == _git("rev-parse", "HEAD", cwd=lane_b)


# ---- the spec's Independent Test, through the real queue -------------------


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _lane(position: int, key: str, scopes: list, integrate_into: str, workspace_dir: str):
    return PlannedTask(
        key=key,
        goal=f"lane {key}",
        kind="implement_feature",
        workspace_dir=workspace_dir,
        lane={
            "position": position,
            "key": key,
            "label": f"{key} work",
            "scopes": scopes,
            "integrate_into": integrate_into,
        },
    )


async def test_an_out_of_scope_lane_fails_while_its_sibling_lands(tmp_path, store, monkeypatch):
    """**The spec's Independent Test.** Two `[P]` tasks with disjoint scopes run
    concurrently; the one that edits outside its declared scope fails on its own,
    and the well-behaved one still integrates and ships."""
    shared = _init_repo(tmp_path / "shared")
    lane_a = _clone(shared, tmp_path / "laneA")
    lane_b = _clone(shared, tmp_path / "laneB")

    delivered: list = []

    async def _fake_deliver(*, workspace_dir, task_id, goal, **kw):
        delivered.append(workspace_dir)
        return {"pr_url": "https://github.com/o/r/pull/1", "branch": GOAL_BRANCH}

    monkeypatch.setattr(queue_settle, "deliver_change", _fake_deliver)

    concurrent = {"now": 0, "peak": 0}
    # A rendezvous, not a sleep: each lane blocks until its sibling has also
    # entered the runner, so `peak == 2` can only happen if the two increments
    # are genuinely in flight at once (FR-101). Serialized execution would time
    # out here and leave the peak at 1.
    both_in_flight = asyncio.Barrier(2)
    seen: set = set()

    async def runner(req):
        concurrent["now"] += 1
        concurrent["peak"] = max(concurrent["peak"], concurrent["now"])
        try:
            if req.workspace_dir not in seen:
                # First entry only: a gate-failed lane is RETRIED, and a retry
                # must not sit at a barrier its sibling has already left.
                seen.add(req.workspace_dir)
                try:
                    await asyncio.wait_for(both_in_flight.wait(), timeout=5)
                except (asyncio.TimeoutError, asyncio.BrokenBarrierError):  # pragma: no cover
                    pass
            if req.workspace_dir == lane_a:
                _write_and_commit(lane_a, "src/widget/render.py", "W = 1\n", "feat: renderer")
            else:
                # …and this one strays outside its declared scope
                _write_and_commit(lane_b, "src/store/db.py", "S = 1\n", "feat: store")
                _write_and_commit(lane_b, "src/widget/sneaky.py", "X = 1\n", "oops")
        finally:
            concurrent["now"] -= 1
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=runner)
    program_id = q.start_planned_program(
        goal="ship the widget",
        workspace_dir=shared,
        planned=[
            _lane(0, "T002", ["src/widget/**"], shared, lane_a),
            _lane(1, "T003", ["src/store/**"], shared, lane_b),
        ],
        open_pr=True,
    )
    await q.drain()

    tasks = {t.plan_key: t for t in store.list_program_tasks(program_id)}
    assert concurrent["peak"] == 2, "the two lanes must actually run concurrently"

    # the in-scope lane landed
    assert tasks["T002"].status == "done"
    assert tasks["T002"].pr_url == "https://github.com/o/r/pull/1"
    assert (tmp_path / "shared" / "src" / "widget" / "render.py").exists()

    # the straying lane failed — alone, and with the violation named
    assert tasks["T003"].status == "failed"
    assert "declared-scope violation" in (tasks["T003"].error or "")
    assert "src/widget/sneaky.py" in (tasks["T003"].error or "")
    assert not (tmp_path / "shared" / "src" / "store" / "db.py").exists()
    assert not (tmp_path / "shared" / "src" / "widget" / "sneaky.py").exists()

    # delivery ran once, from the SHARED workspace — one goal branch, one PR
    assert delivered == [shared]


async def test_both_lanes_land_when_both_stay_in_scope(tmp_path, store, monkeypatch):
    """The other half of the Independent Test: concurrent execution, serial
    integration, both increments on one goal branch in plan order."""
    shared = _init_repo(tmp_path / "shared")
    lane_a = _clone(shared, tmp_path / "laneA")
    lane_b = _clone(shared, tmp_path / "laneB")

    async def _fake_deliver(*, workspace_dir, task_id, goal, **kw):
        return {"pr_url": "https://github.com/o/r/pull/1", "branch": GOAL_BRANCH}

    monkeypatch.setattr(queue_settle, "deliver_change", _fake_deliver)

    async def runner(req):
        if req.workspace_dir == lane_a:
            _write_and_commit(lane_a, "src/widget/render.py", "W = 1\n", "feat: renderer")
        else:
            _write_and_commit(lane_b, "src/store/db.py", "S = 1\n", "feat: store")
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=runner)
    program_id = q.start_planned_program(
        goal="ship it",
        workspace_dir=shared,
        planned=[
            _lane(0, "T002", ["src/widget/**"], shared, lane_a),
            _lane(1, "T003", ["src/store/**"], shared, lane_b),
        ],
        open_pr=True,
    )
    await q.drain()

    tasks = {t.plan_key: t for t in store.list_program_tasks(program_id)}
    assert {t.status for t in tasks.values()} == {"done"}
    assert (tmp_path / "shared" / "src" / "widget" / "render.py").exists()
    assert (tmp_path / "shared" / "src" / "store" / "db.py").exists()
    # integration was serial and in PLAN order — lane 0 is HEAD's first parent
    parents = _git("rev-list", "--parents", "-n", "1", "HEAD", cwd=shared).split()
    assert parents[1] == _git("rev-parse", "HEAD", cwd=lane_a)
    assert parents[2] == _git("rev-parse", "HEAD", cwd=lane_b)


async def test_an_ordinary_task_is_untouched_by_any_of_this(tmp_path, store, monkeypatch):
    """The byte-identical requirement at the execution end: a task with no lane
    metadata never integrates, never queues, and delivers from its own
    workspace exactly as before."""
    ws = _init_repo(tmp_path / "plain")
    delivered: list = []

    async def _fake_deliver(*, workspace_dir, task_id, goal, **kw):
        delivered.append(workspace_dir)
        return {"pr_url": "https://github.com/o/r/pull/9", "branch": GOAL_BRANCH}

    monkeypatch.setattr(queue_settle, "deliver_change", _fake_deliver)

    async def runner(req):
        _write_and_commit(ws, "src/widget/render.py", "W = 1\n", "feat: renderer")
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=ws, goal="do X", deliver=True)
    await q.drain()

    row = store.get_task(tid)
    assert row.status == "done" and row.lane() is None
    assert delivered == [ws]


async def test_a_lane_cannot_escape_its_scope_by_not_committing(tmp_path, store, monkeypatch):
    """#630 end to end: delivery stages everything in the workspace, so leaving
    the out-of-scope file uncommitted used to hide it from every gate while
    shipping it anyway. The scope gate reads the workspace, so the lane fails."""
    shared = _init_repo(tmp_path / "shared")
    lane_a = _clone(shared, tmp_path / "laneA")

    async def _fake_deliver(*, workspace_dir, task_id, goal, **kw):  # pragma: no cover
        raise AssertionError("a scope violation must never reach delivery")

    monkeypatch.setattr(queue_settle, "deliver_change", _fake_deliver)

    async def runner(req):
        _write_and_commit(lane_a, "src/widget/render.py", "W = 1\n", "feat: renderer")
        # …and this one is deliberately never recorded
        (tmp_path / "laneA" / "src" / "store").mkdir(parents=True, exist_ok=True)
        (tmp_path / "laneA" / "src" / "store" / "sneaky.py").write_text("X = 1\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=runner)
    program_id = q.start_planned_program(
        goal="ship the widget",
        workspace_dir=shared,
        planned=[_lane(0, "T002", ["src/widget/**"], shared, lane_a)],
        open_pr=True,
    )
    await q.drain()

    task = store.list_program_tasks(program_id)[0]
    assert task.status == "failed"
    assert "src/store/sneaky.py" in (task.error or "")
    assert not (tmp_path / "shared" / "src" / "widget" / "render.py").exists()
