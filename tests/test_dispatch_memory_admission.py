"""Host-memory admission — the `claude --print` -9 OOM cure.

Named regression: dispatch used to admit sandbox launches purely by COUNT
(GLOBAL_MAX_CONCURRENT) with only a per-container `--memory` CEILING, never
reconciling against real host RAM. On a small box that overcommits, and the
kernel's global OOM-killer reaps the fattest unbounded process — the host-side
`claude --print` cognition (`exited -9`); #448/#449 only retried that symptom.
Dispatch now gates each launch on `/proc/meminfo` MemAvailable and DEFERS when
the box can't fit another sandbox, and fails OPEN when memory is unmeasurable so
an unusual host is never wedged. The /proc read sits AFTER the cheap-idle guard.
"""
from __future__ import annotations

import pytest

from devclaw import task_queue
from devclaw.state_store import StateStore
from devclaw.task_queue import MEM_LAUNCH_FLOOR_BYTES, SANDBOX_MEMORY_BYTES, TaskQueue


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _recording_runner(calls: list):
    async def runner(req):
        calls.append(req.goal)
        return {"status": "ok", "workspaceDir": req.workspace_dir}
    return runner


def _gate_queue(store, calls: list) -> TaskQueue:
    """A queue whose launches we observe via a stub runner, but forced into the
    'sandcastle' engine kind so the memory gate (which only guards real docker
    sandboxes) is actually exercised. Off the sandbox path the gate is inert."""
    q = TaskQueue(store, runner=_recording_runner(calls))
    q._engine_kind = "sandcastle"
    return q


def _submit_n(q: TaskQueue, n: int) -> None:
    # pump=False creates the rows only, so ONE _pump() reconciles all n together
    # — the batch path the goal heartbeat uses, where the within-pump memory debit
    # applies. (submit(pump=True) launches one-per-pump, a different path.)
    for i in range(n):  # distinct workspaces so nothing serializes but memory
        q.submit(kind="implement_feature", workspace_dir=f"/ws{i}",
                 goal=f"goal-{i}", pump=False)


async def test_dispatch_defers_all_launches_when_memory_below_floor(store, monkeypatch):
    monkeypatch.setattr(task_queue, "host_mem_available_bytes",
                        lambda: MEM_LAUNCH_FLOOR_BYTES - 1)
    calls: list = []
    q = _gate_queue(store, calls)
    _submit_n(q, 2)

    q._pump()  # one reconcile under memory pressure
    assert store.count_running() == 0   # both launches deferred, not overcommitted
    assert calls == []                  # nothing dispatched

    # Memory recovers → held work flows on the next tick (auto-resume, no retry).
    monkeypatch.setattr(task_queue, "host_mem_available_bytes", lambda: 64 << 30)
    q._pump()
    await q.drain()
    assert sorted(calls) == ["goal-0", "goal-1"]


async def test_dispatch_launches_only_what_fits_in_ram(store, monkeypatch):
    # Exactly the floor → room for exactly one sandbox; the debit drops the budget
    # to the reserve, which is below the floor, so the 2nd and 3rd defer.
    monkeypatch.setattr(task_queue, "host_mem_available_bytes",
                        lambda: MEM_LAUNCH_FLOOR_BYTES)
    calls: list = []
    q = _gate_queue(store, calls)
    _submit_n(q, 3)

    q._pump()
    assert store.count_running() == 1

    monkeypatch.setattr(task_queue, "host_mem_available_bytes", lambda: 64 << 30)
    await q.drain()
    assert len(calls) == 3  # the rest flow once RAM is ample


async def test_two_fit_when_headroom_is_floor_plus_one_sandbox(store, monkeypatch):
    monkeypatch.setattr(task_queue, "host_mem_available_bytes",
                        lambda: MEM_LAUNCH_FLOOR_BYTES + SANDBOX_MEMORY_BYTES)
    calls: list = []
    q = _gate_queue(store, calls)
    _submit_n(q, 3)

    q._pump()
    assert store.count_running() == 2  # fits two, third defers

    monkeypatch.setattr(task_queue, "host_mem_available_bytes", lambda: 64 << 30)
    await q.drain()


async def test_dispatch_fails_open_when_memory_unreadable(store, monkeypatch):
    # None ⇒ unmeasurable host ⇒ behave exactly as before, never wedge the queue.
    monkeypatch.setattr(task_queue, "host_mem_available_bytes", lambda: None)
    calls: list = []
    q = _gate_queue(store, calls)
    _submit_n(q, 2)
    q._pump()  # None ⇒ fail open ⇒ both launch as today
    await q.drain()
    assert sorted(calls) == ["goal-0", "goal-1"]


def test_idle_tick_never_reads_host_memory(store, monkeypatch):
    # The /proc read must sit AFTER the cheap-idle guard — an idle pump (no work)
    # must not touch memory, same spirit as the zero-token idle invariant.
    reads = {"n": 0}

    def _counting():
        reads["n"] += 1
        return 64 << 30

    monkeypatch.setattr(task_queue, "host_mem_available_bytes", _counting)
    q = _gate_queue(store, [])  # sandcastle mode → the read WOULD fire if not idle-guarded
    q._pump()  # nothing submitted
    assert reads["n"] == 0


def test_parse_mem_units_and_fail_safe_default():
    assert task_queue._parse_mem("2g") == 2 << 30
    assert task_queue._parse_mem("512m") == 512 << 20
    assert task_queue._parse_mem("2048k") == 2048 << 10
    assert task_queue._parse_mem("1073741824") == 1 << 30
    assert task_queue._parse_mem("garbage") == 2 << 30  # unparseable → 2 GiB, not a crash


def test_host_mem_available_is_positive_int_or_none():
    # Contract is only "a positive byte count, or None (the fail-open path)".
    v = task_queue.host_mem_available_bytes()
    assert v is None or (isinstance(v, int) and v > 0)
