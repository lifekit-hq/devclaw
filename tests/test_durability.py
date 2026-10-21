"""Durability + recovery tests — crash recovery, global cap, cheap-idle, heartbeat."""

import asyncio

import pytest

from devclaw.engine import EngineEvent, EngineRequest
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _ok_runner(seen: list[str]):
    async def runner(req: EngineRequest):
        seen.append(req.goal)
        return {"status": "ok", "workspaceDir": req.workspace_dir, "message": "done"}
    return runner


# ---- lifted on_event bound method (#407 PR-1) ----


async def test_on_event_lifted_still_tags_task_id(store):
    """The per-attempt event sink is the bound method ``_append_task_event``
    pre-bound via ``functools.partial(self._append_task_event, task_id)``
    (lifted from an inline closure, #407 PR-1; the program_id it also carried
    died with the 022 lane demolition). An engine event streamed during the
    run must land on the append-only log carrying the correct ``task_id``."""
    store.create_task(
        id="t1", kind="implement_feature", workspace_dir="/ws", goal="g",
    )
    store.claim_pending("t1")  # running — the state _execute settles from

    async def emitting_runner(req: EngineRequest):
        # The engine streams one observation back through the on_event sink,
        # exercising the partial-bound _append_task_event.
        req.on_event(EngineEvent(id=None, type="log", source="agent", ts=1, payload={"m": "hi"}))
        return {"status": "ok", "workspaceDir": req.workspace_dir, "message": "done"}

    q = TaskQueue(store, runner=emitting_runner)
    await q._execute("t1", "implement_feature", "/ws", "g")

    logged = [e for e in store.list_events(task_id="t1") if e.type == "log"]
    assert logged, "the streamed engine event was not persisted"
    assert logged[0].task_id == "t1"


# ---- crash recovery ----


async def test_recover_resets_orphaned_running_and_resumes(store):
    # Simulate a crash: a task is left 'running' with no live execution behind it.
    store.create_task(id="t1", kind="implement_feature", workspace_dir="/ws", goal="g")
    store.claim_pending("t1")
    assert store.get_task("t1").status == "running"

    # A fresh process (new TaskQueue) recovers it.
    q = TaskQueue(store, runner=_ok_runner([]))
    n = q.recover()
    assert n == 1
    assert store.get_task("t1").status == "pending"
    # the reap is in the audit log
    assert any(e.type == "reaped" for e in store.list_events(task_id="t1"))

    # …and a pump resumes it to completion.
    q._pump()
    await q.drain()
    assert store.get_task("t1").status == "done"


async def test_recover_noop_when_nothing_orphaned(store):
    store.create_task(id="t1", kind="implement_feature", workspace_dir="/ws", goal="g")
    store.mark_done("t1", "{}")  # terminal — not orphaned
    q = TaskQueue(store)
    assert q.recover() == 0
    assert store.get_task("t1").status == "done"


# ---- cheap-idle guard ----


def test_has_active_work_false_when_empty(store):
    assert store.has_active_work() is False


def test_idle_pump_is_a_noop(store):
    q = TaskQueue(store)
    q._pump()  # must not raise, must not error on an empty store


def test_has_active_work_true_with_pending(store):
    store.create_task(id="t1", kind="fix_bug", workspace_dir="/ws", goal="g")
    assert store.has_active_work() is True


# ---- global concurrency cap / backpressure ----


async def test_global_cap_limits_concurrent_runs(store, monkeypatch):
    monkeypatch.setattr("devclaw.task_queue.GLOBAL_MAX_CONCURRENT", 1)
    gate = asyncio.Event()
    seen: list[str] = []

    async def gated(req: EngineRequest):
        seen.append(req.goal)
        await gate.wait()
        return {"status": "ok", "workspaceDir": req.workspace_dir, "message": "done"}

    q = TaskQueue(store, runner=gated)
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="a")
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="b")

    # cap=1 → exactly one task may be running; the other is held pending (backpressure)
    assert store.count_running() == 1
    assert len(store.list_pending()) == 1

    gate.set()
    await q.drain()
    assert store.count_running() == 0
    assert seen == ["a", "b"]  # the second ran only after the first freed the slot


# ---- heartbeat ----


async def test_heartbeat_resumes_recovered_work(store):
    store.create_task(id="t1", kind="implement_feature", workspace_dir="/ws", goal="g")
    store.claim_pending("t1")  # orphaned running
    q = TaskQueue(store, runner=_ok_runner([]))
    q.recover()  # → pending
    q.start_ticking()  # first tick pumps immediately
    try:
        for _ in range(200):
            await asyncio.sleep(0.005)
            if store.get_task("t1").status == "done":
                break
    finally:
        await q.stop_ticking()
    assert store.get_task("t1").status == "done"
