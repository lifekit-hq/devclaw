"""Runner-through-fake-agent tests (spec 011, T007/T009/T010).

Runs ``runner/runner.py`` as a real subprocess driving the scripted
fake ACP agent via the agent-command seam — the executable proof that the
swap holds the frozen wire contract (contracts/runner-host-wire.md) and that
the executor is swappable with zero runner-code change. No docker, no claude.
"""

import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = _ROOT / "runner" / "runner.py"
_FAKE_AGENT = Path(__file__).resolve().parent / "acp_fake_agent.py"


def _fake_cmd(script: str) -> str:
    return f"{shlex.quote(sys.executable)} {shlex.quote(str(_FAKE_AGENT))} --script {script}"


def _base_env(tmp_path) -> dict:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    # Deliberately minimal (hermeticity), but the skill bundle is NOT optional:
    # production always has one, and since #613 a runner with no skills refuses
    # to brief the worker at all. Point at the in-repo source the image bakes.
    skills = Path(__file__).resolve().parents[1] / "runner" / "skills"
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        "DEVCLAW_SKILLS_DIR": str(skills),
    }


def _run_runner(tmp_path, script=None, req_extra=None, env_extra=None, timeout=60):
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    req = {
        "kind": "implement_feature",
        "goal": "do the thing",
        "workspace_dir": str(workspace),
        "task_id": "t-acp-1",
    }
    if script is not None:
        req["acp_command"] = _fake_cmd(script)
    if req_extra:
        req.update(req_extra)
    env = _base_env(tmp_path)
    if env_extra:
        env.update(env_extra)
    proc = subprocess.run(
        [sys.executable, str(_RUNNER), json.dumps(req)],
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    events, result = [], None
    for line in proc.stdout.splitlines():
        if line.startswith("event: "):
            events.append(json.loads(line[len("event: "):]))
        elif line.startswith("result: "):
            assert result is None, "more than one result: line"
            result = json.loads(line[len("result: "):])
    assert result is not None, f"no result line; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    return proc, events, result, workspace


def test_ok_run_emits_contract_result_and_events(tmp_path):
    proc, events, result, _ = _run_runner(tmp_path, "ok")
    assert proc.returncode == 0
    assert result["status"] == "ok"
    # agent_output is the agent's OWN final message (#570 semantics).
    assert result["agent_output"].startswith("All done.")
    assert result["repo_notes"] == "fake repo, tests are fast."
    types = [e["type"] for e in events]
    assert "MessageEvent" in types and "ACPToolCallEvent" in types
    # No usage report from this agent → the field is declared-absent (D6).
    assert "usage" not in result


def test_blocked_selfreport_short_circuits_before_verify(tmp_path):
    proc, events, result, workspace = _run_runner(
        tmp_path, "blocked", req_extra={"verify_cmd": "echo should-not-run"}
    )
    assert result["status"] == "blocked"
    assert result["reason"].startswith("the task needs a credential")
    # Fail-closed short-circuit: the verify gate never ran.
    assert "verify" not in result
    assert all(e["type"] != "VerifyResult" for e in events)


def test_rate_limit_is_classified_with_retry_after(tmp_path):
    proc, _, result, _ = _run_runner(tmp_path, "rate_limit")
    assert proc.returncode == 1
    assert result["status"] == "rate_limited"
    assert "usage limit" in result["error"]
    assert result["retry_after"] == 30 * 60


def test_malformed_agent_frame_is_loud_error(tmp_path):
    proc, _, result, _ = _run_runner(tmp_path, "malformed")
    assert proc.returncode == 1
    assert result["status"] == "error"
    assert "malformed frame" in result["error"]


def test_refusal_is_a_failed_task_with_agent_words(tmp_path):
    proc, _, result, _ = _run_runner(tmp_path, "refusal")
    assert proc.returncode == 1
    assert result["status"] == "error"
    assert "refused the task" in result["error"]
    assert "can't help" in result["error"]


def test_verify_gate_still_runs_and_reports(tmp_path):
    _, events, result, _ = _run_runner(
        tmp_path, "ok", req_extra={"verify_cmd": "echo verify-ran"}
    )
    assert result["status"] == "ok"
    assert result["verify"]["passed"] is True
    assert result["verify"]["exit_code"] == 0
    verify_events = [e for e in events if e["type"] == "VerifyResult"]
    assert len(verify_events) == 1 and verify_events[0]["payload"]["passed"] is True


def test_runner_writes_no_vendor_harness_config_into_workspace(tmp_path):
    """Named regression (FR-004): the workspace stays vendor-neutral — no
    agent-brand settings, native skill dirs, or hook manifests are written by
    the runner. On a clean fixture workspace with no verify step, the runner
    writes NOTHING at all."""
    _, _, result, workspace = _run_runner(tmp_path, "ok")
    assert result["status"] == "ok"
    leftovers = sorted(p.name for p in workspace.iterdir())
    assert leftovers == [], f"runner polluted the workspace: {leftovers}"


def test_agent_command_seam_swaps_executor_without_code_change(tmp_path):
    """Named regression (FR-003): the executor is selected purely by the
    command seam — env `DEVCLAW_ACP_COMMAND` works, and the task payload wins
    over the env — with byte-identical runner code either way."""
    # Env-selected executor.
    _, _, result_env, _ = _run_runner(
        tmp_path, script=None, env_extra={"DEVCLAW_ACP_COMMAND": _fake_cmd("ok")}
    )
    assert result_env["status"] == "ok"
    # Payload beats env: env says blocked, payload says ok → ok wins.
    _, _, result_payload, _ = _run_runner(
        tmp_path, script="ok", env_extra={"DEVCLAW_ACP_COMMAND": _fake_cmd("blocked")}
    )
    assert result_payload["status"] == "ok"


def test_api_key_in_env_is_refused_before_any_agent_spawn(tmp_path):
    """Named regression (FR-005 / Principle I): a stray metered credential
    refuses the whole run — it can never reach the agent."""
    proc, events, result, _ = _run_runner(
        tmp_path, "ok", env_extra={"ANTHROPIC_API_KEY": "sk-ant-leaked"}
    )
    assert result["status"] == "error"
    assert "ANTHROPIC_API_KEY" in result["error"]
    assert events == []  # refused before anything ran


def test_client_env_allowlist_never_leaks_key_to_agent(tmp_path, monkeypatch):
    """Belt-and-suspenders below the refusal: even with a key in the runner
    process env, the AcpClient allowlist env keeps it from the agent (the
    fake agent reports LEAKED-API-KEY if it ever sees one)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-leaked")
    spec = importlib.util.spec_from_file_location(
        "acp_client_allowlist_test", _ROOT / "runner" / "acp_client.py"
    )
    acp = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(acp)
    client = acp.AcpClient(
        shlex.split(_fake_cmd("ok")),
        {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")},
    )
    try:
        outcome = client.run(str(tmp_path), "do the thing")
    finally:
        client.close()
    assert outcome.last_agent_message != "LEAKED-API-KEY"
    assert outcome.last_agent_message.startswith("All done.")
