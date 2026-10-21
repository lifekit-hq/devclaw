"""The done-gate review is read-only, checked rather than trusted (#954).

The review session runs ``claude -p`` in the goal's worktree with the host's
tools; nothing seals the tree. A review that committed or edited a tracked
file judged its own work, not the delivered head, so the queue settles it
failed: the tick renders that as an unreadable verdict, which never merges.
Real ``git init`` repo, stub engine.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from devclaw.engine import EngineRequest
from devclaw.state_store import EXIT_REVIEW, StateStore
from devclaw.task_queue import TaskQueue

ACHIEVED = '```json\n{"achieved": true, "clauses": [{"clause": "c", "satisfied": true, "evidence": "f.py"}]}\n```'


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.py").write_text("x = 1\n")
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return repo


def _commit(repo):
    (repo / "f.py").write_text("x = 2\n")
    _git(repo, "commit", "-am", "review fixed it")


def _edit(repo):
    (repo / "f.py").write_text("x = 2\n")


def _untracked(repo):
    (repo / ".pytest_cache").mkdir()
    (repo / ".pytest_cache" / "v").write_text("{}")


@pytest.mark.parametrize("touch, settled", [
    (None, "done"),
    (_untracked, "done"),          # a test run's leftovers are not the delivered head
    (_edit, "failed"),
    (_commit, "failed"),
])
async def test_a_review_that_changed_the_tree_never_settles_a_verdict(tmp_path, repo, touch, settled):
    store = StateStore(str(tmp_path / "t.db"))

    async def review(req: EngineRequest):
        if touch:
            touch(repo)
        return {"status": "ok", "workspaceDir": req.workspace_dir, "agent_output": ACHIEVED}

    q = TaskQueue(store, runner=review)
    tid = q.submit(kind="review_repository", workspace_dir=str(repo), goal="review")
    await q.drain()
    t = store.get_task(tid)
    assert t.exit == EXIT_REVIEW and t.status == settled
    assert json.loads(t.result_json)["agent_output"] == ACHIEVED
    if settled == "failed":
        assert "read-only" in (t.error or "")
    store.close()


async def test_a_quota_paused_review_leaves_no_snapshot_and_resumes_clean(tmp_path, repo):
    store = StateStore(str(tmp_path / "t.db"))
    calls = []

    async def review(req: EngineRequest):
        calls.append(req)
        if len(calls) == 1:
            _untracked(repo)
            return {"status": "error", "error": "API Error: 429 Too Many Requests"}
        return {"status": "ok", "workspaceDir": req.workspace_dir, "agent_output": ACHIEVED}

    q = TaskQueue(store, runner=review)
    tid = q.submit(kind="review_repository", workspace_dir=str(repo), goal="review")
    await q.drain()
    assert store.get_task(tid).status == "pending"
    log = subprocess.run(["git", "log", "--oneline"], cwd=repo, check=True, capture_output=True, text=True).stdout
    assert "wip(devclaw)" not in log
    store.clear_global_pause()
    q.pump()
    await q.drain()
    t = store.get_task(tid)
    assert t.status == "done" and t.exit == EXIT_REVIEW
    store.close()
