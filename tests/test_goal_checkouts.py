"""One goal, one checkout (2026-09-06) — the isolation invariant.

A goal's tasks run in ``<project>/.goals/<goal_id>``, a clone no other goal's
task can move. Pinned here: the project checkout's pristine clean keeps the
goal checkouts (``clean -fdx`` would otherwise delete every in-flight clone
on a direct task's prep); the seed is a local clone whose ``origin`` is the
real remote; a terminal goal's checkout is swept, a live one and a foreign
directory are not.
"""
from __future__ import annotations

import subprocess

import pytest

from devclaw.engine import workspace as ws_mod
from devclaw.goal.store import GoalStore
from devclaw.goal.models import GoalStatus
from devclaw.goal.tick import sweep_goal_checkouts
from tests.goal_fakes import Clock, seed_goal

pytestmark = pytest.mark.asyncio


def _git(d, *args) -> str:
    return subprocess.run(["git", "-C", str(d), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def _project_with_origin(tmp_path):
    """A project checkout cloned from a bare origin on main."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(origin)], check=True)
    seed = tmp_path / "seed"
    seed.mkdir()
    for args in (["init", "-q", "-b", "main"], ["config", "user.email", "t@t"],
                 ["config", "user.name", "t"]):
        _git(seed, *args)
    (seed / "README.md").write_text("# base\n")
    _git(seed, "add", "-A"); _git(seed, "commit", "-q", "-m", "base")
    _git(seed, "remote", "add", "origin", str(origin))
    _git(seed, "push", "-q", "-u", "origin", "main")
    project = tmp_path / "project"
    subprocess.run(["git", "clone", "-q", str(origin), str(project)], check=True)
    return project, origin


async def test_project_prep_keeps_goal_checkouts(tmp_path):
    project, _ = _project_with_origin(tmp_path)
    marker = project / ".goals" / "g1" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_text("in flight\n")

    await ws_mod.prepare_workspace(str(project), branch=None)        # default-branch reset
    assert marker.exists()
    await ws_mod.prepare_workspace(str(project), branch="goal/other")  # goal-branch clean
    assert marker.exists()


async def test_goal_checkout_is_a_local_clone_pointed_at_the_real_origin(tmp_path):
    project, origin = _project_with_origin(tmp_path)

    await ws_mod.ensure_goal_checkout(str(project), None, "g1")

    checkout = project / ".goals" / "g1"
    assert (checkout / ".git").is_dir()                       # a full clone, not a worktree
    assert _git(checkout, "remote", "get-url", "origin") == str(origin)
    assert ".goals/" in (project / ".git" / "info" / "exclude").read_text()
    # prepare_workspace then owns placement in the goal checkout
    assert await ws_mod.prepare_workspace(str(checkout), None, "goal/g1") == "goal/g1"
    assert _git(checkout, "branch", "--show-current") == "goal/g1"
    assert _git(project, "status", "--porcelain") == ""          # the project's git never sees it
    assert ws_mod.project_workspace_for(str(checkout)) == str(project)


async def test_sweep_removes_only_terminal_goal_checkouts(tmp_path):
    project, _ = _project_with_origin(tmp_path)
    goals_dir = tmp_path / "goals"
    store = GoalStore(goals_dir, now=Clock())
    seed_goal(goals_dir, "done-goal", workspace_dir=str(project))
    seed_goal(goals_dir, "live-goal", workspace_dir=str(project))
    store.save_status("done-goal", GoalStatus(phase="done", lifecycle="executing"))
    store.save_status("live-goal", GoalStatus(phase="in_flight", lifecycle="executing"))
    for name in ("done-goal", "live-goal", "not-a-goal"):
        (project / ".goals" / name).mkdir(parents=True)
        (project / ".goals" / name / "f").write_text("x")

    removed = sweep_goal_checkouts(store)

    assert removed == ["done-goal"]
    assert not (project / ".goals" / "done-goal").exists()
    assert (project / ".goals" / "live-goal").exists()        # live work is never swept
    assert (project / ".goals" / "not-a-goal").exists()       # unknown dirs are left alone
