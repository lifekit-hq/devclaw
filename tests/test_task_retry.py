"""Retry-on-fail — the third leg of verify + RETRY + human.

A task that fails its verify gate (or errors) is re-run, each time with the
failure fed back into the goal, up to DEVCLAW_MAX_RETRIES, then escalated.
Timeouts are NOT retried. Driven with stub runners (no docker).
"""

import asyncio

import pytest

from devclaw.queue import settle as queue_settle
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
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
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
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 2)
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
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=99, calls=calls))  # never passes
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"
    assert len(calls) == 2  # 1 attempt + 1 retry
    assert "failed after 2 attempts" in t.error and "boom-detail" in t.error


async def test_no_retry_when_disabled(store, monkeypatch):
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 0)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=99, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "failed"
    assert len(calls) == 1  # no retry


async def test_success_first_try_runs_once(store, monkeypatch):
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
    calls: list = []
    q = TaskQueue(store, runner=_flaky_runner(fail_times=0, calls=calls))
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g", verify_cmd="pytest")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert len(calls) == 1  # no needless retry on success


# ---- a retry keeps the workspace (spec 013 FR-012/FR-013) ------------------
#
# The loop used to rewind to ``pre_run_sha`` and ``clean -fdx`` before each
# retry, so the gates would diff a clean base. That was a compensation for the
# gates guessing what state the agent had left the tree in; they no longer
# guess. What the rewind cost was the work the agent got mostly right — it
# turned "fix your own output" into "rewrite from scratch" on every attempt.


def _repo(tmp_path):
    import subprocess

    d = tmp_path / "ws"
    d.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", str(d), *args], check=True, capture_output=True)
    (d / "README.md").write_text("# base\n")
    subprocess.run(["git", "-C", str(d), "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(d), "commit", "-q", "-m", "base"],
                   check=True, capture_output=True)
    return d


async def test_a_retry_keeps_the_workspace_and_rejudges_the_whole_span_against_the_pinned_base(
    store, monkeypatch, tmp_path
):
    """FR-012 + FR-013 together. Attempt 1 leaves a partial file and fails its
    gate; attempt 2 must still SEE that file (the tree is not rewound) and add
    to it. The span every gate judged on attempt 2 is the FULL change measured
    against the ORIGINAL pre-run base — not a delta against the rejected
    attempt, which is how gate-rejected content would otherwise reach a PR
    unjudged."""
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
    ws = _repo(tmp_path)
    saw: list = []
    judged: list = []

    async def runner(req: EngineRequest):
        first = (ws / "first.py").exists()
        saw.append(first)
        if not first:
            (ws / "first.py").write_text("A = 1\n")
            return {"status": "ok", "workspaceDir": req.workspace_dir,
                    "verify": _gate(False, "boom")}
        (ws / "second.py").write_text("B = 2\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "verify": _gate(True)}

    real_capture = queue_settle._capture_change

    async def spy(workspace_dir, base, **kw):
        change = await real_capture(workspace_dir, base, **kw)
        judged.append(change)
        return change

    monkeypatch.setattr(queue_settle, "_capture_change", spy)

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="do X",
                   verify_cmd="pytest")
    await q.drain()

    assert store.get_task(tid).status == "done"
    assert saw == [False, True]  # attempt 2 inherited attempt 1's output
    # the verify gate short-circuits attempt 1 before the span is captured, so
    # exactly one span exists — and it is the WHOLE change against the pinned base
    final = judged[-1]
    assert "first.py" in final.diff and "second.py" in final.diff
    assert final.base_sha == store.get_task(tid).pre_run_sha


async def test_the_pre_run_reference_stays_pinned_across_retries(store, monkeypatch, tmp_path):
    """FR-012's second half: promoting a rejected attempt to the new base would
    let gate-REJECTED content reach a PR without ever being re-judged —
    reintroducing this spec's own bug class."""
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
    ws = _repo(tmp_path)
    import subprocess

    base_before = subprocess.run(
        ["git", "-C", str(ws), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    calls: list = []

    async def runner(req: EngineRequest):
        calls.append(req.goal)
        (ws / f"f{len(calls)}.py").write_text("X = 1\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "verify": _gate(passed=len(calls) > 1)}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest")
    await q.drain()

    assert store.get_task(tid).status == "done"
    assert store.get_task(tid).pre_run_sha == base_before



async def test_worker_blocked_status_is_not_retried_and_surfaces_reason(store, monkeypatch):
    # A worker honest-block (result status="blocked") fails CLOSED and FAST: the
    # task is failed (never "done" — invariant #186), not retried (a re-run
    # reproduces the same block), and the reason rides the failure so the goal
    # layer can surface it to the owner.
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 3)  # retries available, must not be used
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
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 3)  # retries available, must not be used
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
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
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
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
    monkeypatch.setattr(queue_settle, "TASK_TIMEOUT_S", 0.2)
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
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
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


async def test_sandbox_oom_fails_fast_without_retry_and_names_the_cap(store, monkeypatch):
    # Spec 020 US1: the runner-stamped "sandbox OOM-killed" marker is kernel
    # evidence that the container memory cap killed the agent — deterministic
    # for this environment, so an identical retry only reproduces the kill
    # (the 2026-08-26 incident burned two dispatches this way). Fail FAST
    # (one engine invocation) + CLOSED (failed, never paused) with the cap
    # and BOTH remedies in the reason.
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 3)  # available, must not be used
    calls: list = []

    async def oom_runner(req: EngineRequest):
        calls.append(req.goal)
        return {"status": "error",
                "error": ("sandbox OOM-killed (cap=2g, oom_kill=1): "
                          "session/prompt failed: Internal error: The Claude "
                          "Agent process exited unexpectedly.")}

    q = TaskQueue(store, runner=oom_runner)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"  # fail closed — an OOM kill is never an approval
    assert len(calls) == 1  # no second engine invocation — same cgroup, same kill
    assert "sandbox OOM-killed (cap=2g" in t.error
    assert "Not auto-retried" in t.error
    assert "DEVCLAW_SANDBOX_MEMORY" in t.error and "bound the verify workload" in t.error
    until_ms, _reason = store.global_pause()
    assert until_ms == 0  # a memory cap is a REAL failure, never a usage pause


