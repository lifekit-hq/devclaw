"""Protocol-level tests for the runner's ACP client (spec 011, T004).

Drives ``runner/acp_client.py`` against the scripted fake agent
subprocess — no docker, no claude, no SDK. These are the tests that make the
agent-drive seam continuously verified instead of a doc claim.
"""

import importlib.util
import os
import sys
import time
from pathlib import Path

import pytest

_CLIENT_PATH = (
    Path(__file__).resolve().parents[1] / "runner" / "acp_client.py"
)
_FAKE_AGENT = Path(__file__).resolve().parent / "acp_fake_agent.py"


@pytest.fixture(scope="module")
def acp():
    spec = importlib.util.spec_from_file_location("acp_client_under_test", _CLIENT_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _agent_argv(script: str) -> list[str]:
    return [sys.executable, str(_FAKE_AGENT), "--script", script]


def _agent_env() -> dict:
    return {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "")}


def _run(acp, script: str, tmp_path, **kwargs):
    events: list[dict] = []
    client = acp.AcpClient(
        _agent_argv(script), _agent_env(), on_event=events.append, **kwargs
    )
    try:
        outcome = client.run(str(tmp_path), "do the thing")
    finally:
        client.close()
    return outcome, events, client


def test_ok_turn_returns_outcome_and_streams_events(acp, tmp_path):
    outcome, events, _ = _run(acp, "ok", tmp_path)
    assert outcome.stop_reason == "end_turn"
    # The LAST complete message is the hand-back (REPO NOTES parse feeds on it).
    assert outcome.last_agent_message.startswith("All done.")
    assert "REPO NOTES:" in outcome.last_agent_message
    types = [e["type"] for e in events]
    assert "MessageEvent" in types
    assert types.count("ACPToolCallEvent") == 2
    # Event payloads carry the host-classifier shapes (contracts/runner-host-wire.md).
    msg_events = [e for e in events if e["type"] == "MessageEvent" and not e["payload"].get("thought")]
    assert msg_events[0]["payload"]["llm_message"]["content"][0]["type"] == "text"
    tool = next(e for e in events if e["type"] == "ACPToolCallEvent")
    assert tool["payload"]["tool_call_id"] == "tc-1"
    assert tool["payload"]["title"] == "Read README.md"
    assert tool["payload"]["is_error"] is False
    # Thought chunks flow as thought-marked messages, never as the hand-back.
    thoughts = [e for e in events if e["payload"].get("thought")]
    assert thoughts and "planning" in thoughts[0]["payload"]["llm_message"]["content"][0]["text"]
    # No usage report in this script → declared-absent.
    assert outcome.usage is None


def test_permission_request_auto_grants_allow_always(acp, tmp_path):
    outcome, events, _ = _run(acp, "permission", tmp_path)
    assert outcome.last_agent_message == "PERMISSION-OK"
    grant = next(e for e in events if e["type"] == "PermissionRequestEvent")
    assert grant["payload"]["chosen"] == "opt-always"


def test_unadvertised_client_method_gets_method_not_found(acp, tmp_path):
    outcome, _, _ = _run(acp, "client_call", tmp_path)
    assert outcome.last_agent_message == "FS-DENIED-OK"


def test_idle_timeout_kills_hung_agent_and_raises(acp, tmp_path):
    events: list[dict] = []
    client = acp.AcpClient(
        _agent_argv("hang"), _agent_env(), idle_timeout_s=1, on_event=events.append
    )
    start = time.monotonic()
    with pytest.raises(acp.AcpError, match="idle timeout"):
        client.run(str(tmp_path), "do the thing")
    assert time.monotonic() - start < 30
    # The hung process is dead, not leaked.
    assert client.proc.poll() is not None
    client.close()


def test_malformed_frame_raises_acp_error(acp, tmp_path):
    client = acp.AcpClient(_agent_argv("malformed"), _agent_env())
    with pytest.raises(acp.AcpError, match="malformed frame"):
        client.run(str(tmp_path), "do the thing")
    client.close()


def test_agent_death_mid_turn_raises_with_exit_context(acp, tmp_path):
    # An unknown script name crashes the fake agent inside the prompt turn.
    client = acp.AcpClient(_agent_argv("no_such_script"), _agent_env())
    with pytest.raises(acp.AcpError, match="exited"):
        client.run(str(tmp_path), "do the thing")
    client.close()


def test_refusal_stop_reason_propagates(acp, tmp_path):
    outcome, _, _ = _run(acp, "refusal", tmp_path)
    assert outcome.stop_reason == "refusal"
    assert outcome.last_agent_message == "I can't help with that."


def test_rate_limit_error_text_preserved_verbatim(acp, tmp_path):
    client = acp.AcpClient(_agent_argv("rate_limit"), _agent_env())
    with pytest.raises(acp.AcpError) as exc_info:
        client.run(str(tmp_path), "do the thing")
    client.close()
    # Verbatim text is the contract — the runner/host classifiers regex it.
    assert "usage limit" in str(exc_info.value)
    assert "try again in 30 minutes" in str(exc_info.value)


def test_usage_extractor_accumulates_and_all_zero_is_none(acp):
    acc: dict = {}
    acp.accumulate_usage(
        acc,
        {"update": {"sessionUpdate": "agent_message_chunk", "usage": {"inputTokens": 100, "outputTokens": 20}}},
    )
    acp.accumulate_usage(acc, {"usage": {"input_tokens": 50, "cost_usd": 0.0}})
    assert acp.finalize_usage(acc) == {
        "input_tokens": 150,
        "output_tokens": 20,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
    }
    # No report / all-zero reads as unknown, never as free.
    assert acp.finalize_usage({}) is None
    assert acp.finalize_usage({"input_tokens": 0, "cost_usd": 0.0}) is None


def test_usage_reported_via_update_rides_the_outcome(acp, tmp_path):
    outcome, _, _ = _run(acp, "usage", tmp_path)
    assert outcome.usage == {
        "input_tokens": 120,
        "output_tokens": 34,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
    }
