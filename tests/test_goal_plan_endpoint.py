"""Regression tests for the console Plan view read surface — ``_read_plan_md``,
the best-effort reader behind ``GET /goals/{goal_id}/plan.json``.

The Plan tab surfaces the worker-owned PLAN.md (cognition-demolition: plan-state
is a repo file, not control-plane state). These lock the reader's contract:
a committed PLAN.md is read off the repo; a working-tree-only one still shows;
a repo without a PLAN.md returns content=None (a goal that hasn't planned yet),
never an error; and a non-repo / missing dir degrades to None rather than raising.
"""

from __future__ import annotations

import asyncio
import subprocess

import devclaw.server.http as http_mod


def _git(cwd, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True,
    )


def _read(workspace, goal_id="g1"):
    return asyncio.run(http_mod._read_plan_md(str(workspace), goal_id))


def test_committed_plan_md_is_read(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "PLAN.md").write_text("# Plan\n\n- ship the thing\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    doc = _read(tmp_path)
    assert doc["content"] and "ship the thing" in doc["content"]
    # No origin/goal branch in a bare local repo → falls through to HEAD.
    assert doc["source"] in ("branch", "head")


def test_working_tree_only_plan_md_still_shows(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "PLAN.md").write_text("# Draft plan\nwork in progress\n")
    # NOT committed — HEAD:PLAN.md misses, the worktree fallback catches it.
    doc = _read(tmp_path)
    assert doc["content"] and "work in progress" in doc["content"]
    assert doc["source"] == "worktree"


def test_no_plan_md_returns_none_not_error(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "README.md").write_text("no plan here\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    doc = _read(tmp_path)
    assert doc == {"content": None, "source": None, "ref": None}


def test_non_repo_dir_degrades_to_none(tmp_path):
    # A dir that isn't a git repo and has no PLAN.md — every read path misses,
    # nothing raises.
    doc = _read(tmp_path)
    assert doc == {"content": None, "source": None, "ref": None}


def test_empty_plan_md_is_treated_as_absent(tmp_path):
    _git(tmp_path, "init", "-q")
    (tmp_path / "PLAN.md").write_text("   \n\n")  # whitespace only
    doc = _read(tmp_path)
    assert doc["content"] is None
