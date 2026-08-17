"""Escape-hatch pins for the container-per-goal → container-per-project flip.

When a target project's workspace contains a ``Dockerfile``, devclaw MUST NOT
spin its own ``devclaw-deploy-<goal_id>`` container on ``achieved`` — the
project's own CI is the single deploy source. Without this escape hatch, five
closeloop goals produced five simultaneous closeloop containers on the VPS
(evidence, 2026-07-01). See
``~/memory/projects/devclaw/proposals/2026-07-01-per-project-runner-not-per-goal.md``.

The escape-hatch signal (Dockerfile at workspace root) is pure mechanism and is
what gets pinned here. How that Dockerfile gets there is engineering-judgment
work an ``implement_feature`` task does per-project — devclaw does NOT ship a
template scaffolder for it (the earlier ``setup_cicd`` MCP tool was removed
after it hardcoded five stack templates and silently misdetected fullstack
apps; per ``plan.md`` §Production-ready criterion C5, CI/Dockerfile shape is
per-project standards work that stays with the code, not universal mechanism).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from devclaw.delivery.deploy import workspace_has_app_surface
from devclaw.goal.models import Goal
from devclaw.goal.store import GoalStore
from devclaw.goal.tick import _auto_deploy, _project_owns_its_deploy


def _make_goal(workspace_dir: str) -> Goal:
    return Goal(
        id="demo-goal",
        objective="ship it",
        cadence="1d",
        engine="devclaw",
        workspace_dir=workspace_dir,
        repo_url=None,
        verify_cmd=None,
        open_pr=True,
        done_when="deployed",
    )


# --------------------------- _project_owns_its_deploy ---------------------------


def test_owns_deploy_returns_true_when_dockerfile_exists(tmp_path):
    (tmp_path / "Dockerfile").write_text("FROM alpine\n")
    assert _project_owns_its_deploy(str(tmp_path)) is True


def test_owns_deploy_returns_false_when_no_dockerfile(tmp_path):
    assert _project_owns_its_deploy(str(tmp_path)) is False


def test_owns_deploy_returns_false_on_missing_workspace():
    assert _project_owns_its_deploy("/nonexistent/path") is False


# --------------------------- _auto_deploy escape hatch ---------------------------


async def test_auto_deploy_skips_when_dockerfile_present(tmp_path):
    """The load-bearing pin: with Dockerfile present, devclaw MUST NOT call
    deploy_project — the whole reason for this flip."""
    (tmp_path / "Dockerfile").write_text("FROM alpine\n")
    goal = _make_goal(str(tmp_path))
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    (goals_dir / goal.id).mkdir()
    store = GoalStore(goals_dir)

    with patch("devclaw.goal.tick._deploy.deploy_project", new_callable=AsyncMock) as mock_deploy:
        suffix = await _auto_deploy(goal.id, goal, store, enabled=True)

    assert suffix == ""
    mock_deploy.assert_not_called()
    log = (goals_dir / goal.id / "log.md").read_text()
    assert "project owns its deploy" in log
    assert "Dockerfile present" in log


async def test_auto_deploy_still_fires_without_dockerfile(tmp_path):
    """Backward-compat pin: projects that haven't migrated (no Dockerfile) still
    get the old auto-deploy — no regression during the migration window."""
    goal = _make_goal(str(tmp_path))
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    (goals_dir / goal.id).mkdir()
    store = GoalStore(goals_dir)

    fake_result = {
        "url": "https://vps.tail.ts.net:8090/",
        "container": "devclaw-deploy-demo-goal",
        "ready": True,
        "tailscale_served": True,
    }

    with patch(
        "devclaw.goal.tick._deploy.deploy_project", new_callable=AsyncMock
    ) as mock_deploy:
        mock_deploy.return_value = fake_result
        suffix = await _auto_deploy(goal.id, goal, store, enabled=True)

    mock_deploy.assert_called_once_with(str(tmp_path), goal.id)
    assert "https://vps.tail.ts.net:8090/" in suffix


async def test_auto_deploy_kill_switch_still_works(tmp_path):
    """The autodeploy kill switch (now the resolved ``enabled=False`` flag —
    a project override or the devclaw-wide autodeploy default, resolved upstream
    in GoalService) takes precedence over both branches: disabled means no
    deploy, no matter the Dockerfile."""
    (tmp_path / "Dockerfile").write_text("FROM alpine\n")  # would-normally-skip
    goal = _make_goal(str(tmp_path))
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    (goals_dir / goal.id).mkdir()
    store = GoalStore(goals_dir)

    with patch("devclaw.goal.tick._deploy.deploy_project", new_callable=AsyncMock) as mock_deploy:
        suffix = await _auto_deploy(goal.id, goal, store, enabled=False)

    assert suffix == ""
    mock_deploy.assert_not_called()
    # And under env-off we don't log a "project owns" note either — the goal's
    # log stays clean of unrelated context.
    log_path = goals_dir / goal.id / "log.md"
    if log_path.exists():
        assert "project owns its deploy" not in log_path.read_text()


# ------------------- workspace_has_app_surface (#554) -------------------
# The deploy-domain instance of the browser gate's app-surface-vs-library
# trigger semantics: a workspace has an app surface iff the preview launcher
# has a branch that can actually SERVE it (FastAPI backend, root ASGI app,
# static frontend). A pure library matches nothing but the file-listing
# fallback — that is NOT an app surface.


def test_app_surface_detected_for_backend_requirements(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    assert workspace_has_app_surface(str(tmp_path)) is True


def test_app_surface_detected_for_frontend_dir(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "index.html").write_text("<html></html>")
    assert workspace_has_app_surface(str(tmp_path)) is True


def test_app_surface_detected_for_root_index_html(tmp_path):
    (tmp_path / "index.html").write_text("<html></html>")
    assert workspace_has_app_surface(str(tmp_path)) is True


def test_app_surface_detected_for_root_asgi_app(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\nuvicorn\n")
    (tmp_path / "main.py").write_text("from fastapi import FastAPI\napp = FastAPI()\n")
    assert workspace_has_app_surface(str(tmp_path)) is True


def test_no_app_surface_for_pure_python_library(tmp_path):
    """The Run-1 (2026-08-16) shape: a single-function Python utility. Nothing
    the launcher could serve — only the file-listing fallback would run."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'util'\n")
    (tmp_path / "util.py").write_text("def helper():\n    return 42\n")
    assert workspace_has_app_surface(str(tmp_path)) is False


