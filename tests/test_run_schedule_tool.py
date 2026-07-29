"""The operator dispatch-control MCP tools — ``get_run_schedule`` /
``set_run_schedule`` / ``set_operator_hold`` (#434).

The run-window gate (``dispatch_gate``), the schedule store
(``StateStore.ControlPlaneMixin``) and the operator hold all already existed and
worked — but were reachable only via the CLI (SSH) and the raw ``/control/*``
HTTP routes, never over MCP. So the operator "couldn't flip the schedule from the
MCP" and had no way to open dispatch on demand. These pins guard the thin MCP
wrappers: they mirror the store/HTTP write path 1:1, report whether dispatch is
open right now, open on demand via ``enabled=false``, fail closed on a bad
schedule, and let the manual hold win over the window.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastmcp.exceptions import ToolError

from devclaw.server import tools as _tools
from devclaw.state_store import StateStore

# A fixed 2026-06-01 12:00 UTC so window-open/closed assertions don't depend on
# the wall clock (tools._now_ms is monkeypatched to this where it matters).
_NOON_UTC_MS = int(datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)


@pytest.fixture
def store(tmp_path):
    return StateStore(str(tmp_path / "control.db"))


@pytest.fixture(autouse=True)
def _patch_store(store, monkeypatch):
    # tools.py binds `store` at import; point it at a throwaway control plane.
    monkeypatch.setattr(_tools, "store", store)
    return store


async def test_get_run_schedule_default_is_open(store):
    out = json.loads(await _tools.get_run_schedule())
    assert out["schedule"]["enabled"] is False       # disabled default
    assert out["operator_hold"] == {"on": False, "reason": ""}
    assert out["dispatch_open"] is True              # nothing gates a fresh store
    assert out["why_blocked"] is None
    assert out["next_window_open_ms"] is None


async def test_set_run_schedule_persists_and_reads_back(store):
    out = json.loads(
        await _tools.set_run_schedule(enabled=True, start="22:00", end="05:00", tz="Europe/London")
    )
    assert out["schedule"] == {
        "enabled": True, "start": "22:00", "end": "05:00", "tz": "Europe/London",
    }
    # Written straight through to the control-plane store (same path as HTTP).
    assert store.get_run_schedule() == out["schedule"]


async def test_set_run_schedule_disabled_opens_dispatch_on_demand(store, monkeypatch):
    monkeypatch.setattr(_tools, "_now_ms", lambda: _NOON_UTC_MS)
    # A window that EXCLUDES noon UTC → dispatch is gated.
    await _tools.set_run_schedule(enabled=True, start="22:00", end="23:00", tz="UTC")
    closed = json.loads(await _tools.get_run_schedule())
    assert closed["dispatch_open"] is False
    assert closed["next_window_open_ms"] is not None  # legible "held until <when>"

    # Disabling the window opens dispatch immediately — the on-demand escape.
    await _tools.set_run_schedule(enabled=False)
    opened = json.loads(await _tools.get_run_schedule())
    assert opened["dispatch_open"] is True


async def test_set_run_schedule_rejects_bad_time(store):
    with pytest.raises(ToolError):
        await _tools.set_run_schedule(enabled=True, start="25:99", end="05:00", tz="UTC")
    # Fail-closed: nothing persisted, the window stays at its disabled default.
    assert store.get_run_schedule()["enabled"] is False


async def test_set_run_schedule_rejects_bad_timezone(store):
    with pytest.raises(ToolError):
        await _tools.set_run_schedule(enabled=True, start="22:00", end="05:00", tz="Mars/Olympus")
    assert store.get_run_schedule()["enabled"] is False


async def test_set_operator_hold_closes_and_reopens_dispatch(store):
    held = json.loads(await _tools.set_operator_hold(on=True, reason="watching a live run"))
    assert held["operator_hold"] == {"on": True, "reason": "watching a live run"}
    # Hold wins over the window (which is open by default) → dispatch closed.
    closed = json.loads(await _tools.get_run_schedule())
    assert closed["dispatch_open"] is False
    assert closed["why_blocked"] == "watching a live run"

    await _tools.set_operator_hold(on=False)
    opened = json.loads(await _tools.get_run_schedule())
    assert opened["operator_hold"] == {"on": False, "reason": ""}
    assert opened["dispatch_open"] is True


async def test_per_goal_run_schedule_is_isolated_from_global(store):
    await _tools.set_run_schedule(enabled=True, start="01:00", end="02:00", tz="UTC", goal_id="g1")
    # The per-goal window is stored under the goal, not the global key.
    assert json.loads(await _tools.get_run_schedule(goal_id="g1"))["schedule"]["enabled"] is True
    assert json.loads(await _tools.get_run_schedule())["schedule"]["enabled"] is False
