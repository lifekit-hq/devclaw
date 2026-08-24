"""Host engine — run the worker runner directly on the host, with NO docker sandbox.

``DEVCLAW_ENGINE=host`` wires this in place of the sandcastle (docker) engine.
It runs ``runner/runner.py`` as a host subprocess in the task's
workspace, using the host's ``claude`` + ``~/.claude`` session.

⚠ NO ISOLATION. The agent runs as your user with full filesystem access — the
whole point of the sandcastle engine is to prevent exactly that. Use the host
engine only where docker isn't available and the risk is acceptable: local dev,
CI, and live validation of small throwaway builds. Production uses the docker
sandbox.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from . import EngineRequest, EngineResult
from .runner_io import STREAM_LINE_LIMIT, consume_runner_output
from .. import config as _config
from ..git_identity import git_identity_env

#: The REPOSITORY ROOT — ``runner/`` and its skill bundle are siblings of the
#: ``devclaw/`` package, not children of it. This was ``parents[1]`` (the
#: package dir), so every path derived from it pointed one level short and
#: silently missed: ``RUNNER_PY`` resolved to ``devclaw/runner/runner.py``,
#: which has never existed, so host mode only ever ran with an explicit
#: ``DEVCLAW_RUNNER_PY``. Nothing caught it because nothing asserted the
#: default resolves — see ``tests/test_host_engine_paths.py``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
# the in-sandbox runner, run here on the host
RUNNER_PY = (
    _config.RUNNER_PY_OVERRIDE
    if _config.RUNNER_PY_OVERRIDE is not None
    else str(_REPO_ROOT / "runner" / "runner.py")
)
#: The ONE home for worker-kind instructions (#613). The sandbox bakes
#: ``runner/skills/`` to /opt/devclaw/skills; on the host nothing mounts it, so
#: point the runner at the in-repo source it was baked from. Without this the
#: host engine finds no skills at all and ``_wrap_goal`` raises — which is the
#: correct loud failure, but the wiring is the fix, not the fallback text that
#: used to paper over it.
SKILLS_DIR = (
    _config.SKILLS_DIR_OVERRIDE
    if _config.SKILLS_DIR_OVERRIDE is not None
    else str(_REPO_ROOT / "runner" / "skills")
)
HOOKS_DIR = (
    _config.HOOKS_DIR_OVERRIDE
    if _config.HOOKS_DIR_OVERRIDE is not None
    else str(_REPO_ROOT / "runner" / "hooks")
)
# the runner is stdlib-only (spec 011); a runner venv is still preferred if
# present, else this interpreter
_RUNNER_VENV = _REPO_ROOT / "runner" / ".venv" / "bin" / "python"
RUNNER_PYTHON = _config.RUNNER_PYTHON_OVERRIDE or (
    str(_RUNNER_VENV) if _RUNNER_VENV.exists() else sys.executable
)


def _strip_api_keys(env: dict[str, str]) -> dict[str, str]:
    clean = dict(env)
    clean.pop("ANTHROPIC_API_KEY", None)
    clean.pop("ANTHROPIC_AUTH_TOKEN", None)
    return clean


def _runner_env() -> dict[str, str]:
    """The host runner's process env: the host env minus API keys (OAuth-only
    invariant), plus devclaw's pinned git identity so the agent's commits are
    authored by devclaw on this engine exactly as in the sandbox."""
    return {
        **_strip_api_keys(dict(os.environ)),
        **git_identity_env(),
        "DEVCLAW_SKILLS_DIR": SKILLS_DIR,
        "DEVCLAW_HOOKS_DIR": HOOKS_DIR,
    }


async def run_host(req: EngineRequest) -> EngineResult:
    """Run one task by invoking the worker runner on the host (no container).
    The workspace path is used as-is (no bind-mount / no path translation)."""
    Path(req.workspace_dir).mkdir(parents=True, exist_ok=True)
    body: dict = {
        "kind": req.kind,
        "workspace_dir": req.workspace_dir,
        "goal": req.goal,
        # verify gate runs on the host after the agent finishes (host toolchain).
        "verify_cmd": req.verify_cmd,
    }
    if req.validation is not None:
        body["validation"] = req.validation
    payload = json.dumps(body)
    try:
        proc = await asyncio.create_subprocess_exec(
            RUNNER_PYTHON,
            RUNNER_PY,
            payload,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=STREAM_LINE_LIMIT,  # event lines can exceed asyncio's 64 KiB default
            env=_runner_env(),
        )
    except OSError as exc:
        return {
            "status": "error",
            "error": f"failed to spawn host runner ({RUNNER_PYTHON} {RUNNER_PY}): {exc}",
        }
    return await consume_runner_output(proc, req.on_event, label="host-runner")
