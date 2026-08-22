"""Regression tests for the console Plan view read surface — ``_read_plan``,
the best-effort reader behind ``GET /goals/{goal_id}/plan.json``.

The Plan tab surfaces the worker-owned execution contract: the active feature's
``specs/NNN-*/tasks.md``. It used to read a repo-root ``PLAN.md``; nothing has
written that file since the spec 008 speckit shrink, so the console's DEFAULT
tab returned ``content: None`` for every goal. Deleting the PLAN.md reader
(#614) did not break the tab — it had been empty for weeks; pointing it at the
file the worker actually maintains is what restores it.

These lock the reader's contract: a committed tasks.md is read off the repo;
a working-tree-only one still shows; the ACTIVE feature wins when several
exist; a repo with no speckit contract returns content=None (a goal that hasn't
planned yet), never an error; and a non-repo / missing dir degrades to None
rather than raising.
"""

from __future__ import annotations

import asyncio
import subprocess

import devclaw.server.routes.goals as http_mod

_TASKS = """# Tasks: widget

- [ ] T001 [P] [US1] scaffold the module
- [ ] T002 [US1] wire the endpoint
"""


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _read(workspace, goal_id="g1"):
    return asyncio.run(http_mod._read_plan(str(workspace), goal_id))


def _feature(root, name, text=_TASKS):
    d = root / "specs" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "tasks.md").write_text(text)
    return d


def test_committed_tasks_md_is_read(tmp_path):
    _git(tmp_path, "init", "-q")
    _feature(tmp_path, "001-widget")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    doc = _read(tmp_path)
    assert doc["content"] and "wire the endpoint" in doc["content"]
    # No origin/goal branch in a bare local repo → falls through to HEAD.
    assert doc["source"] in ("branch", "head")


def test_the_plan_names_which_feature_it_came_from(tmp_path):
    """The tab can show any of a goal's features over its life, so the payload
    carries the path — otherwise the operator reads a task list with no idea
    which feature it belongs to."""
    _git(tmp_path, "init", "-q")
    _feature(tmp_path, "012-saga-prompt-contract")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    assert _read(tmp_path)["path"] == "specs/012-saga-prompt-contract/tasks.md"


def test_the_active_feature_wins_when_several_exist(tmp_path):
    """Named regression (#614): the Plan tab shows the feature being worked.

    Off a ref there is no mtime to order by, so the highest-numbered feature
    dir is the active one — a stalled specs/001 must not shadow specs/012.
    """
    _git(tmp_path, "init", "-q")
    _feature(tmp_path, "001-stalled", "# Tasks: stalled\n\n- [ ] T001 [US1] old\n")
    _feature(tmp_path, "012-active", "# Tasks: active\n\n- [ ] T001 [US1] current\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "two features")
    doc = _read(tmp_path)
    assert "current" in doc["content"]
    assert "old" not in doc["content"]


def test_working_tree_only_tasks_md_still_shows(tmp_path):
    _git(tmp_path, "init", "-q")
    _feature(tmp_path, "001-widget", "# Tasks: draft\nwork in progress\n")
    # NOT committed — every ref read misses, the worktree fallback catches it.
    doc = _read(tmp_path)
    assert doc["content"] and "work in progress" in doc["content"]
    assert doc["source"] == "worktree"


def test_no_speckit_contract_returns_none_not_error(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("no specs here\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    doc = _read(tmp_path)
    assert doc == {"content": None, "source": None, "ref": None, "path": None}


def test_a_repo_root_plan_md_is_no_longer_a_source(tmp_path):
    """The deleted reader must not come back by accident: a repo carrying the
    old PLAN.md and no speckit contract reads as unplanned, not as a plan."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "PLAN.md").write_text("# Plan\n\n- ship the thing\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "legacy plan only")
    doc = _read(tmp_path)
    assert doc == {"content": None, "source": None, "ref": None, "path": None}


def test_non_repo_dir_degrades_to_none(tmp_path):
    # A dir that isn't a git repo and has no specs/ — every read path misses,
    # nothing raises.
    doc = _read(tmp_path)
    assert doc == {"content": None, "source": None, "ref": None, "path": None}


def test_empty_tasks_md_is_treated_as_absent(tmp_path):
    _git(tmp_path, "init", "-q")
    _feature(tmp_path, "001-widget", "   \n\n")  # whitespace only
    doc = _read(tmp_path)
    assert doc["content"] is None
