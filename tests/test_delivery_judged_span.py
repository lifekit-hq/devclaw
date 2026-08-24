"""What ships is what was judged (spec 013 US2 / FR-005).

P1 made one of the two computations complete. P2 removes the second: delivery
publishes the object the gates judged rather than re-deriving its own view of
the change. The property is checked by IDENTITY (the same sha), not by two
computations happening to agree — that agreement is what quietly stopped holding
on 2026-08-22 (#630).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devclaw.queue import settle as queue_settle
from devclaw.delivery import deliver_change, delivery_failed
from devclaw.engine import EngineRequest
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


def _git(d, *args):
    return subprocess.run(["git", "-C", str(d), *args],
                          capture_output=True, text=True, check=True).stdout.strip()


def _repo(tmp_path, name="ws"):
    d = tmp_path / name
    d.mkdir()
    for args in (["init", "-q", "-b", "main"],
                 ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        _git(d, *args)
    (d / "README.md").write_text("# base\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-q", "-m", "base")
    return d


def _gate():
    return {"ran": True, "cmd": "pytest", "passed": True, "exit_code": 0,
            "timed_out": False, "output": ""}


async def test_delivery_publishes_the_judged_head_without_rediscovering_the_change(
    store, tmp_path, monkeypatch,
):
    """The identity check: the sha delivery was handed is the sha it published,
    and it is the sha the gates judged."""
    ws = _repo(tmp_path)
    seen: dict = {}
    real = queue_settle.deliver_change

    async def spy(**kwargs):
        seen.update(kwargs)
        return await real(**kwargs)

    monkeypatch.setattr(queue_settle, "deliver_change", spy)

    async def runner(req: EngineRequest):
        (ws / "never_recorded.py").write_text("N = 1\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate()}

    q = TaskQueue(store, runner=runner)
    tid = q.submit(kind="implement_feature", workspace_dir=str(ws), goal="add a thing",
                   verify_cmd="pytest", deliver=True)
    await q.drain()

    row = store.get_task(tid)
    assert row.status == "done"
    judged = json.loads(row.result_json)["change"]["head_sha"]
    assert seen["judged_head"] == judged
    assert _git(ws, "rev-parse", "HEAD") == judged
    # and the published tree carries the file the agent never recorded
    assert "never_recorded.py" in _git(ws, "show", "--name-only", "--format=", judged)


async def test_delivery_fails_loud_when_the_workspace_drifted_from_the_judged_span(
    tmp_path,
):
    """A drifted workspace means what would ship is not what passed. That is a
    delivery FAILURE (the task must not settle done on it), never a quiet
    publish of the newer thing."""
    ws = _repo(tmp_path)
    (ws / "judged.py").write_text("J = 1\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "feat: judged")
    judged = _git(ws, "rev-parse", "HEAD")
    (ws / "sneaked_in_later.py").write_text("S = 1\n")  # drift after the gates

    result = await deliver_change(
        workspace_dir=str(ws), task_id="t1", goal="g", kind="implement_feature",
        judged_head=judged, agent_authored=True,
    )
    assert result["delivered"] is False
    assert "drifted from the judged span" in (result["error"] or "")
    assert delivery_failed(result)  # a REAL failure, not a benign no-op


async def test_delivery_never_stages_or_commits_on_the_judged_path(tmp_path):
    """FR-005's second half: delivery performs no independent discovery. If it
    still staged, an artifact appearing between the gates and delivery would be
    swept into the PR unjudged — the same class of bug from the other end."""
    ws = _repo(tmp_path)
    (ws / "judged.py").write_text("J = 1\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "feat: judged")
    judged = _git(ws, "rev-parse", "HEAD")
    log_before = _git(ws, "log", "--format=%H %s")

    result = await deliver_change(
        workspace_dir=str(ws), task_id="t1", goal="g", kind="implement_feature",
        judged_head=judged, agent_authored=True,
    )
    assert result["committed"] is True
    # the branch moved; the HISTORY did not
    assert _git(ws, "log", "--format=%H %s") == log_before
    assert _git(ws, "rev-parse", "HEAD") == judged


async def test_the_worker_subject_still_titles_the_pr_when_the_worker_wrote_it(tmp_path):
    ws = _repo(tmp_path)
    (ws / "f.py").write_text("F = 1\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m", "fix(feed): stop pagination drift")
    judged = _git(ws, "rev-parse", "HEAD")

    result = await deliver_change(
        workspace_dir=str(ws), task_id="t1", goal="some generic task instruction",
        kind="implement_feature", judged_head=judged, agent_authored=True,
    )
    assert result["branch"].startswith("fix/")


async def test_a_devclaw_authored_span_does_not_title_the_pr_from_its_own_message(
    tmp_path,
):
    """``agent_authored`` replaces the old ``ahead > 0`` proxy. Without it,
    devclaw's own materialization commit would be mistaken for the worker's
    description of the change and re-used as the PR title. Spec 017 criterion 2:
    neither the devclaw commit subject NOR the dispatch prompt can title the PR —
    only the worker's own commit subject or MACHINE_COMMIT_SUBJECT applies."""
    ws = _repo(tmp_path)
    (ws / "f.py").write_text("F = 1\n")
    _git(ws, "add", "-A")
    _git(ws, "commit", "-q", "-m",
         "feat: add a health endpoint\n\nDelivered by devclaw (task t1).")
    judged = _git(ws, "rev-parse", "HEAD")

    result = await deliver_change(
        workspace_dir=str(ws), task_id="t1", goal="wire up the /metrics route",
        kind="implement_feature", judged_head=judged, agent_authored=False,
    )
    # The devclaw-authored commit subject is NOT used (not "add a health endpoint").
    # The dispatch prompt is NOT used (not "metrics").
    # The machine-commit subject IS used — a self-describing snapshot label.
    assert "add-a-health-endpoint" not in result["branch"]
    assert "metrics" not in result["branch"]
    # MACHINE_COMMIT_SUBJECT → branch suffix is "-snapshot"
    assert result["branch"].endswith("-snapshot")


async def test_a_goal_branch_delivery_publishes_the_judged_head_too(
    store, tmp_path, monkeypatch,
):
    """Long-lived goals accumulate increments on one branch; each materialized
    span must stay individually identifiable rather than merging into an
    indistinguishable whole."""
    ws = _repo(tmp_path)
    _git(ws, "checkout", "-q", "-b", "goal/g1")
    seen: list = []
    real = queue_settle.deliver_change

    async def spy(**kwargs):
        seen.append(kwargs.get("judged_head"))
        return await real(**kwargs)

    monkeypatch.setattr(queue_settle, "deliver_change", spy)

    async def runner(req: EngineRequest):
        n = len(seen) + 1
        (ws / f"inc{n}.py").write_text(f"I = {n}\n")
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": _gate()}

    q = TaskQueue(store, runner=runner)
    for _ in range(2):
        q.submit(kind="implement_feature", workspace_dir=str(ws), goal="advance",
                 verify_cmd="pytest", deliver=True)
        await q.drain()

    assert len(seen) == 2 and seen[0] != seen[1]
    # two distinct commits on the branch, each carrying its own increment —
    # the second must not have AMENDED the first out of existence
    assert _git(ws, "show", "--name-only", "--format=", seen[0]).strip() == "inc1.py"
    assert _git(ws, "show", "--name-only", "--format=", seen[1]).strip() == "inc2.py"
    assert _git(ws, "rev-parse", f"{seen[1]}^") == seen[0]
