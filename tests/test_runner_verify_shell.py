"""The verify gate cannot be talked out of a failure by a pipe.

`sh -c` reports the LAST pipeline stage's status, so the extremely natural
`pytest -q | tail -20` exits 0 on a red suite. A devclaw self-fix settled
`done` on exactly that (task b9e3c3af, 2026-08-20) — `verify.passed: True,
exit_code: 0` with a FAILURES block in the gate's own captured output. A gate
that can be masked is fail-OPEN, which the hardening philosophy forbids;
pipefail removes the possibility for every gate command, not just that one.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_RUNNER_PATH = Path(__file__).resolve().parents[1] / "runner" / "runner.py"


@pytest.fixture(scope="module")
def runner():
    spec = importlib.util.spec_from_file_location("devclaw_runner_verify_shell", _RUNNER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # top-level import; the runner is stdlib-only (spec 011)
    return mod


def test_verify_gate_fails_when_a_pipe_would_mask_a_nonzero_exit(runner, tmp_path):
    verdict = runner._run_verify("exit 1 | tail -20", str(tmp_path))
    assert verdict["ran"] is True
    assert verdict["passed"] is False, "a masked failure must not pass the gate"
    assert verdict["exit_code"] != 0


def test_verify_gate_still_passes_a_green_piped_command(runner, tmp_path):
    verdict = runner._run_verify("echo green | tail -1", str(tmp_path))
    assert verdict["passed"] is True
    assert "green" in verdict["output"]


def test_verify_gate_still_runs_a_full_shell_command_line(runner, tmp_path):
    # The gate takes command LINES ("npm run build && npm run test:ci"), not
    # argv — the shell has to stay a shell.
    verdict = runner._run_verify("echo one && echo two", str(tmp_path))
    assert verdict["passed"] is True
    assert "one" in verdict["output"] and "two" in verdict["output"]


def test_verify_gate_reports_a_missing_command_as_a_failed_gate(runner, tmp_path):
    verdict = runner._run_verify("definitely-not-a-command", str(tmp_path))
    assert verdict["ran"] is True and verdict["passed"] is False