async def test_quota_error_mentioning_sandbox_oom_still_pauses(store, monkeypatch):
    # Same ordering shield as the prompt-too-long class: classify_failure runs
    # BEFORE the OOM marker check, so a quota-shaped error that also carries
    # the marker takes the pause-and-resume path, never the terminal fail.
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 1)
    calls: list = []

    async def quota_with_oom_marker(req: EngineRequest):
        calls.append(req.goal)
        return {"status": "error",
                "error": ("sandbox OOM-killed (cap=2g, oom_kill=1): Internal "
                          "error: You're out of extra usage · resets 3:30am "
                          "(UTC)")}

    q = TaskQueue(store, runner=quota_with_oom_marker)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "pending"  # requeued for the pause window, NOT failed
    assert len(calls) == 1
    until_ms, _reason = store.global_pause()
    assert until_ms > 0


async def test_oversized_chunk_demands_reslice_and_refuses_identical_retry(store, monkeypatch):
    # Spec 021 FR-008: when the runner's slice watcher named the active slice
    # in the overflow error, the terminal failure carries the re-slice demand
    # (the goal layer's next brief turns it into "re-slice THAT slice first")
    # — still exactly one engine invocation, still failed, never paused.
    monkeypatch.setattr(queue_settle, "TASK_MAX_RETRIES", 3)
    calls: list = []

    async def overflowing_runner(req: EngineRequest):
        calls.append(req.goal)
        return {"status": "error",
                "error": ("Conversation run failed for id=abc123: "
                          "Internal error: Prompt is too long "
                          "[active_slice: 001-f US2]")}

    q = TaskQueue(store, runner=overflowing_runner)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="do X", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed"
    assert len(calls) == 1
    assert "[active_slice: 001-f US2]" in t.error
    assert "re-slice IT" in t.error