def test_no_app_surface_for_requirements_only_utility(tmp_path):
    """requirements.txt alone is not an app — the launcher only serves it if an
    ASGI app module is actually present."""
    (tmp_path / "requirements.txt").write_text("requests\n")
    (tmp_path / "cli.py").write_text("print('hi')\n")
    assert workspace_has_app_surface(str(tmp_path)) is False


def test_no_app_surface_on_missing_workspace():
    assert workspace_has_app_surface("/nonexistent/path") is False


# ------------------- conditional autodeploy default (#554) -------------------
# ``enabled=None`` = nothing pinned anywhere (no project override). The fleet
# default is CONDITIONAL: app surface ⇒ deploy, library ⇒ skip. An explicit
# pin (project ``autodeploy=on``/``off``) always wins over detection.


async def test_autodeploy_defaults_off_for_library_repo(tmp_path):
    """The #554 regression pin: with no explicit autodeploy anywhere
    (enabled=None), a pure-library workspace must NOT get a preview container —
    Run 1 (2026-08-16) left two orphan devclaw-deploy-* containers for a
    single-function Python utility."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'util'\n")
    (tmp_path / "util.py").write_text("def helper():\n    return 42\n")
    goal = _make_goal(str(tmp_path))
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    (goals_dir / goal.id).mkdir()
    store = GoalStore(goals_dir)

    with patch("devclaw.goal.tick._deploy.deploy_project", new_callable=AsyncMock) as mock_deploy:
        suffix = await _auto_deploy(goal.id, goal, store, enabled=None)

    assert suffix == ""
    mock_deploy.assert_not_called()
    log = (goals_dir / goal.id / "log.md").read_text()
    assert "no app surface" in log  # loud skip, never silent


async def test_autodeploy_defaults_on_for_app_surface_repo(tmp_path):
    """The other half of the conditional default: an app-surface repo with no
    explicit setting still auto-deploys — the pre-#554 behavior is preserved
    exactly where a preview container is meaningful."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "requirements.txt").write_text("fastapi\n")
    goal = _make_goal(str(tmp_path))
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    (goals_dir / goal.id).mkdir()
    store = GoalStore(goals_dir)

    fake_result = {
        "url": "https://vps.tail.ts.net:8090/",
        "container": "devclaw-deploy-demo-goal",
        "ready": True,
        "tailscale_served": True,
    }
    with patch("devclaw.goal.tick._deploy.deploy_project", new_callable=AsyncMock) as mock_deploy:
        mock_deploy.return_value = fake_result
        suffix = await _auto_deploy(goal.id, goal, store, enabled=None)

    mock_deploy.assert_called_once_with(str(tmp_path), goal.id)
    assert "https://vps.tail.ts.net:8090/" in suffix


async def test_explicit_autodeploy_registration_still_wins(tmp_path):
    """An explicit pin behaves exactly as before #554: ``enabled=True`` deploys
    a library repo anyway (detection never overrides an operator decision), and
    ``enabled=False`` never deploys an app repo."""
    # library workspace + explicit on → deploys
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "util.py").write_text("def helper():\n    return 42\n")
    goal = _make_goal(str(lib))
    goals_dir = tmp_path / "goals"
    goals_dir.mkdir()
    (goals_dir / goal.id).mkdir()
    store = GoalStore(goals_dir)
    fake_result = {"url": "https://vps.tail.ts.net:8090/", "container": "c", "ready": True,
                   "tailscale_served": True}
    with patch("devclaw.goal.tick._deploy.deploy_project", new_callable=AsyncMock) as mock_deploy:
        mock_deploy.return_value = fake_result
        suffix = await _auto_deploy(goal.id, goal, store, enabled=True)
    mock_deploy.assert_called_once_with(str(lib), goal.id)
    assert "https://vps.tail.ts.net:8090/" in suffix

    # app workspace + explicit off → silent skip
    app = tmp_path / "app"
    (app / "backend").mkdir(parents=True)
    (app / "backend" / "requirements.txt").write_text("fastapi\n")
    goal_off = _make_goal(str(app))
    with patch("devclaw.goal.tick._deploy.deploy_project", new_callable=AsyncMock) as mock_deploy:
        suffix = await _auto_deploy(goal_off.id, goal_off, store, enabled=False)
    assert suffix == ""
    mock_deploy.assert_not_called()
