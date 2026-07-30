"""Regression tests: staleness check wired into _prep_branch_target.

The check runs after prepare_workspace succeeds (the workspace is on the target
branch) and compares HEAD to origin/<base_branch>: if the branch has 0 commits
of its own but is far enough behind that it's considered hard-stale, the task
is refused before the engine ever runs.

All four required properties:
  1. Hard-stale (ahead==0, behind>=threshold) → fails with actionable message.
  2. Below threshold → proceeds (returns None).
  3. Branch has own commits (ahead>0) → probe fires but condition not met → None.
  4. Probe returns None (git hiccup) → proceeds unchanged (best-effort).
  5. No base_branch supplied → staleness never consulted even if target_branch is set.
  6. Brand-new branch (0 ahead, 0 behind) → proceeds (not stale, no divergence).
"""
from __future__ import annotations

import pytest

from devclaw import task_queue
from devclaw.state_store import StateStore
from devclaw.task_queue import BRANCH_STALE_THRESHOLD, TaskQueue


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path):
    s = StateStore(str(tmp_path / "t.db"))
    yield s
    s.close()


@pytest.fixture()
def queue(store):
    """A TaskQueue with a stub runner; we only call _prep_branch_target."""
    async def _noop_runner(req):
        return {"status": "ok", "workspaceDir": req.workspace_dir, "verify": {}}

    return TaskQueue(store, runner=_noop_runner)


# ---------------------------------------------------------------------------
# Helpers — patch both blocking subprocess seams so no real git runs
# ---------------------------------------------------------------------------

def _patch_prep(monkeypatch, *, staleness_return, base_error=None, default_branch="main"):
    """Replace the three async helpers _prep_branch_target depends on:
    - _base_branch_error → None (base is valid) unless base_error overridden
    - _workspace_default_branch → "main"
    - prepare_workspace → succeeds (no-op)
    - task_queue._branch_staleness → staleness_return
    """
    async def fake_base_branch_error(host_dir, base_branch):
        return base_error

    async def fake_default_branch(workspace_dir):
        return default_branch

    async def fake_prepare_workspace(workspace_dir, branch=None, base_branch=None):
        return branch or default_branch

    async def fake_branch_staleness(host_dir, base_branch):
        return staleness_return

    monkeypatch.setattr(task_queue, "_base_branch_error", fake_base_branch_error)
    monkeypatch.setattr(task_queue, "_workspace_default_branch", fake_default_branch)
    monkeypatch.setattr(task_queue, "prepare_workspace", fake_prepare_workspace)
    monkeypatch.setattr(task_queue, "_branch_staleness", fake_branch_staleness)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hard_stale_branch_fails_with_actionable_message(queue, monkeypatch):
    """0 commits ahead, commits_behind >= BRANCH_STALE_THRESHOLD → task refused."""
    _patch_prep(
        monkeypatch,
        staleness_return={
            "commits_ahead": 0,
            "commits_behind": BRANCH_STALE_THRESHOLD,
        },
    )
    result = await queue._prep_branch_target(
        "/ws", base_branch="main", target_branch="feat/old"
    )
    assert result is not None
    assert "feat/old" in result
    assert "main" in result
    assert str(BRANCH_STALE_THRESHOLD) in result
    # Must name the divergence counts
    assert str(BRANCH_STALE_THRESHOLD) in result  # commits_behind count


@pytest.mark.asyncio
async def test_hard_stale_message_names_branch_and_base(queue, monkeypatch):
    """Actionable message explicitly names branch, base, and counts."""
    behind_count = BRANCH_STALE_THRESHOLD + 20
    _patch_prep(
        monkeypatch,
        staleness_return={"commits_ahead": 0, "commits_behind": behind_count},
    )
    result = await queue._prep_branch_target(
        "/ws", base_branch="develop", target_branch="feat/stale-feature"
    )
    assert result is not None
    assert "feat/stale-feature" in result
    assert "develop" in result
    assert str(behind_count) in result


@pytest.mark.asyncio
async def test_below_threshold_proceeds(queue, monkeypatch):
    """0 commits ahead but behind < threshold → not hard-stale, return None."""
    _patch_prep(
        monkeypatch,
        staleness_return={
            "commits_ahead": 0,
            "commits_behind": BRANCH_STALE_THRESHOLD - 1,
        },
    )
    result = await queue._prep_branch_target(
        "/ws", base_branch="main", target_branch="feat/recent"
    )
    assert result is None


@pytest.mark.asyncio
async def test_branch_with_own_commits_proceeds(queue, monkeypatch):
    """commits_ahead > 0 even with large commits_behind → work in progress, not stale."""
    _patch_prep(
        monkeypatch,
        staleness_return={
            "commits_ahead": 3,
            "commits_behind": BRANCH_STALE_THRESHOLD + 100,
        },
    )
    result = await queue._prep_branch_target(
        "/ws", base_branch="main", target_branch="feat/active"
    )
    assert result is None


@pytest.mark.asyncio
async def test_staleness_probe_none_proceeds(queue, monkeypatch):
    """None from the probe (git hiccup) → best-effort, proceed unchanged."""
    _patch_prep(monkeypatch, staleness_return=None)
    result = await queue._prep_branch_target(
        "/ws", base_branch="main", target_branch="feat/unknown"
    )
    assert result is None


@pytest.mark.asyncio
async def test_brand_new_branch_zero_ahead_zero_behind_proceeds(queue, monkeypatch):
    """A freshly created branch (0 ahead, 0 behind) must NOT be blocked.

    This is the first-dispatch case for every new goal branch: no work has
    landed yet so it has 0 commits of its own AND 0 commits of divergence.
    The stale threshold check requires BOTH conditions (ahead==0 AND
    behind>=threshold) — 0/0 satisfies only the first, so it must pass.
    """
    _patch_prep(
        monkeypatch,
        staleness_return={"commits_ahead": 0, "commits_behind": 0},
    )
    result = await queue._prep_branch_target(
        "/ws", base_branch="main", target_branch="feat/brand-new"
    )
    assert result is None


@pytest.mark.asyncio
async def test_no_base_branch_skips_staleness_probe(queue, monkeypatch):
    """Without a base_branch the staleness probe must never be called."""
    probe_called = False

    async def spy_branch_staleness(host_dir, base_branch):
        nonlocal probe_called
        probe_called = True
        return None

    async def fake_default_branch(workspace_dir):
        return "main"

    async def fake_prepare_workspace(workspace_dir, branch=None, base_branch=None):
        return branch or "main"

    monkeypatch.setattr(task_queue, "_branch_staleness", spy_branch_staleness)
    monkeypatch.setattr(task_queue, "_workspace_default_branch", fake_default_branch)
    monkeypatch.setattr(task_queue, "prepare_workspace", fake_prepare_workspace)

    result = await queue._prep_branch_target(
        "/ws", base_branch=None, target_branch="feat/no-base"
    )
    assert result is None
    assert not probe_called, "staleness probe must not fire when base_branch is absent"
