"""dispatch_task — the consolidated one-shot task tool.

Pins:
  1. review_repository ignores verify_cmd + open_pr — the old dedicated tool
     never accepted them, and the merge must not smuggle a gate/PR into a
     read-only review.
  2. The kind-specific aliases (implement_feature, fix_bug, review_repository)
     survive as thin sugar (FR-008). Auto-file behavior is pinned in
     test_issue_keyed_dispatch.py (spec 022 US3 T011).
  3. spec 003 / #520: the tool takes a ``project_id`` (not a raw path); devclaw
     resolves the workspace from the registry, rejects an unknown project, and
     preflights that the workspace is a real git checkout before submit.
"""

from __future__ import annotations

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
    monkeypatch.setattr(_tools._common, "registry", reg)
    return _Env(calls, "proj", str(ws))


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


# ---- spec 003 / #520 regression tests (quickstart scenarios) ----------------


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
    monkeypatch.setattr(_tools._common, "registry", reg)
    with pytest.raises(ToolError, match="not a git checkout"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id="nogit", goal="x"
        )
    assert calls == [], "preflight rejects before any submit/claim"


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


async def test_dispatch_missing_workspace_no_repo_url_still_rejects(monkeypatch, tmp_path):
    """#523 (P2): auto-prep is scoped — an absent workspace with NO repo_url has
    nothing to clone from, so it stays a loud reject (no submit)."""
    calls: list[dict] = []
    from devclaw.server import _state

    monkeypatch.setattr(_state.queue, "submit", lambda **k: calls.append(k) or "t1")
    reg = ProjectRegistry(str(tmp_path / "reg.db"))
    reg.create(id="norepo", name="norepo", workspace_dir=str(tmp_path / "nope"))
    monkeypatch.setattr(_tools._common, "registry", reg)
    with pytest.raises(ToolError, match="does not exist"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id="norepo", goal="x"
        )
    assert calls == [], "no repo to clone from → reject, never submit"


# ---- #727 review findings + spec 022 US2/US3 pins ---------------------------


@pytest.fixture
def goal_lane(monkeypatch, env):
    """env + a faked goal-lane seam: dispatch_issue recorded, never real."""
    from devclaw.server import _state

    dispatched: list[dict] = []

    async def _fake_dispatch(**kwargs) -> dict:
        dispatched.append(kwargs)
        return {"result": "created", "goal_id": "g-test",
                "issue_ref": kwargs.get("issue_ref"), "message": "ok"}

    monkeypatch.setattr(_state.goals, "dispatch_issue", _fake_dispatch)
    env.dispatched = dispatched
    return env


async def test_mutating_dispatch_rejects_branch_targets_loudly(goal_lane):
    """#727 finding 1: base_branch/target_branch used to be silently DISCARDED
    for mutating kinds (dispatch_issue carries neither) — a documented
    parameter is threaded or rejected, never eaten. Same class: notify_url
    (dispatch_issue carries no notify seam either — the goal lane notifies
    through the goal's own path). Read-only review keeps all of them."""
    for kwargs in (dict(base_branch="develop"), dict(target_branch="feat/x"),
                   dict(base_branch="develop", target_branch="feat/x")):
        with pytest.raises(ToolError, match="goal lane"):
            await _tools.dispatch_task(
                kind="implement_feature", project_id=goal_lane.pid,
                goal="continue the work", issue_ref=7, **kwargs,
            )
    with pytest.raises(ToolError, match="notify_url does not apply"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id=goal_lane.pid,
            goal="continue the work", issue_ref=7,
            notify_url="http://waiter/notify",
        )
    assert goal_lane.dispatched == [] and goal_lane.calls == []
    # read-only still threads them into the queue submit
    await _tools.dispatch_task(
        kind="review_repository", project_id=goal_lane.pid, goal="review it",
        base_branch="develop", target_branch="feat/x",
        notify_url="http://waiter/notify",
    )
    (call,) = goal_lane.calls
    assert call["base_branch"] == "develop" and call["target_branch"] == "feat/x"
    assert call["notify_url"] == "http://waiter/notify"


