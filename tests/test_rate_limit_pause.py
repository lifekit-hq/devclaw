"""The pause-and-resume brake (money): a usage limit, an expired login or a
provider outage PAUSES the account and requeues the session with its work
snapshotted — never fails-and-retries, never burns the remaining quota. A
real error is INTERRUPTED. Driven with stub runners (no docker)."""
from __future__ import annotations

import os
import subprocess

import pytest

from devclaw.engine import EngineRequest
from devclaw.loom import limits
from devclaw.queue import admission as queue_admission
from devclaw.queue import settle as queue_settle
from devclaw.state_store import EXIT_BLOCKED, EXIT_INTERRUPTED, StateStore, _now_ms
from devclaw.task_queue import TaskQueue


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


@pytest.mark.parametrize("error_text,expected_kind", [
    ("API Error: 429 Too Many Requests", "rate_limit"),
    ("session/prompt failed: Internal error: API Error: 529 Overloaded", "server_error"),
    ("API Error: 401 authentication_error: OAuth token has expired, please run /login", "auth"),
])
async def test_limit_pauses_and_requeues_never_fails(store, error_text, expected_kind):
    calls: list = []

    async def rl(req: EngineRequest):
        calls.append(req.goal)
        return {"status": "error", "error": error_text}

    q = TaskQueue(store, runner=rl)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "pending" and t.pause_count == 1   # requeued, NOT failed
    assert len(calls) == 1                                # NOT retried
    until, reason = store.global_pause()
    assert expected_kind in reason and until > _now_ms()


async def test_real_error_is_interrupted_not_paused(store):
    async def boom(req: EngineRequest):
        return {"status": "error", "error": "ModuleNotFoundError: No module named 'fastapi'"}

    q = TaskQueue(store, runner=boom)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed" and t.exit == EXIT_INTERRUPTED
    assert store.global_pause()[0] == 0


async def test_a_blocked_session_is_never_classified_as_a_limit(store):
    """Provenance before wording: a session's own words are REAL by origin.
    A BLOCKED reason that happens to say 'rate limit' must not pause the account."""
    async def blocked(req: EngineRequest):
        return {"status": "blocked", "reason": "the API rate limit config is undecided — which?",
                "block_kind": "contract", "block_item": "", "workspace_dir": req.workspace_dir,
                "agent_output": "BLOCKED: the API rate limit config is undecided — which?"}

    q = TaskQueue(store, runner=blocked)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "done" and t.exit == EXIT_BLOCKED
    assert store.global_pause()[0] == 0


async def test_a_red_verify_naming_a_limit_never_pauses(store, tmp_path):
    """A verify log is prose about the repository (a test file can be named
    test_rate_limit_pause.py); it never reaches the classifier."""
    repo = _git_repo(tmp_path)

    async def red(req: EngineRequest):
        return {"status": "ok", "workspaceDir": req.workspace_dir, "agent_output": "DELIVERED: x",
                "verify": {"ran": True, "cmd": "pytest", "passed": False, "exit_code": 1,
                           "timed_out": False, "output": "FAILED tests/test_rate_limit_pause.py::test_x — 429 Too Many Requests"}}

    q = TaskQueue(store, runner=red)
    tid = q.submit(kind="implement_feature", workspace_dir=str(repo), goal="g", verify_cmd="pytest")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed" and t.exit == EXIT_INTERRUPTED
    assert store.global_pause()[0] == 0


async def test_pump_holds_dispatch_while_paused(store):
    called: list = []

    async def ok(req: EngineRequest):
        called.append(1)
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=ok)
    store.set_global_pause(_now_ms() + 60_000, "manual")
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    assert store.get_task(tid).status == "pending"
    assert called == []


@pytest.mark.parametrize("error_text", [
    "rate limit exceeded",
    "session/prompt failed: Internal error: API Error: 529 Overloaded",
])
async def test_resumes_after_pause_expires(store, error_text):
    state = {"n": 0}

    async def rl_then_ok(req: EngineRequest):
        state["n"] += 1
        if state["n"] == 1:
            return {"status": "error", "error": error_text}
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=rl_then_ok)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    assert store.get_task(tid).status == "pending"
    store.set_global_pause(_now_ms() - 1000, "expired")
    q._pump()
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert state["n"] == 2
    assert store.global_pause()[0] == 0


