"""The suite-wide no-real-docker guard (tests/conftest.py::_block_real_docker).

On 2026-07-14 pytest runs on two hosts each leaked a REAL running container:
a best-effort path swallowed ``Exception`` and a docker-enabled host silently
succeeded. These tests pin the structural fix: any real docker/tailscale
spawn fails the test loudly (a BaseException an ``except Exception`` cannot
eat), while ordinary subprocesses stay usable."""

import asyncio
import sys

import pytest

from devclaw.engine import sandcastle as sc
from devclaw.procutil import run as _run


def test_suite_guard_blocks_real_docker_invocations():
    with pytest.raises(pytest.fail.Exception, match="BLOCKED.*docker"):
        asyncio.run(_run("docker", "ps", "-a"))


def test_suite_guard_blocks_real_tailscale_invocations():
    with pytest.raises(pytest.fail.Exception, match="BLOCKED.*tailscale"):
        asyncio.run(_run("tailscale", "status", "--json"))


def test_suite_guard_blocks_sandcastle_sync_docker_seam():
    """The sweep's synchronous seam is a separate escape hatch (subprocess.run,
    not asyncio) — guarded independently."""
    with pytest.raises(pytest.fail.Exception, match="BLOCKED.*_docker_run_sync"):
        sc._docker_run_sync(["ps", "--filter", "label=devclaw.sandbox=1"])


def test_suite_guard_lets_non_docker_subprocesses_through():
    """git/gh/python spawns are legitimate (many tests `git init` real repos)."""
    rc, out = asyncio.run(_run(sys.executable, "-c", "print('ok')"))
    assert rc == 0 and out == "ok"
