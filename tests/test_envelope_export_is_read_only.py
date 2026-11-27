"""#1061: ``devclaw tasks --json`` hands the fleet view one envelope per open
goal and task. It is a pointer, never a copy (the issue owns the item), and it
never writes — a fleet reader on the same database must not be able to move it."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from devclaw import cli
from types import SimpleNamespace
from typing import get_args

from devclaw.envelopes import (A2A_STATES, ENVELOPE_KEYS, GOAL_STATES, TASK_STATES, _a2a,
                               _goal_envelope, open_envelopes)
from devclaw.goal.service import _state_word
from devclaw.state_store import EXITS, StateStore
from devclaw.state_store.rows import TaskStatus

SECRET = "sk-secret-session-content"


def _snapshot(directory: Path) -> dict[str, tuple[str, int]]:
    """Every file beside the database — except ``-shm``, the WAL's in-memory
    index that SQLite itself updates for any reader; the data files must not move."""
    return {p.name: (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(directory.iterdir()) if not p.name.endswith("-shm")}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "state" / "devclaw.db"
    store = StateStore(str(path))
    yield store, path
    store.close()


def test_an_empty_state_exports_an_empty_list(db):
    _, path = db
    assert open_envelopes(str(path)) == []


def test_an_absent_database_is_empty_and_is_not_created(tmp_path):
    path = tmp_path / "nowhere" / "devclaw.db"
    assert open_envelopes(str(path)) == []
    assert not path.exists() and not path.parent.exists()


def test_a_goal_with_issue_numbers_exports_refs_not_issue_text(db):
    store, path = db
    store.create_goal(id="night-1", project_id="devclaw", workspace_dir=f"/srv/{SECRET}",
                      repo_url="https://github.com/lifekit-hq/devclaw.git",
                      objective=f"title copy {SECRET}", issues=[1061, 1062], branch="goal/night-1")
    store.create_goal(id="old", project_id="devclaw", workspace_dir="/w", repo_url="",
                      objective="", issues=[1], branch="goal/old")
    store.close_goal("old", "achieved")

    [env] = open_envelopes(str(path))

    assert env["id"] == "devclaw:night-1" and env["kind"] == "goal"
    assert env["ref"] == "lifekit-hq/devclaw#1061"
    assert env["refs"] == ["lifekit-hq/devclaw#1061", "lifekit-hq/devclaw#1062"]
    assert (env["state"], env["detail"], env["owner"]) == ("submitted", "new", "devclaw")
    assert SECRET not in json.dumps(env)


def test_the_envelope_shape_and_the_blocked_owner(db):
    store, path = db
    store.create_goal(id="g", project_id="p", workspace_dir="/w", repo_url="https://github.com/o/r",
                      objective="o", issues=[7], branch="goal/g")
    store.create_task(id="t-blocked", kind="implement_feature", workspace_dir="/w",
                      goal=f"prompt {SECRET}", parent_goal_id="g")
    store.claim_pending("t-blocked")
    store.mark_failed("t-blocked", f"error {SECRET}", exit="BLOCKED", exit_detail=f"q {SECRET}",
                      result_json=json.dumps({"session": SECRET}))
    store.set_task_session_id("t-blocked", SECRET)
    store.create_task(id="t-run", kind="review_repository", workspace_dir="/w",
                      goal=f"prompt {SECRET}")

    goal_env, task_env = open_envelopes(str(path))

    assert tuple(goal_env) == tuple(task_env) == ENVELOPE_KEYS
    assert (goal_env["store"], goal_env["class"]) == ("devclaw", "dev")
    assert (goal_env["state"], goal_env["owner"], goal_env["stage"]) == ("input-required", "owner", "build")
    assert goal_env["detail"] == "blocked"
    assert task_env == {**task_env, "id": "devclaw:t-run", "kind": "task", "ref": "t-run",
                        "stage": "review", "state": "submitted", "detail": "pending",
                        "owner": "devclaw"}
    for env in (goal_env, task_env):
        assert env["since"].endswith("Z") and "T" in env["since"]
    assert SECRET not in json.dumps([goal_env, task_env])


def test_the_export_never_writes(db, capsys, monkeypatch):
    store, path = db
    store.create_goal(id="g", project_id="p", workspace_dir="/w", repo_url="https://github.com/o/r",
                      objective="o", issues=[7], branch="goal/g")
    store.create_task(id="t", kind="fix_bug", workspace_dir="/w", goal="x", parent_goal_id="g")
    before = _snapshot(path.parent)

    open_envelopes(str(path))
    assert _snapshot(path.parent) == before

    ro = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    ro.execute("PRAGMA query_only = ON")
    with pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM tasks")
    ro.close()

    monkeypatch.setenv("DEVCLAW_DB", str(path))
    assert cli.main(["tasks", "--json"]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert [e["id"] for e in printed] == ["devclaw:g", "devclaw:t"]
    assert _snapshot(path.parent) == before


def _every_goal_word() -> set[str]:
    """Every word ``_state_word`` can produce: open goals over every task status
    and exit, closed goals over every outcome the tick writes, and none."""
    words = {_state_word(SimpleNamespace(open=True, outcome=None), None)}
    for status in get_args(TaskStatus):
        for exit_ in (None, *EXITS):
            words.add(_state_word(SimpleNamespace(open=True, outcome=None),
                                  SimpleNamespace(status=status, exit=exit_)))
    for outcome in ("achieved", "cancelled", None):
        words.add(_state_word(SimpleNamespace(open=False, outcome=outcome), None))
    return words


def test_every_devclaw_state_maps_to_an_a2a_state_and_the_owner_only_on_input_required():
    words = _every_goal_word()
    assert words == set(GOAL_STATES), "a goal state word with no A2A mapping (or a dead one)"
    assert set(get_args(TaskStatus)) == set(TASK_STATES), "a task status with no A2A mapping"
    assert set(GOAL_STATES.values()) | set(TASK_STATES.values()) <= set(A2A_STATES)

    for word in words:
        goal = SimpleNamespace(open=True, outcome=None, created_at=0, repo_url="", project_id="p",
                               issues=[1], id="g")
        exit_ = {"blocked": "BLOCKED", "proposed done": "DONE", "interrupted": "INTERRUPTED"}.get(word)
        last = (None if word == "new" else
                SimpleNamespace(status="running" if word == "running" else "done", exit=exit_,
                                started_at=0, created_at=0, completed_at=0, kind="implement_feature"))
        if word in ("achieved", "cancelled", "closed"):
            goal.open, goal.outcome = False, (None if word == "closed" else word)
        env = _goal_envelope(goal, last)
        assert (env["state"], env["detail"]) == (GOAL_STATES[word], word)
        assert (env["owner"] == "owner") == (env["state"] == "input-required")

    with pytest.raises(ValueError):
        _a2a(GOAL_STATES, "some new word")