async def test_stated_hint_survives_not_clobbered_to_max(store):
    async def rl(req: EngineRequest):
        return {"status": "error", "error": "usage limit — try again in 10 hours"}

    q = TaskQueue(store, runner=rl)
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    until, _ = store.global_pause()
    assert _now_ms() + 35_990_000 <= until <= _now_ms() + 36_010_000


async def test_stated_hint_caps_to_stated_max(store, monkeypatch):
    monkeypatch.setattr(limits, "RATE_LIMIT_STATED_MAX_S", 60)

    async def rl(req: EngineRequest):
        return {"status": "error", "error": "usage limit — try again in 10 hours"}

    q = TaskQueue(store, runner=rl)
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    assert store.global_pause()[0] <= _now_ms() + 60_000 + 2_000


async def test_unstated_default_pause(store):
    async def rl(req: EngineRequest):
        return {"status": "error", "error": "API Error: 429 Too Many Requests"}

    q = TaskQueue(store, runner=rl)
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    assert store.global_pause()[0] <= _now_ms() + limits.RATE_LIMIT_PAUSE_S * 1000 + 2_000


async def test_absolute_reset_time_reaches_the_pause(store, monkeypatch):
    from datetime import datetime, timezone

    class _FrozenDatetime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 7, 10, 18, 0, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(queue_settle, "datetime", _FrozenDatetime)

    async def rl(req: EngineRequest):
        return {"status": "error", "error": "Internal error: You're out of extra usage · resets 10pm (UTC)"}

    q = TaskQueue(store, runner=rl)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    assert store.get_task(tid).status == "pending"
    until, reason = store.global_pause()
    expect = (4 * 3600 + 120) * 1000
    assert _now_ms() + expect - 5_000 <= until <= _now_ms() + expect + 5_000
    assert "quota" in reason


async def test_pause_requeue_is_bounded(store, monkeypatch):
    monkeypatch.setattr(queue_settle, "MAX_PAUSE_REQUEUES", 2)
    monkeypatch.setattr(queue_admission, "WORKSPACE_BREAK_THRESHOLD", 1)

    async def rl(req: EngineRequest):
        return {"status": "error", "error": "API Error: 429 Too Many Requests"}

    q = TaskQueue(store, runner=rl)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    await q.drain()
    assert store.get_task(tid).pause_count == 1
    for expected in (2,):
        store.set_global_pause(_now_ms() - 1000, "expired")
        q._pump()
        await q.drain()
        assert store.get_task(tid).pause_count == expected
    store.set_global_pause(_now_ms() - 1000, "expired")
    q._pump()
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "failed" and t.exit == EXIT_INTERRUPTED
    assert "exceeded 2 usage-limit pauses" in t.error and "429" in t.error
    assert store.global_pause()[0] > _now_ms()
    assert store.get_workspace_break("/ws")[0] > _now_ms()


async def test_crash_recovery_requeue_does_not_count_as_pause(store):
    store.create_task(id="t1", kind="implement_feature", workspace_dir="/ws", goal="g")
    store.claim_pending("t1")
    store.reset_running_to_pending()
    assert store.get_task("t1").pause_count == 0


# ---- WIP preserved across a pause ---------------------------------------------

def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _git_out(repo, *args) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


async def test_pause_snapshots_dirty_tree_as_wip_commit(store, tmp_path):
    repo = _git_repo(tmp_path)

    async def rl(req: EngineRequest):
        with open(os.path.join(req.workspace_dir, "half_done.py"), "w") as fh:
            fh.write("partial = True\n")
        return {"status": "error", "error": "API Error: 429 Too Many Requests"}

    q = TaskQueue(store, runner=rl)
    tid = q.submit(kind="implement_feature", workspace_dir=str(repo), goal="g")
    await q.drain()
    assert store.get_task(tid).status == "pending"
    head = _git_out(repo, "log", "--oneline", "-1")
    assert "wip(devclaw): interrupted" in head and tid[:8] in head
    assert _git_out(repo, "status", "--porcelain").strip() == ""


async def test_rerun_after_pause_gets_interruption_brief(store):
    goals: list = []

    async def rl_then_ok(req: EngineRequest):
        goals.append(req.goal)
        if len(goals) == 1:
            return {"status": "error", "error": "rate limit exceeded"}
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=rl_then_ok)
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="build the thing")
    await q.drain()
    store.set_global_pause(_now_ms() - 1000, "expired")
    q._pump()
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert goals[0] == "build the thing"
    assert "[Resuming after an interruption]" in goals[1]
    assert "build the thing" in goals[1]