async def test_prose_dispatch_requires_real_done_when(goal_lane, monkeypatch):
    """#727 finding 2: the auto-filed contract must be REAL criteria — never a
    verbatim restatement of the ask, never padding to clear the doorway
    minimum. A thin prose ask is refused naming what is missing."""
    from devclaw.server import tools as _tools_pkg

    filed: list[dict] = []

    async def _fake_file_intake(registry, **kwargs):
        filed.append(kwargs)
        return {"issue_url": "https://github.com/org/repo/issues/99"}

    monkeypatch.setattr(_tools_pkg.tasks._intake, "file_intake", _fake_file_intake)

    with pytest.raises(ToolError, match="done_when"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id=goal_lane.pid, goal="add caching",
        )
    with pytest.raises(ToolError, match="verbatim"):
        await _tools.dispatch_task(
            kind="implement_feature", project_id=goal_lane.pid,
            goal="add caching to the sessions endpoint",
            done_when="add caching to the sessions endpoint",
        )
    assert filed == [] and goal_lane.dispatched == []

    await _tools.dispatch_task(
        kind="implement_feature", project_id=goal_lane.pid,
        goal="add caching to the sessions endpoint",
        done_when="GET /sessions p95 under 50ms with a cache hit, covered by a test",
    )
    (intake_call,) = filed
    assert intake_call["done_when"].startswith("GET /sessions p95")
    (lane_call,) = goal_lane.dispatched
    assert lane_call["issue_ref"] == 99


async def test_goal_lane_dispatch_preserves_override_knobs(goal_lane, monkeypatch, tmp_path):
    """#727 finding 3a (re-pin of #524 P3, moved to the goal lane): the
    project's identity — the key every override knob resolves by — reaches
    dispatch_issue, so by-key dispatch yields the same gating decisions the
    direct path did."""
    reg = ProjectRegistry(str(tmp_path / "reg2.db"))
    ws = tmp_path / "knobbed-ws"
    register_tmp_project(reg, ws, project_id="knobbed",
                         repo_url="https://github.com/org/knobbed.git",
                         autodeploy=True, review_gate=False)
    monkeypatch.setattr(_tools._common, "registry", reg)

    await _tools.dispatch_task(
        kind="implement_feature", project_id="knobbed", goal="x", issue_ref=5,
    )
    (call,) = goal_lane.dispatched
    assert call["project_id"] == "knobbed"
    assert reg.resolve_override("knobbed", "autodeploy", None) is True
    assert reg.resolve_override("knobbed", "review_gate", True) is False


async def test_goal_lane_dispatch_auto_preps_missing_workspace_from_repo_url(
        goal_lane, monkeypatch, tmp_path):
    """#727 finding 3b (re-pin of #523 P2, moved to the goal lane): an ABSENT
    workspace with a repo_url is auto-cloned at admission (the preflight runs
    before the lane routing), then the dispatch proceeds."""
    import subprocess

    src = tmp_path / "src-repo"
    src.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
    (src / "f.txt").write_text("hi\n")
    subprocess.run(["git", "-C", str(src), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(src), "-c", "user.email=t@t",
                    "-c", "user.name=t", "commit", "-qm", "init"], check=True)

    reg = ProjectRegistry(str(tmp_path / "reg3.db"))
    ws = tmp_path / "clone-here"  # does NOT exist
    reg.create(id="fresh", name="fresh", workspace_dir=str(ws), repo_url=str(src))
    monkeypatch.setattr(_tools._common, "registry", reg)

    await _tools.dispatch_task(
        kind="implement_feature", project_id="fresh", goal="x", issue_ref=3,
    )
    assert (ws / ".git").exists(), "absent workspace auto-cloned before the goal lane"
    (call,) = goal_lane.dispatched
    assert call["workspace_dir"] == str(ws)


async def test_mutating_dispatch_never_reaches_direct_queue_submit(goal_lane):
    """Spec 022 US2 T010: the single-writer exemption is REPEALED by
    construction — every mutating dispatch rides the goal lane (serialized by
    the project hold at the tick), and the direct queue.submit is reachable by
    read-only kinds only."""
    await _tools.dispatch_task(
        kind="implement_feature", project_id=goal_lane.pid, goal="x", issue_ref=1,
    )
    await _tools.fix_bug(
        project_id=goal_lane.pid, description="broken thing", issue_ref=2,
    )
    assert len(goal_lane.dispatched) == 2
    assert goal_lane.calls == [], "no mutating kind may reach queue.submit directly"
