"""The OOM-killer shield on runner-spawned workloads (spec 020 US2).

The runner/agent/workload share one cgroup; the container runs non-root, so
scores can only be RAISED — every workload process marks ITSELF as the
preferred victim pre-exec, leaving the supervisor processes at the default.
These pin (a) the preexec wiring on all three spawn seams, (b) happy-path
outputs staying byte-identical, and (c) the agent-side BASH_ENV entry being
keyed on the baked script's existence (the sandbox image is the only place
it exists — see also test_runner_acp.py's echo_bash_env case).
"""

import importlib.util
import subprocess
from pathlib import Path

import pytest

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_shield", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _capture_run(calls):
    def fake_run(argv, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")
    return fake_run


def test_verify_hook_and_mise_spawns_carry_the_self_raise_preexec(runner, monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(runner.subprocess, "run", _capture_run(calls))

    runner._run_verify("true", str(tmp_path))
    hook = tmp_path / "h.sh"
    hook.write_text("#!/bin/bash\ntrue\n")
    runner._run_one_hook(str(hook), ())
    runner._mise_run(["--version"], str(tmp_path), timeout=5)

    assert len(calls) == 3
    for kwargs in calls:
        assert kwargs.get("preexec_fn") is runner._raise_own_oom_score


def test_shield_is_inert_on_the_happy_path(runner, tmp_path):
    # Real spawn, real preexec: the verify verdict shape and output are
    # byte-identical to a shieldless run — the shield must never be the
    # reason a workload behaves differently.
    verdict = runner._run_verify("echo shield-inert", str(tmp_path))
    assert verdict["ran"] is True and verdict["passed"] is True
    assert verdict["exit_code"] == 0
    assert "shield-inert" in verdict["output"]


def test_self_raise_swallows_every_failure(runner, monkeypatch):
    # The preexec must never be the reason a workload fails to start —
    # an unwritable /proc entry degrades to "no shield", silently.
    import builtins
    real_open = builtins.open

    def deny(path, *a, **k):
        if path == "/proc/self/oom_score_adj":
            raise OSError("denied")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", deny)
    runner._raise_own_oom_score()  # must not raise


def test_bash_env_entry_is_keyed_on_the_baked_script(runner):
    # Structural pin (the positive arm is only reachable inside the sandbox
    # image): the allowlist line exists, is guarded by the script's
    # existence, and points at the constant the Dockerfile bakes.
    src = _RUNNER_PATH.read_text()
    assert runner._OOM_SHIELD_SCRIPT == "/opt/devclaw/oom-shield.sh"
    assert 'if os.path.exists(_OOM_SHIELD_SCRIPT):' in src
    assert 'acp_env["BASH_ENV"] = _OOM_SHIELD_SCRIPT' in src
