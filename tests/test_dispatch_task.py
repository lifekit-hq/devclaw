"""dispatch_task — the consolidated one-shot task tool.

Pins:
  1. Each kind (implement_feature, fix_bug, review_repository) reaches
     queue.submit with the correct ``kind`` string, so downstream routing
     (planner _VALID_TOOLS, engine.py review branch,
     state_store.TaskKind) keeps working unchanged.
  2. review_repository ignores verify_cmd + open_pr — the old dedicated tool
     never accepted them, and the merge must not smuggle a gate/PR into a
     read-only review.
  3. The deprecated aliases (implement_feature, fix_bug, review_repository)
     still forward the same kind/goal/deliver as before, so external MCP callers
     don't break.
  4. spec 003 / #520: the tool takes a ``project_id`` (not a raw path); devclaw
     resolves the workspace from the registry, rejects an unknown project, and
     preflights that the workspace is a real git checkout before submit.
"""

from __future__ import annotations

import json

import pytest
from fastmcp.exceptions import ToolError

from devclaw.project_registry import ProjectRegistry
from devclaw.server import tools as _tools
from tests.goal_fakes import register_tmp_project


class _Env:
    def __init__(self, calls, project_id, workspace_dir):
        self.calls = calls
        self.pid = project_id
        self.ws = workspace_dir


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Spy queue.submit + a registry holding one registered project ('proj')
    whose workspace is a REAL git checkout (so the dispatch preflight passes).
    Patches the tools module's ``registry``/``queue`` seams."""
    calls: list[dict] = []

    def _fake_submit(**kwargs) -> str:
        calls.append(kwargs)
        return f"task_{len(calls)}"

    from devclaw.server import _state

    monkeypatch.setattr(_state.queue, "submit", _fake_submit)
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "wsp"
    register_tmp_project(reg, ws, project_id="proj",
                         repo_url="https://github.com/lifekit-hq/x.git")
    monkeypatch.setattr(_tools, "registry", reg)
    return _Env(calls, "proj", str(ws))


async def test_dispatch_task_implement_feature_forwards_kind_and_deliver(env):
    raw = await _tools.dispatch_task(
        kind="implement_feature",
        project_id=env.pid,
        goal="add /health",
        verify_cmd="pytest -q",
        open_pr=True,
    )
    result = json.loads(raw)
    assert result["task_id"] == "task_1"
    assert result["status"] == "pending"
    (call,) = env.calls
    assert call["kind"] == "implement_feature"
    assert call["workspace_dir"] == env.ws  # resolved from the registry row
    assert call["goal"] == "add /health"
    assert call["verify_cmd"] == "pytest -q"
    assert call["deliver"] is True


async def test_dispatch_task_fix_bug_forwards_kind(env):
    await _tools.dispatch_task(
        kind="fix_bug",
        project_id=env.pid,
        goal="fix crash on empty payload",
        verify_cmd="pytest",
    )
    (call,) = env.calls
    assert call["kind"] == "fix_bug"
    assert call["goal"] == "fix crash on empty payload"
    assert call["verify_cmd"] == "pytest"
    assert call["deliver"] is False


async def test_dispatch_task_review_repository_ignores_verify_and_open_pr(env):
    await _tools.dispatch_task(
        kind="review_repository",
        project_id=env.pid,
        goal="focus on auth",
        verify_cmd="pytest",
        open_pr=True,
    )
    (call,) = env.calls
    assert call["kind"] == "review_repository"
    assert call["verify_cmd"] is None, "review is read-only — no verify gate"
    assert call["deliver"] is False, "review is read-only — no PR delivery"


async def test_dispatch_task_rejects_empty_project_or_goal(env):
    with pytest.raises(ToolError, match="project_id"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id="", goal="x"
        )
    with pytest.raises(ToolError, match="project_id and goal"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id=env.pid, goal=""
        )
    assert env.calls == [], "a rejected dispatch submits no task"


async def test_implement_feature_alias_still_submits_same_kind(env):
    await _tools.implement_feature(
        project_id=env.pid, goal="add /health", open_pr=True
    )
    (call,) = env.calls
    assert call["kind"] == "implement_feature"
    assert call["deliver"] is True


async def test_fix_bug_alias_still_submits_same_kind(env):
    await _tools.fix_bug(
        project_id=env.pid, description="crash on empty payload"
    )
    (call,) = env.calls
    assert call["kind"] == "fix_bug"
    assert call["goal"] == "crash on empty payload"


async def test_review_repository_alias_still_submits_same_kind(env):
    await _tools.review_repository(project_id=env.pid, focus="auth")
    (call,) = env.calls
    assert call["kind"] == "review_repository"
    assert call["goal"] == "auth"
    assert call["verify_cmd"] is None
    assert call["deliver"] is False


async def test_review_repository_alias_defaults_goal_when_no_focus(env):
    await _tools.review_repository(project_id=env.pid)
    (call,) = env.calls
    assert call["goal"] == "general code review"


