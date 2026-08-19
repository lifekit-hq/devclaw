"""Retry-on-fail — the third leg of verify + RETRY + human.

A task that fails its verify gate (or errors) is re-run, each time with the
failure fed back into the goal, up to DEVCLAW_MAX_RETRIES, then escalated.
Timeouts are NOT retried. Driven with stub runners (no docker).
"""

import asyncio

import pytest

from devclaw import task_queue
from devclaw.engine import EngineRequest
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _gate(passed: bool, output: str = ""):
    return {"ran": True, "cmd": "pytest", "passed": passed,
            "exit_code": 0 if passed else 1, "timed_out": False, "output": output}


def _flaky_runner(fail_times: int, calls: list):
    """Agent-ok every time, but the gate fails the first `fail_times` runs."""
    async def runner(req: EngineRequest):
        calls.append(req.goal)
        gate = _gate(passed=len(calls) > fail_times, output="boom-detail")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": gate}
    return runner


async def test_retry_then_success_feeds_failure_back(store, monkeypatch):
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=1, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert len(calls) == 2  # first failed the gate, retried, second passed
    # the retry goal carried the failure context forward
    assert calls[0] == "do X"
    assert "[Automatic retry 1/1]" in calls[1] and "boom-detail" in calls[1] and "do X" in calls[1]


async def test_retry_prompt_accumulates_all_prior_failures(store, monkeypatch):
    # The retry prompt used to carry ONLY the most-recent failure (overwritten
    # string), so attempt 3 never learned what attempt 1 tried and could repeat
    # a mistake already fed back once. Now every prior failure rides along,
    # numbered, so the agent can rule out whole approaches.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 2)
    calls: list = []

    async def runner(req: EngineRequest):
        calls.append(req.goal)
        # distinguishable failure per attempt
        gate = _gate(passed=False, output=f"boom-{len(calls)}")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": gate}

    q = TaskQueue(store, runner=runner)
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    assert len(calls) == 3
    # attempt 2 knows failure 1 — and cannot know a failure that hasn't happened
    assert "Attempt 1:" in calls[1] and "boom-1" in calls[1]
    assert "boom-2" not in calls[1]
    # attempt 3 carries BOTH prior failures, numbered, in order
    assert "Attempt 1:" in calls[2] and "boom-1" in calls[2]
    assert "Attempt 2:" in calls[2] and "boom-2" in calls[2]
    assert calls[2].index("boom-1") < calls[2].index("boom-2")
    # and the original goal still rides along
    assert "do X" in calls[2]


async def test_retries_exhausted_then_failed(store, monkeypatch):
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=99, calls=calls))  # never passes
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"
    assert len(calls) == 2  # 1 attempt + 1 retry
    assert "failed after 2 attempts" in t.error and "boom-detail" in t.error


async def test_no_retry_when_disabled(store, monkeypatch):
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 0)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=99, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "failed"
    assert len(calls) == 1  # no retry


async def test_success_first_try_runs_once(store, monkeypatch):
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=0, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert len(calls) == 1  # no needless retry on success


def _reset_recorder(monkeypatch, base_sha: str = "basesha0"):
    """Fake a captured pre-run base + record every retry-reset call.

    Real ``/ws`` isn't a git repo, so without this the base capture returns ''
    and the reset is (correctly) skipped — these fakes let us assert the reset
    behavior directly."""
    resets: list = []

    async def fake_head(host_dir):
        return base_sha

    async def fake_reset(host_dir, sha):
        resets.append((host_dir, sha))
        return True

    monkeypatch.setattr(task_queue, "_git_head", fake_head)
    monkeypatch.setattr(task_queue, "_git_reset_clean", fake_reset)
    return resets


async def test_retry_resets_workspace_to_clean_per_item_base(store, monkeypatch):
    # #1 retry isolation: each retry rewinds the workspace to the pre-run base
    # BEFORE re-running, so a failed attempt's drift can't compound into the
    # next (the closeloop-bench 2026-07-18 "each retry inherits more drift"
    # pattern). The reset fires on the retry and ONLY the retry.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    resets = _reset_recorder(monkeypatch)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=1, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert len(calls) == 2  # failed once, retried, passed
    assert resets == [("/ws", "basesha0")]  # reset to the captured base, once


async def test_first_attempt_never_resets(store, monkeypatch):
    # A clean first-try success must not rewind anything — no needless reset.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    resets = _reset_recorder(monkeypatch)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=0, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert resets == []


async def test_retry_reset_skipped_when_base_capture_missed(store, monkeypatch):
    # Best-effort: if the pre-run base capture returned '' (a git hiccup),
    # the retry still runs but attempts no reset (nothing to rewind to) —
    # degrade on the drifted tree, never wedge the retry.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    resets = _reset_recorder(monkeypatch, base_sha="")
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=1, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert len(calls) == 2  # retry still happened
    assert resets == []  # but no reset attempted with an empty base


