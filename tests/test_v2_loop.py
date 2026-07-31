"""Named regression tests for the v2 stubborn loop (mvp/loop.py).

Fully stubbed per repo testing rules: no docker, no claude — the cognition
boundary is a bash script injected via DEVCLAW2_CLAUDE_CMD.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

LOOP_PY = Path(__file__).resolve().parents[1] / "mvp" / "loop.py"

_spec = importlib.util.spec_from_file_location("v2_loop", LOOP_PY)
loop = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(loop)


# ---------------------------------------------------------------- fixtures


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=ws, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=ws, check=True)
    (ws / "README.md").write_text("scratch\n")
    subprocess.run(["git", "add", "-A"], cwd=ws, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=ws, check=True)
    return ws


def fake_claude(tmp_path: Path, body: str) -> tuple[str, Path]:
    """Write a fake-claude bash script; returns (DEVCLAW2_CLAUDE_CMD, counter file).

    The counter lives OUTSIDE the workspace so bumping it never counts as
    tree progress. Every script consumes stdin first (the loop writes the
    prompt there)."""
    counter = tmp_path / "count"
    script = tmp_path / "fake_claude.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "cat > /dev/null\n"
        f"c=$(cat {counter} 2>/dev/null || echo 0)\n"
        "c=$((c+1))\n"
        f"echo $c > {counter}\n" + body
    )
    return f"bash {script}", counter


def run_loop(ws: Path, claude_cmd: str, *extra: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["DEVCLAW2_CLAUDE_CMD"] = claude_cmd
    env["DEVCLAW2_BACKOFF_S"] = "0"
    return subprocess.run(
        [sys.executable, str(LOOP_PY), str(ws), "test goal: exercise the loop",
         "--deliver", "commit", "--session-timeout", "60", *extra],
        env=env, text=True, capture_output=True, stdin=subprocess.DEVNULL,
    )


def git_out(ws: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ws, text=True, capture_output=True
    ).stdout


# ---------------------------------------------------------------- loop behavior


def test_loop_iterates_until_selfreported_done_then_commits(workspace, tmp_path):
    cmd, counter = fake_claude(
        tmp_path,
        'if [ "$c" = "1" ]; then\n'
        "  echo hello > feature.txt\n"
        '  git add -A && git commit -q -m "feat: add feature"\n'
        'elif [ "$c" = "2" ]; then\n'
        "  mkdir -p .devclaw2\n"
        '  echo "did the goal" > .devclaw2/DONE.md\n'
        "fi\n",
    )
    proc = run_loop(workspace, cmd, "--strategy", "direct")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert counter.read_text().strip() == "2"
    log = git_out(workspace, "log", "--oneline")
    assert "feat: add feature" in log
    # DONE.md was committed by the finalize step
    assert "DONE.md" in git_out(workspace, "ls-files")
    # goal-grain: work happened on one dedicated branch
    assert git_out(workspace, "branch", "--show-current").strip().startswith("v2/")


def test_no_progress_brake_abandons_with_report_and_never_waits(workspace, tmp_path):
    cmd, counter = fake_claude(tmp_path, "true\n")  # changes nothing, exits 0
    proc = run_loop(workspace, cmd, "--strategy", "direct", "--max-iters", "8")
    assert proc.returncode == 1
    # brake fired after 2 sessions, not the 8-session cap
    assert counter.read_text().strip() == "2"
    report = workspace / ".devclaw2" / "REPORT.md"
    assert report.exists()
    assert "no progress" in report.read_text()
    assert "ABANDONED" in report.read_text()
    # never-block: the report was committed so the morning artifact survives
    assert "REPORT.md" in git_out(workspace, "ls-files")


def test_claude_signal_death_is_transient_and_retried(workspace, tmp_path):
    cmd, counter = fake_claude(
        tmp_path,
        'if [ "$c" = "1" ]; then kill -9 $$; fi\n'
        "mkdir -p .devclaw2\n"
        'echo done > .devclaw2/DONE.md\n',
    )
    proc = run_loop(workspace, cmd, "--strategy", "direct")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "transient" in proc.stdout
    assert counter.read_text().strip() == "2"


def test_verify_gate_fails_closed_and_feeds_next_session(workspace, tmp_path):
    # agent claims done immediately; verify only passes once fixed.txt exists,
    # which the fake agent only creates after seeing a failed-verify session
    cmd, counter = fake_claude(
        tmp_path,
        "mkdir -p .devclaw2\n"
        'echo done > .devclaw2/DONE.md\n'
        'if [ "$c" = "2" ]; then echo ok > fixed.txt; fi\n',
    )
    proc = run_loop(
        workspace, cmd, "--strategy", "direct", "--verify", "test -f fixed.txt"
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert counter.read_text().strip() == "2"


def test_dirty_workspace_is_refused_loudly(workspace, tmp_path):
    (workspace / "dirty.txt").write_text("x")
    cmd, _ = fake_claude(tmp_path, "true\n")
    proc = run_loop(workspace, cmd)
    assert proc.returncode == 2
    assert "uncommitted" in proc.stdout


# ---------------------------------------------------------------- prompts

# Prompt-content tests assert presence AND absence (repo cognition-prompts rule).


def test_plan_first_prompt_mentions_plan_and_done_markers():
    p = loop.build_prompt("g", "plan-first", 1, 10, "b")
    assert "PLAN.md" in p
    assert "DONE.md" in p
    assert "session 1 of 10" in p


def test_direct_prompt_has_no_plan_marker():
    p = loop.build_prompt("g", "direct", 1, 10, "b")
    assert "PLAN.md" not in p
    assert "DONE.md" in p


def test_replan_prompt_revises_plan():
    p = loop.build_prompt("g", "replan", 3, 10, "b")
    assert "revise" in p
    assert "PLAN.md" in p


def test_custom_strategy_file_is_used_verbatim():
    custom = "MY CUSTOM STRATEGY TEXT 12345"
    p = loop.build_prompt("g", "plan-first", 1, 10, "b", custom_strategy_text=custom)
    assert custom in p
    assert "PLAN.md" not in p  # built-in body must NOT leak alongside custom


def test_verify_section_present_only_when_configured():
    with_tail = loop.build_prompt(
        "g", "direct", 1, 10, "b", verify_cmd="pytest -q", verify_tail="boom"
    )
    assert "pytest -q" in with_tail
    assert "boom" in with_tail
    assert "FAILED" in with_tail
    no_tail = loop.build_prompt("g", "direct", 1, 10, "b", verify_cmd="pytest -q")
    assert "FAILED" not in no_tail
    without = loop.build_prompt("g", "direct", 1, 10, "b")
    assert "exits 0" not in without


# ---------------------------------------------------------------- invariants


def test_oauth_only_metered_keys_are_stripped_from_child_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-live-oops")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "tok-oops")
    env = loop.clean_env()
    assert "ANTHROPIC_API_KEY" not in env
    assert "ANTHROPIC_AUTH_TOKEN" not in env