async def test_dispatch_task_forwards_branch_targets_to_submit(env):
    """v1-helper-resurface PR-2: the MCP surface threads base_branch /
    target_branch into queue.submit — and omitting them submits None (the
    byte-identical legacy shape)."""
    await _tools.dispatch_task(
        kind="implement_feature",
        project_id=env.pid,
        goal="continue spec 035",
        open_pr=True,
        base_branch="develop",
        target_branch="feat/spec-035",
    )
    await _tools.dispatch_task(
        kind="implement_feature", project_id=env.pid, goal="plain",
    )
    first, second = env.calls
    assert first["base_branch"] == "develop"
    assert first["target_branch"] == "feat/spec-035"
    assert second["base_branch"] is None and second["target_branch"] is None


# ---- spec 003 / #520 regression tests (quickstart scenarios) ----------------


async def test_dispatch_by_project_id_resolves_workspace_and_repo(env):
    """Quickstart 1: naming a registered project_id resolves its workspace from
    the registry row — the caller never passes a path."""
    await _tools.dispatch_task(
        kind="implement_feature", project_id="proj", goal="do the thing"
    )
    (call,) = env.calls
    assert call["workspace_dir"] == env.ws


async def test_dispatch_unknown_project_id_rejected_zero_token(env):
    """Quickstart 2: an unknown project_id is rejected synchronously — no task
    is submitted (zero downstream/engine work)."""
    with pytest.raises(ToolError, match="unknown project_id"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id="ghost", goal="x"
        )
    assert env.calls == [], "unknown project must not reach queue.submit"


async def test_preflight_rejects_non_git_workspace_before_submit(monkeypatch, tmp_path):
    """Quickstart 3: a registered project whose workspace is not a git checkout
    is rejected at admission, BEFORE queue.submit — replacing the old late
    sandbox-launch failure."""
    calls: list[dict] = []
    from devclaw.server import _state

    monkeypatch.setattr(_state.queue, "submit", lambda **k: calls.append(k) or "t1")
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    bare = tmp_path / "bare"  # exists but no .git
    register_tmp_project(reg, bare, project_id="nogit", git_init=False)
    monkeypatch.setattr(_tools, "registry", reg)
    with pytest.raises(ToolError, match="not a git checkout"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id="nogit", goal="x"
        )
    assert calls == [], "preflight rejects before any submit/claim"


async def test_by_key_dispatch_preserves_override_knobs(monkeypatch, tmp_path):
    """Quickstart 4: override knobs still resolve via the workspace-path join off
    the RESOLVED workspace — by-key dispatch yields the identical gating decision
    as the old by-path dispatch did (FR-007: resolution populates, not replaces)."""
    calls: list[dict] = []
    from devclaw.server import _state

    monkeypatch.setattr(_state.queue, "submit", lambda **k: calls.append(k) or "t1")
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "wsp"
    register_tmp_project(reg, ws, project_id="knobbed",
                         automerge=True, review_gate=False)
    monkeypatch.setattr(_tools, "registry", reg)
    await _tools.dispatch_task(
        kind="implement_feature", project_id="knobbed", goal="x"
    )
    (call,) = calls
    # the submitted task carries the project_id, and the knobs resolve BY that id
    # (#524 P3) — the same values a by-path resolve produced before.
    assert call["project_id"] == "knobbed"
    assert reg.resolve_override("knobbed", "automerge", None) is True
    assert reg.resolve_override("knobbed", "review_gate", True) is False


# ---- #523 (P2): direct-path auto-prep from repo_url -------------------------


def _make_source_repo(path):
    """A real local git repo with one commit, usable as a clone source (offline)."""
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "f.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        check=True,
    )
    return str(path)


async def test_dispatch_auto_preps_missing_workspace_from_repo_url(monkeypatch, tmp_path):
    """#523 (P2): a registered project whose workspace is ABSENT but which carries
    a repo_url is auto-cloned at dispatch instead of rejected, then submitted."""
    calls: list[dict] = []
    from devclaw.server import _state

    monkeypatch.setattr(_state.queue, "submit", lambda **k: calls.append(k) or "t1")
    repo_url = _make_source_repo(tmp_path / "src")
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    ws = tmp_path / "clone-here"  # does NOT exist yet
    reg.create(id="fresh", name="fresh", workspace_dir=str(ws), repo_url=repo_url)
    monkeypatch.setattr(_tools, "registry", reg)
    await _tools.dispatch_task(
        kind="implement_feature", project_id="fresh", goal="x"
    )
    assert (ws / ".git").exists(), "absent workspace was auto-cloned from repo_url"
    (call,) = calls
    assert call["workspace_dir"] == str(ws)


async def test_dispatch_missing_workspace_no_repo_url_still_rejects(monkeypatch, tmp_path):
    """#523 (P2): auto-prep is scoped — an absent workspace with NO repo_url has
    nothing to clone from, so it stays a loud reject (no submit)."""
    calls: list[dict] = []
    from devclaw.server import _state

    monkeypatch.setattr(_state.queue, "submit", lambda **k: calls.append(k) or "t1")
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    reg.create(id="norepo", name="norepo", workspace_dir=str(tmp_path / "nope"))
    monkeypatch.setattr(_tools, "registry", reg)
    with pytest.raises(ToolError, match="does not exist"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id="norepo", goal="x"
        )
    assert calls == [], "no repo to clone from → reject, never submit"