async def test_worker_blocked_status_is_not_retried_and_surfaces_reason(store, monkeypatch):
    # A worker honest-block (result status="blocked") fails CLOSED and FAST: the
    # task is failed (never "done" — invariant #186), not retried (a re-run
    # reproduces the same block), and the reason rides the failure so the goal
    # layer can surface it to the owner.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 3)  # retries available, must not be used
    calls: list = []

    async def blocked_runner(req: EngineRequest):
        calls.append(req.goal)
        return {"status": "blocked",
                "reason": "the task needs a paid API key not present in the repo"}

    q = TaskQueue(store, runner=blocked_runner)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"  # never "done" — a block is not an approval
    assert len(calls) == 1  # not auto-retried despite retries being available
    assert "worker reported BLOCKED:" in t.error
    assert "the task needs a paid API key not present in the repo" in t.error
    assert "Needs a human" in t.error


async def test_prompt_too_long_fails_fast_without_retry(store, monkeypatch):
    # Context overflow ("Conversation run failed for id=...: Internal error:
    # Prompt is too long") is DETERMINISTIC for a task's scope — and the retry
    # prompt appends the failure history, so a re-run is strictly larger and
    # overflows again ("(failed after 2 attempts)" was pure quota burn). Fail
    # FAST (exactly one engine invocation) + CLOSED (failed — never done,
    # never paused) with actionable smaller-scope guidance.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 3)  # retries available, must not be used
    calls: list = []

    async def overflowing_runner(req: EngineRequest):
        calls.append(req.goal)
        return {"status": "error",
                "error": ("Conversation run failed for id=abc123: "
                          "Internal error: Prompt is too long")}

    q = TaskQueue(store, runner=overflowing_runner)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"  # fail closed — an overflow is never an approval
    assert len(calls) == 1  # no second engine invocation — the retry is futile
    assert "Prompt is too long" in t.error
    assert "Not auto-retried" in t.error and "smaller scope" in t.error
    # never a pause: a context overflow is a REAL failure, not a usage limit
    until_ms, _reason = store.global_pause()
    assert until_ms == 0


async def test_quota_error_mentioning_prompt_too_long_still_pauses(store, monkeypatch):
    # LOAD-BEARING ORDERING: classify_failure runs BEFORE the prompt-too-long
    # marker check, so a quota-shaped error that ALSO carries the marker text
    # must take the pause-and-resume path (requeued, quota preserved), never
    # the terminal no-retry fail. Pins the ordering the settle-path comment
    # promises — a comment alone is not a regression guard.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    calls: list = []

    async def quota_with_marker(req: EngineRequest):
        calls.append(req.goal)
        return {"status": "error",
                "error": ("Conversation run failed for id=abc123: Internal "
                          "error: You're out of extra usage · resets 3:30am "
                          "(UTC) — Internal error: Prompt is too long")}

    q = TaskQueue(store, runner=quota_with_marker)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "pending"  # requeued for the pause window, NOT failed
    assert len(calls) == 1
    until_ms, _reason = store.global_pause()
    assert until_ms > 0  # the pause engaged — quota routing was not shadowed


async def test_timeout_is_not_retried(store, monkeypatch):
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    monkeypatch.setattr(task_queue, "TASK_TIMEOUT_S", 0.2)
    calls: list = []

    async def slow(req: EngineRequest):
        calls.append(req.goal)
        await asyncio.sleep(5)  # >> the 0.2s cap
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=slow)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed" and "wall-clock timeout" in t.error
    assert len(calls) == 1  # a stuck run is escalated, not retried


async def test_retry_prompt_tells_worker_to_reproduce_before_diagnosing(store, monkeypatch):
    # Night 2026-08-18 (#564): the retry preamble said only "diagnose and fix",
    # so a pre-existing FLAKY test failure sent the worker on a 32-minute hunt
    # for a phantom bug in its own change — burning the conversation context
    # until it overflowed. The retry prompt now instructs: re-run the failing
    # command FIRST; a non-reproducing failure is flakiness to fix (or re-run
    # verify), not a defect in the change.
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 1)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=1, calls=calls))
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    assert len(calls) == 2
    # first attempt carries no reproduce-first instruction (nothing failed yet)
    assert "re-run the failing command" not in calls[0]
    retry = calls[1]
    # reproduce-first comes BEFORE diagnose — order is the instruction
    assert "First re-run the failing command" in retry
    assert "flaky" in retry
    assert retry.index("re-run the failing command") < retry.index("diagnose the cause")
