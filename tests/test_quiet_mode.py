"""Quiet mode (spec 025 US3): while armed, only the instance-dead ping class
reaches the owner; everything else is recorded into ``suppressed_pings`` and
readable on return. The QuietNotifier wraps the ONE notifier binding both send
paths share (tick-path ``_notify`` and the cycle report's direct send)."""

from __future__ import annotations

import json

import pytest

from devclaw.goal.notify import QuietNotifier
from devclaw.goal.service import GoalConfig, GoalService
from devclaw.goal.tick_context import NotifyLevel, _notify
from devclaw.server import tools as _tools
from devclaw.state_store import StateStore
from devclaw.task_queue import TaskQueue
from tests.goal_fakes import FakeClaude, RecordingNotifier


@pytest.fixture
def state(tmp_path):
    s = StateStore(str(tmp_path / "quiet.db"))
    yield s
    s.close()


def _quiet(inner, state, now=5_000):
    return QuietNotifier(inner, state, now_ms=lambda: now)


@pytest.mark.asyncio
async def test_quiet_mode_suppresses_and_records_all_noncritical_pings(state):
    inner = RecordingNotifier()
    qn = _quiet(inner, state)
    state.set_quiet_mode(True, until_ms=None, armed_at_ms=1_000)

    # the cycle report's direct-send path (service.py sends on the notifier
    # object, bypassing _notify — the reason the wrap is at the binding)
    assert await qn.send("🔁 cycle report") is True
    # ordinary tick-path pings at both altitudes
    await _notify(qn, NotifyLevel.OWNER, "🟥 goal parked on a merge failure")
    await _notify(qn, NotifyLevel.OWNER, "✅ goal complete")
    # the instance-dead class pierces
    await _notify(qn, NotifyLevel.OWNER, "🔑 paused — auth dead", critical=True)

    assert inner.sent == ["🔑 paused — auth dead"]
    backlog = [p["text"] for p in state.list_suppressed_pings()]
    assert backlog == ["🔁 cycle report", "🟥 goal parked on a merge failure",
                       "✅ goal complete"]


@pytest.mark.asyncio
async def test_disarmed_quiet_mode_is_a_pure_passthrough(state):
    inner = RecordingNotifier()
    qn = _quiet(inner, state)

    await qn.send("normal ping")

    assert inner.sent == ["normal ping"]
    assert state.list_suppressed_pings() == []


@pytest.mark.asyncio
async def test_quiet_mode_expiry_self_disarms(state):
    inner = RecordingNotifier()
    qn = _quiet(inner, state, now=10_000)
    state.set_quiet_mode(True, until_ms=9_000, armed_at_ms=1_000)  # already past

    await qn.send("after expiry")

    assert inner.sent == ["after expiry"]          # sent, not suppressed
    assert state.quiet_mode() == (False, None)     # and the toggle cleared itself


@pytest.mark.asyncio
async def test_auth_pause_ping_pierces_quiet_mode(state):
    # the auth branch in tick.py is the one tick-path critical call site —
    # pinned structurally (the call carries critical=True) plus behaviorally
    # (send_critical bypasses the armed filter).
    import pathlib

    import devclaw.goal.tick as tick_mod

    src = pathlib.Path(tick_mod.__file__).read_text(encoding="utf-8")
    auth_block = src.split('cred_path = _config.host_claude_dir()', 1)[1][:1200]
    assert "critical=True" in auth_block, "the auth-pause ping lost its critical routing"

    inner = RecordingNotifier()
    qn = _quiet(inner, state)
    state.set_quiet_mode(True, until_ms=None, armed_at_ms=1_000)
    assert await qn.send_critical("🔑 re-login needed") is True
    assert inner.sent == ["🔑 re-login needed"]


@pytest.mark.asyncio
async def test_suppressed_backlog_reads_back_in_order(state):
    for i in range(3):
        state.record_suppressed_ping(f"ping {i}", ts_ms=1_000 + i)
    rows = state.list_suppressed_pings()
    assert [r["text"] for r in rows] == ["ping 0", "ping 1", "ping 2"]
    assert state.suppressed_ping_count() == 3


def test_service_notifier_binding_is_quiet_wrapped(tmp_path, state):
    """The wrap lives at the binding, so BOTH send paths (tick _notify and the
    cycle report's direct send on self._notifier) go through quiet mode."""
    cfg = GoalConfig(goals_dir=tmp_path / "goals", notify_url="", tick_seconds=900,
                     verify_done=False)
    svc = GoalService(TaskQueue(state), state, config=cfg,
                      notifier=RecordingNotifier(), evaluator_caller=FakeClaude())
    assert isinstance(svc._notifier, QuietNotifier)


# ---- the MCP operator verbs -----------------------------------------------


@pytest.fixture(autouse=True)
def _patch_tool_store(state, monkeypatch):
    monkeypatch.setattr(_tools.control, "store", state)
    return state


async def test_set_quiet_mode_tool_roundtrip(state):
    out = json.loads(await _tools.set_quiet_mode(True, until="2026-09-08T08:00:00+00:00"))
    assert out["quiet"] is True and out["until_ms"] is not None
    assert state.quiet_mode()[0] is True

    state.record_suppressed_ping("held", ts_ms=1)
    out = json.loads(await _tools.list_suppressed_pings())
    assert out["count"] == 1 and out["pings"][0]["text"] == "held"

    out = json.loads(await _tools.set_quiet_mode(False))
    assert out["quiet"] is False
    assert state.quiet_mode() == (False, None)
    # disarm keeps the backlog
    assert state.suppressed_ping_count() == 1


async def test_set_quiet_mode_rejects_a_bad_until(state):
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError):
        await _tools.set_quiet_mode(True, until="next tuesday")
