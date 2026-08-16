"""Speckit adopt-or-install onboarding — US2 named regression (spec 008,
SC-001/SC-004).

Every repo devclaw works uses speckit. The `onboard` MCP tool decides on the
**committed** `.specify/` directory:

- committed `.specify/` present  → ADOPT: no `PLAN.md` written, no scaffolding PR.
- absent                         → INSTALL: a reviewable PR carrying the
                                   `.specify/` scaffold, ZERO direct commits to
                                   the default branch.

And feature dispatch is BLOCKED for a repo whose install PR is still open (no
half-installed execution — spec Edge Case).

Fully stubbed: real local git (no network), and the delivery PR-open / gh-list
calls are faked (the suite never touches a real remote).
"""

from __future__ import annotations

import json
import subprocess

import pytest

from devclaw import speckit_setup as _speckit
from devclaw.project_registry import ProjectRegistry
from devclaw.server import tools as _tools
from devclaw.speckit_setup import INSTALL_BRANCH


def _git(path, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "--allow-empty", "-q", "-m", "init")


def _commit_all(path, msg) -> None:
    _git(path, "add", "-A")
    _git(path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)


class _FakeQueue:
    """Records queue.submit calls so we can assert whether a task was queued."""

    def __init__(self):
        self.submitted: list[dict] = []

    def submit(self, **kwargs) -> str:
        self.submitted.append(kwargs)
        return f"task-{len(self.submitted)}"


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(str(tmp_path / "reg.db"))


# ---- (a) ADOPT: committed .specify/ → no PLAN.md, no PR --------------------


async def test_onboard_adopts_a_repo_with_committed_specify_no_plan_no_pr(
    tmp_path, registry, monkeypatch
):
    ws = tmp_path / "adopt-repo"
    _init_repo(ws)
    # A committed .specify/ is the adopt signal.
    (ws / ".specify" / "memory").mkdir(parents=True)
    (ws / ".specify" / "memory" / "constitution.md").write_text("# Constitution\n")
    _commit_all(ws, "add speckit")

    registry.create(id="adopt", name="adopt", workspace_dir=str(ws))
    monkeypatch.setattr(_tools, "registry", registry)
    fake_q = _FakeQueue()
    monkeypatch.setattr(_tools, "queue", fake_q)

    installed = {"called": False}

    async def _no_install(*a, **k):
        installed["called"] = True
        return {}

    monkeypatch.setattr(_speckit, "install_speckit_pr", _no_install)

    out = json.loads(await _tools.onboard(project_id="adopt"))

    assert out["speckit"] == "adopted"
    # No install PR path taken.
    assert installed["called"] is False
    # SC-001: onboard writes NO PLAN.md, in either branch.
    assert not (ws / "PLAN.md").exists()
    # Adopt still runs the comprehension-doc onboarding task.
    assert fake_q.submitted and fake_q.submitted[0]["kind"] == "onboard"
    # No commit was added to the default branch by onboard.
    assert _git(ws, "rev-list", "--count", "HEAD") == "2"  # init + add speckit


# ---- (b) INSTALL: bare repo → reviewable PR, zero silent commits -----------


async def test_onboard_installs_speckit_via_reviewable_pr_no_silent_commit(
    tmp_path, registry, monkeypatch
):
    ws = tmp_path / "bare-repo"
    _init_repo(ws)  # no .specify/

    default_branch = _git(ws, "rev-parse", "--abbrev-ref", "HEAD")
    default_head_before = _git(ws, "rev-parse", "HEAD")
    commits_before = _git(ws, "rev-list", "--count", default_branch)

    registry.create(id="bare", name="bare", workspace_dir=str(ws))
    monkeypatch.setattr(_tools, "registry", registry)
    fake_q = _FakeQueue()
    monkeypatch.setattr(_tools, "queue", fake_q)

    calls: list[dict] = []

    async def fake_deliver(**kwargs):
        calls.append(kwargs)
        return {"delivered": True, "pushed": True, "branch": kwargs.get("target_branch"),
                "pr_url": "https://github.com/x/y/pull/1", "error": None}

    # Patch the delivery call the installer reuses (never a real gh/push).
    monkeypatch.setattr(_speckit, "deliver_change", fake_deliver)

    out = json.loads(await _tools.onboard(project_id="bare"))

    assert out["speckit"] == "install_pr"
    assert out["pr_url"] == "https://github.com/x/y/pull/1"

    # A reviewable PR was opened through the delivery path, on the install branch.
    assert len(calls) == 1
    assert calls[0]["target_branch"] == INSTALL_BRANCH

    # The .specify/ scaffold was generated in the workspace.
    assert (ws / ".specify" / "templates").is_dir()
    assert (ws / ".specify" / "scripts").is_dir()
    assert (ws / ".specify" / "memory" / "constitution.md").is_file()

    # SC-004: ZERO direct commits to the default branch — the default branch HEAD
    # and commit count are unchanged; the scaffold rode a side branch/PR.
    assert _git(ws, "rev-parse", default_branch) == default_head_before
    assert _git(ws, "rev-list", "--count", default_branch) == commits_before
    # SC-001: still no PLAN.md.
    assert not (ws / "PLAN.md").exists()
    # A bare repo isn't speckit-ready yet, so no comprehension-doc task is queued.
    assert fake_q.submitted == []


