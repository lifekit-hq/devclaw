"""The suite-wide no-real-docker guard (tests/conftest.py::_block_real_docker).

On 2026-07-14 pytest runs on two hosts each leaked a REAL running container:
a best-effort path swallowed ``Exception`` and a docker-enabled host silently
succeeded. These tests pin the structural fix: any real docker/tailscale
spawn fails the test loudly (a BaseException an ``except Exception`` cannot
eat), while ordinary subprocesses stay usable."""

import asyncio
import sys
from pathlib import Path

import pytest

from devclaw import config as _config
from devclaw.engine import EngineRequest
from devclaw.engine import claude as engine
from devclaw.engine import workspace as ws
from devclaw.procutil import run as _run


def test_suite_guard_blocks_real_docker_invocations():
    with pytest.raises(pytest.fail.Exception, match="BLOCKED.*docker"):
        asyncio.run(_run("docker", "ps", "-a"))


def test_suite_guard_blocks_real_tailscale_invocations():
    with pytest.raises(pytest.fail.Exception, match="BLOCKED.*tailscale"):
        asyncio.run(_run("tailscale", "status", "--json"))


def test_suite_guard_blocks_real_treehouse_leases():
    """The lease seam is synchronous (subprocess.run, not asyncio) and touches a
    pool live sessions share — guarded independently."""
    with pytest.raises(pytest.fail.Exception, match="BLOCKED.*treehouse"):
        ws.goal_checkout_dir(str(Path(__file__).resolve().parents[1]), "g1")


def test_suite_guard_blocks_the_real_claude_behind_the_session_shell(monkeypatch, tmp_path):
    """The session spawns ``sh -c '… exec "$@"' sh claude …`` — the guard must
    see the program the shell execs, not the shell."""
    monkeypatch.setattr(_config, "CLAUDE_BIN", "claude")
    req = EngineRequest(kind="review_repository", workspace_dir=str(tmp_path), goal="g")
    with pytest.raises(pytest.fail.Exception, match="BLOCKED.*cognition"):
        asyncio.run(engine.run_turn(req, "go", resume=None))


def test_suite_guard_lets_non_docker_subprocesses_through():
    """git/gh/python spawns are legitimate (many tests `git init` real repos)."""
    rc, out = asyncio.run(_run(sys.executable, "-c", "print('ok')"))
    assert rc == 0 and out == "ok"
