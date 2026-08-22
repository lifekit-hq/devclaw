"""An empty span is a first-class outcome (spec 013 FR-006/FR-011/FR-014).

"The agent accomplished nothing" and "nothing needed doing" are the same row
today: both settle ``done`` and both reset the no-progress watchdog. Once the
span is mechanical, the emptiness is knowable — so a code-writing task that
changed nothing settles successfully, publishes nothing, and is reported
upstream as NO PROGRESS. Failing it would punish a run that was correct to do
nothing; plain success is the false-green being closed.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devclaw import task_queue
from devclaw.engine import EngineRequest
from devclaw.goal.engine import _no_change
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _gate():
    return {"ran": True, "cmd": "pytest", "passed": True, "exit_code": 0,
            "timed_out": False, "output": ""}


def _repo(tmp_path, name="ws"):
    d = tmp_path / name
    d.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)
    (d / "README.md").write_text("# base\n")
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", "base"],
                   check=True, capture_output=True)
    return d


async def test_a_code_task_that_changed_nothing_settles_done_without_publishing(
    store, tmp_path, monkeypatch,
):
    delivered: list = []

    async def _never(**kwargs):  # pragma: no cover — must not be called
        delivered.append(kwargs)
        return {"delivered": True, "pr_url": "https://example/pull/1"}

    monkeypatch.setattr(task_queue, "deliver_change", _never)
    ws = _repo(tmp_path)

    async def runner(req: EngineRequest):
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate()}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest", deliver=True)
    await q.drain()

    row = store.get_task(tid)
    assert row.status == "done" and row.pr_url is None
    assert delivered == []
    result = json.loads(row.result_json)
    assert result["no_change"] is True
    assert result["change"]["status"] == "no_change"


async def test_a_no_change_settle_is_reported_as_no_progress_not_a_delivery(
    store, tmp_path,
):
    """FR-014's upstream half: the goal layer's poll must be able to tell the
    two apart, because the no-progress watchdog keys off exactly this."""
    ws = _repo(tmp_path)

    async def runner(req: EngineRequest):
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate()}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest")
    await q.drain()
    assert _no_change(store.get_task(tid).result_json) is True


async def test_a_task_that_did_change_something_is_never_flagged_no_change(
    store, tmp_path,
):
    ws = _repo(tmp_path)

    async def runner(req: EngineRequest):
        (ws / "f.py").write_text("F = 1\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate()}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest")
    await q.drain()
    result = json.loads(store.get_task(tid).result_json)
    assert "no_change" not in result
    assert result["change"]["status"] == "change"
    assert _no_change(store.get_task(tid).result_json) is False


async def test_a_read_only_task_that_changes_nothing_is_not_flagged_no_change(
    store, tmp_path,
):
    """FR-011: a review writes a report, not code. Its deliverable is the
    report, so changing nothing is success — and counting it as no progress
    would break the watchdog for verification-heavy goals."""
    ws = _repo(tmp_path)

    async def runner(req: EngineRequest):
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "report": "all good"}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="review_repository", workspace_dir=str(ws), goal="review it")
    await q.drain()

    row = store.get_task(tid)
    assert row.status == "done"
    result = json.loads(row.result_json)
    assert "no_change" not in result
    assert _no_change(row.result_json) is False


async def test_a_no_change_settle_does_not_reset_the_no_progress_watchdog(tmp_path):
    """The watchdog's whole job is telling a goal that ships from a goal that
    spins. A settle that published nothing must not refresh
    ``last_progress_at``, or a worker accomplishing nothing looks — to every
    timestamp upstream — exactly like a worker shipping."""
    from tests.goal_fakes import (
        Clock, FakeClaude, FakeEngine, RecordingNotifier, seed_goal,
    )
    from tests.test_goal_tick import _store, _tick
    from devclaw.goal.models import GoalStatus, InFlight, PollResult

    STALE = "2026-06-01T00:00:00+00:00"

    async def _run(no_change: bool) -> "str | None":
        store = _store(tmp_path / ("nc" if no_change else "ch"), Clock())
        seed_goal(tmp_path / ("nc" if no_change else "ch"), "g")
        store.save_status("g", GoalStatus(
            phase="in_flight",
            last_progress_at=STALE,
            in_flight=InFlight("devclaw", "implement_feature", "t1", "task", "do X"),
        ))
        engine = FakeEngine(poll_result=PollResult(
            terminal=True, status="done", detail="d", gate_passed=True,
            no_change=no_change,
        ))
        await _tick(store, "g", FakeClaude(), engine, RecordingNotifier())
        return store.load_status("g").last_progress_at

    assert await _run(no_change=False) != STALE   # a real increment: progress
    assert await _run(no_change=True) == STALE    # an empty span: none