async def test_scaffold_specify_copies_reusable_parts_not_devclaw_constitution(tmp_path):
    ws = tmp_path / "scaffold-target"
    ws.mkdir()
    created = _speckit.scaffold_specify(str(ws))
    assert ".specify/templates/" in created
    assert (ws / ".specify" / "templates" / "spec-template.md").is_file()
    # The seeded constitution is the placeholder TEMPLATE, not devclaw's filled one.
    text = (ws / ".specify" / "memory" / "constitution.md").read_text()
    assert "devclaw Constitution" not in text


# ---- (c) BLOCK feature dispatch while the install PR is open ---------------


async def test_feature_dispatch_blocked_while_install_pr_open(
    tmp_path, registry, monkeypatch
):
    ws = tmp_path / "pending-repo"
    _init_repo(ws)  # bare, no committed .specify/

    registry.create(id="pending", name="pending", workspace_dir=str(ws))
    monkeypatch.setattr(_tools, "registry", registry)
    fake_q = _FakeQueue()
    monkeypatch.setattr(_tools, "queue", fake_q)

    async def _open_pr(workspace_dir):
        return "https://github.com/x/y/pull/7"

    monkeypatch.setattr(_speckit, "open_install_pr", _open_pr)

    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as exc:
        await _tools.dispatch_task(
            kind="implement_feature", project_id="pending", goal="add a feature"
        )
    msg = str(exc.value)
    assert "install" in msg.lower()
    assert "pull/7" in msg  # actionable: names the exact PR
    # Blocked BEFORE any task was claimed.
    assert fake_q.submitted == []


async def test_review_repository_not_blocked_by_open_install_pr(
    tmp_path, registry, monkeypatch
):
    """Read-only review is not feature work — an open install PR must not block it."""
    ws = tmp_path / "review-repo"
    _init_repo(ws)

    registry.create(id="rev", name="rev", workspace_dir=str(ws))
    monkeypatch.setattr(_tools, "registry", registry)
    fake_q = _FakeQueue()
    monkeypatch.setattr(_tools, "queue", fake_q)

    async def _open_pr(workspace_dir):
        return "https://github.com/x/y/pull/7"

    monkeypatch.setattr(_speckit, "open_install_pr", _open_pr)

    out = json.loads(await _tools.dispatch_task(
        kind="review_repository", project_id="rev", goal="look around"
    ))
    assert out["status"] == "pending"
    assert fake_q.submitted and fake_q.submitted[0]["kind"] == "review_repository"


async def test_feature_dispatch_allowed_for_legacy_repo_without_install_pr(
    tmp_path, registry, monkeypatch
):
    """A legacy PLAN.md repo (no committed .specify/, no open install PR, no local
    install branch) is NOT blocked — the transition dual-read (D4) keeps
    un-migrated repos working."""
    ws = tmp_path / "legacy-repo"
    _init_repo(ws)

    registry.create(id="legacy", name="legacy", workspace_dir=str(ws))
    monkeypatch.setattr(_tools, "registry", registry)
    fake_q = _FakeQueue()
    monkeypatch.setattr(_tools, "queue", fake_q)

    async def _no_pr(workspace_dir):
        return None

    monkeypatch.setattr(_speckit, "open_install_pr", _no_pr)

    out = json.loads(await _tools.dispatch_task(
        kind="implement_feature", project_id="legacy", goal="do a thing"
    ))
    assert out["status"] == "pending"
    assert fake_q.submitted and fake_q.submitted[0]["kind"] == "implement_feature"