async def test_pause_with_non_git_workspace_still_requeues(store, tmp_path):
    ws = tmp_path / "plain"
    ws.mkdir()

    async def rl(req: EngineRequest):
        return {"status": "error", "error": "API Error: 429 Too Many Requests"}

    q = TaskQueue(store, runner=rl)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "pending" and t.pause_count == 1
    assert not (ws / ".git").exists()


async def test_snapshot_crash_never_blocks_the_pause(store, tmp_path, monkeypatch):
    def boom(host_dir, message):
        raise RuntimeError("git exploded")

    async def boom_async(host_dir, message):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(queue_settle, "_wip_commit_sync", boom)

    async def rl(req: EngineRequest):
        return {"status": "error", "error": "API Error: 429 Too Many Requests"}

    q = TaskQueue(store, runner=rl)
    tid = q.submit(kind="implement_feature", workspace_dir=str(tmp_path), goal="g")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "pending" and t.pause_count == 1
    assert store.global_pause()[0] > _now_ms()


async def test_resumed_task_gate_baseline_is_original_base_not_wip_snapshot(store, tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    base_sha = _git_out(repo, "rev-parse", "HEAD").strip()
    diff_bases: list = []

    async def recording_diff(host_dir, base="", head="", paths=None):
        diff_bases.append(base)
        return ""

    monkeypatch.setattr(queue_settle, "_git_diff", recording_diff)
    runs: list = []

    async def rl_then_ok(req: EngineRequest):
        runs.append(req.goal)
        if len(runs) == 1:
            with open(os.path.join(req.workspace_dir, "half_done.py"), "w") as fh:
                fh.write("partial = True\n")
            return {"status": "error", "error": "API Error: 429 Too Many Requests"}
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "verify": {"ran": True, "cmd": "true", "passed": True, "exit_code": 0,
                           "timed_out": False, "output": ""}}

    q = TaskQueue(store, runner=rl_then_ok)
    tid = q.submit(kind="implement_feature", workspace_dir=str(repo), goal="g", verify_cmd="true")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "pending" and t.pre_run_sha == base_sha
    assert _git_out(repo, "rev-parse", "HEAD").strip() != base_sha
    store.set_global_pause(_now_ms() - 1000, "expired")
    q._pump()
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert diff_bases and all(b == base_sha for b in diff_bases)


async def test_stale_persisted_baseline_degrades_to_fresh_capture(store, tmp_path):
    repo = _git_repo(tmp_path)
    head_sha = _git_out(repo, "rev-parse", "HEAD").strip()

    async def ok(req: EngineRequest):
        return {"status": "ok", "workspaceDir": req.workspace_dir,
                "verify": {"ran": True, "cmd": "true", "passed": True, "exit_code": 0,
                           "timed_out": False, "output": ""}}

    q = TaskQueue(store, runner=ok)
    tid = q.submit(kind="implement_feature", workspace_dir=str(repo), goal="g", verify_cmd="true")
    store.set_task_pre_run_sha(tid, "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef")
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "done" and t.pre_run_sha == head_sha


# ---- hold legibility at the pump gate ------------------------------------------

def _closed_window_now() -> tuple[str, str]:
    from datetime import datetime, timedelta, timezone
    now = datetime.now(tz=timezone.utc)
    return (now + timedelta(hours=3)).strftime("%H:%M"), (now + timedelta(hours=4)).strftime("%H:%M")


async def test_pause_expiring_into_closed_window_logs_held_not_resuming(store, capsys):
    async def ok(req: EngineRequest):
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=ok)
    start, end = _closed_window_now()
    store.set_run_schedule(True, start, end, "UTC")
    tid = q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    store.set_global_pause(_now_ms() - 1000, "quota: out of extra usage - resets 6am (UTC)")
    q._pump()
    await q.drain()
    err = capsys.readouterr().err
    assert "resuming" not in err
    assert "quota pause expired" in err and "dispatch held" in err
    assert store.global_pause()[0] == 0
    assert store.get_task(tid).status == "pending"


async def test_window_hold_logs_once_not_every_tick(store, capsys):
    async def ok(req: EngineRequest):
        return {"status": "ok", "workspaceDir": req.workspace_dir}

    q = TaskQueue(store, runner=ok)
    start, end = _closed_window_now()
    store.set_run_schedule(True, start, end, "UTC")
    q.submit(kind="implement_feature", workspace_dir="/ws", goal="g")
    q._pump()
    q._pump()
    q._pump()
    await q.drain()
    assert capsys.readouterr().err.count("dispatch held") == 1
