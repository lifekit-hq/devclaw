"""The materialize gate — the span is a precondition, not a finding (spec 013).

Before this gate existed, a span that could not be determined arrived at every
diff-reading gate as ``""`` and passed all of them trivially: a gate shipping on
its own silence, which #186 forbids. The gate sits between ``verify`` and every
consumer of the change, is always-hard in both dial positions, and never runs
before verify — so a verify failure still costs zero git.
"""

from __future__ import annotations

import subprocess

import pytest

from devclaw import task_queue
from devclaw.engine import EngineRequest
from devclaw.quality.gate_pipeline import GateInput, run_pipeline
from devclaw.quality.gate_policy import ALWAYS_HARD, Consequence, gate_consequence
from devclaw.state_store import StateStore
from devclaw.task_change import ChangeSet, ERROR
from devclaw.task_queue import TaskQueue

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _gate(passed: bool = True):
    return {"ran": True, "cmd": "pytest", "passed": passed,
            "exit_code": 0 if passed else 1, "timed_out": False, "output": ""}


def _repo(tmp_path):
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


def _input(change_fn, *, verify=None) -> GateInput:
    return GateInput(
        kind="implement_feature", goal="g", workspace_dir="/ws",
        verify=verify if verify is not None else _gate(True),
        scaffold=False, browser_mode="flexible", change_fn=change_fn,
    )


async def test_an_undeterminable_span_fails_the_gate_closed_instead_of_reading_as_empty():
    async def _broken():
        return ChangeSet(status=ERROR, reason="git could not diff abc..def")

    verdict = await task_queue._MaterializeGate().check(_input(_broken))
    assert verdict.ok is False
    assert verdict.gate_id == "materialize"
    assert "could not be determined" in (verdict.reason or "")
    assert verdict.dialable is False


async def test_the_span_gate_is_always_hard_in_both_dial_positions():
    assert "materialize" in ALWAYS_HARD
    for dial in ("trust", "strict"):
        assert gate_consequence("materialize", dial) is Consequence.BLOCK


async def test_a_crash_capturing_the_span_also_fails_closed():
    async def _boom():
        raise RuntimeError("thread died")

    verdict = await task_queue._MaterializeGate().check(_input(_boom))
    assert verdict.ok is False and "thread died" in (verdict.reason or "")


async def test_a_verify_failure_short_circuits_before_the_span_is_captured():
    """The property gate_pipeline has always promised, preserved: the verify gate
    reads nothing, so a failing verify_cmd costs no git at all."""
    captured: list = []

    async def _capture():  # pragma: no cover — must never run
        captured.append(1)
        return ChangeSet(status=ERROR, reason="should not be reached")

    gi = _input(_capture, verify=_gate(False))
    verdict = await run_pipeline(
        gi, (task_queue._VerifyGate(), task_queue._MaterializeGate(),
             task_queue._IntegrityGate()),
    )
    assert verdict is not None and verdict.gate_id == "verify"
    assert captured == []


async def test_the_gate_chain_captures_the_span_exactly_once(store, tmp_path, monkeypatch):
    """SC-004 at the settle path: every gate below reads ONE object. Two
    consumers naming the same post-run sha cannot disagree."""
    monkeypatch.setattr(task_queue, "REVIEW_GATE_ENABLED", True)
    ws = _repo(tmp_path)
    calls: list = []
    real = task_queue._capture_change

    async def spy(workspace_dir, base, **kw):
        calls.append(base)
        return await real(workspace_dir, base, **kw)

    monkeypatch.setattr(task_queue, "_capture_change", spy)

    async def runner(req: EngineRequest):
        (ws / "f.py").write_text("F = 1\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate(True)}

    async def reviewer(*, goal, kind, diff, repo_context=None):
        return {"verdict": "approve", "summary": "ok", "issues": [], "blocking": []}

    q = TaskQueue(store, runner=runner, reviewer=reviewer)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest", strictness="strict")
    await q.drain()
    assert store.get_task(tid).status == "done"
    assert len(calls) == 1


async def test_a_task_whose_span_cannot_be_determined_never_settles_done(
    store, tmp_path, monkeypatch,
):
    """End to end: #186 for the span itself. The change is real, but git cannot
    answer — the task must not ship on that silence."""
    monkeypatch.setattr(task_queue, "TASK_MAX_RETRIES", 0)
    ws = _repo(tmp_path)

    async def _broken(host_dir, base="", head=""):
        return None

    monkeypatch.setattr(task_queue, "_git_diff", _broken)

    async def runner(req: EngineRequest):
        (ws / "f.py").write_text("F = 1\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate(True)}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest", deliver=True)
    await q.drain()

    row = store.get_task(tid)
    assert row.status == "failed"
    assert "could not be determined" in (row.error or "")
    assert row.pr_url is None


async def test_every_read_the_change_gate_sees_the_materialized_span(
    store, tmp_path, monkeypatch,
):
    """FR-003: the integrity scan, the adversarial review and the browser gate
    all read the ONE artifact — so a file the agent never recorded is in front
    of every one of them, not just the ones that thought to look."""
    monkeypatch.setattr(task_queue, "REVIEW_GATE_ENABLED", True)
    ws = _repo(tmp_path)
    seen: dict = {}

    async def reviewer(*, goal, kind, diff, repo_context=None):
        seen["review"] = diff
        return {"verdict": "approve", "summary": "ok", "issues": [], "blocking": []}

    integrity_seen: list = []
    real_integrity = task_queue._integrity_failure

    def spy_integrity(diff, workspace_dir=None):
        integrity_seen.append(diff)
        return real_integrity(diff, workspace_dir)

    monkeypatch.setattr(task_queue, "_integrity_failure", spy_integrity)

    async def runner(req: EngineRequest):
        (ws / "never_recorded.py").write_text("N = 1\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate(True)}

    q = TaskQueue(store, runner=runner, reviewer=reviewer)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="g",
                   verify_cmd="pytest", strictness="strict")
    await q.drain()

    assert store.get_task(tid).status == "done"
    assert integrity_seen and "never_recorded.py" in integrity_seen[0]
    assert "never_recorded.py" in seen["review"]