# ---- (d) F7: fail CLOSED on gh-uncertainty when a pending install exists ----


async def test_feature_dispatch_blocked_when_gh_unverifiable_and_install_branch_exists(
    tmp_path, registry, monkeypatch
):
    """open_install_pr returning None can mean 'gh could not tell'. With concrete
    local evidence of a pending install (the install branch) AND gh unavailable,
    the gate fails CLOSED rather than silently admitting half-installed work."""
    ws = tmp_path / "uncertain-repo"
    _init_repo(ws)  # no committed .specify/

    registry.create(id="unc", name="unc", workspace_dir=str(ws))
    monkeypatch.setattr(_tools, "registry", registry)
    fake_q = _FakeQueue()
    monkeypatch.setattr(_tools, "queue", fake_q)

    async def _no_pr(workspace_dir):
        return None  # gh returned nothing — but was it 'no PR' or 'gh down'?

    async def _branch_exists(workspace_dir):
        return True  # an install was scaffolded here

    async def _gh_down(workspace_dir):
        return True

    monkeypatch.setattr(_speckit, "open_install_pr", _no_pr)
    monkeypatch.setattr(_speckit, "local_install_branch_exists", _branch_exists)
    monkeypatch.setattr(_speckit, "gh_unavailable", _gh_down)

    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError) as exc:
        await _tools.dispatch_task(
            kind="implement_feature", project_id="unc", goal="add a feature"
        )
    assert "unverifiable" in str(exc.value).lower()
    assert fake_q.submitted == []


# ---- (e) F6: install never strands the workspace on the install branch ------


async def test_install_restores_the_original_branch(tmp_path, monkeypatch):
    ws = tmp_path / "install-restore"
    _init_repo(ws)  # no .specify/
    default_branch = _git(ws, "rev-parse", "--abbrev-ref", "HEAD")

    async def fake_deliver(**kwargs):
        return {"delivered": True, "pushed": True, "branch": kwargs.get("target_branch"),
                "pr_url": "https://github.com/x/y/pull/1", "error": None}

    monkeypatch.setattr(_speckit, "deliver_change", fake_deliver)

    await _speckit.install_speckit_pr(str(ws), project_id="p")

    # HEAD is back on the default branch — a later dispatch reading HEAD's tree
    # won't mistake the install branch's committed .specify/ for 'adopted'.
    assert _git(ws, "rev-parse", "--abbrev-ref", "HEAD") == default_branch


# ---- (f) F3: the durable-goal (heartbeat) hold predicate --------------------


async def test_feature_block_reason_holds_only_a_repo_mid_install(tmp_path, monkeypatch):
    ws = tmp_path / "gp"
    _init_repo(ws)

    # No local install branch → an ordinary/legacy repo is never held (and gh is
    # never probed — cheap).
    async def _boom(*a, **k):  # pragma: no cover - must not run for a plain repo
        raise AssertionError("gh probed for a repo with no install branch")

    monkeypatch.setattr(_speckit, "open_install_pr", _boom)
    assert await _speckit.feature_block_reason(str(ws)) is None

    # A real local install branch + an open PR → held with an actionable reason.
    _git(ws, "branch", INSTALL_BRANCH)

    async def _open(workspace_dir):
        return "https://github.com/x/y/pull/9"

    monkeypatch.setattr(_speckit, "open_install_pr", _open)
    reason = await _speckit.feature_block_reason(str(ws))
    assert reason and "pull/9" in reason


async def test_feature_block_reason_clears_once_speckit_committed(tmp_path, monkeypatch):
    ws = tmp_path / "gp2"
    _init_repo(ws)
    _git(ws, "branch", INSTALL_BRANCH)  # stale local install branch lingers
    # The install merged and .specify/ landed here → good to go, no hold.
    (ws / ".specify" / "memory").mkdir(parents=True)
    (ws / ".specify" / "memory" / "constitution.md").write_text("# c\n")
    _commit_all(ws, "adopt speckit")

    async def _boom(*a, **k):  # pragma: no cover - must not run once committed
        raise AssertionError("gh probed though .specify/ is committed")

    monkeypatch.setattr(_speckit, "open_install_pr", _boom)
    assert await _speckit.feature_block_reason(str(ws)) is None
