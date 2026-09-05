"""/health staleness verdict (audit B3a, tinyspec health-liveness-verdict).

The heartbeat freshness stamp (#494) is "the signal an external dead-man
watcher needs" — these tests pin that /health actually renders a verdict from
it, that the reason names the TRUE failing condition (a held instance is never
reported stale — an alarm naming the wrong condition trains its reader to
distrust it), and that strict mode turns the verdict into exit-code semantics
without changing the default shape existing consumers read.
"""

from __future__ import annotations

import json

from starlette.requests import Request

from devclaw.server.routes import control as _control
from devclaw.server.routes.control import _liveness_verdict

TICK = 900
NOW = 10_000_000_000_000  # arbitrary epoch-ms "now"
WINDOW_MS = 3 * TICK * 1000  # default stale_ticks=3


def _verdict(**kw):
    base = dict(
        started_at_ms=NOW - 100 * TICK * 1000,
        last_tick_at_ms=NOW - TICK * 1000,
        tick_seconds=TICK,
        stale_ticks=3,
        now_ms=NOW,
    )
    base.update(kw)
    return _liveness_verdict(**base)


def test_fresh_tick_is_not_stale():
    assert _verdict(last_tick_at_ms=NOW - TICK * 1000) == (False, None)


def test_stamp_past_threshold_is_heartbeat_stale():
    assert _verdict(last_tick_at_ms=NOW - WINDOW_MS - 1) == (True, "heartbeat_stale")


def test_never_ticked_past_grace_is_loop_never_ticked():
    assert _verdict(
        last_tick_at_ms=None, started_at_ms=NOW - WINDOW_MS - 1
    ) == (True, "loop_never_ticked")


def test_never_ticked_within_grace_is_not_stale():
    # A freshly started process gets stale_ticks × tick_seconds to complete
    # its first pass (startup sweep included) before the alarm fires.
    assert _verdict(last_tick_at_ms=None, started_at_ms=NOW - TICK * 1000) == (False, None)


def test_unknown_inputs_render_no_alarm():
    # Unknown is not an alarm: no tick interval / no start stamp → no verdict.
    assert _verdict(tick_seconds=None) == (False, None)
    assert _verdict(started_at_ms=None, last_tick_at_ms=None) == (False, None)


class _FakeGoals:
    def __init__(self, *, started_at_ms, last_tick_at_ms, tick_seconds=TICK):
        self.started_at_ms = started_at_ms
        self.last_tick_at_ms = last_tick_at_ms
        self.tick_seconds = tick_seconds


class _FakeStore:
    """Just enough store for _health_freshness; ``hold`` drives operator_block."""

    def __init__(self, hold: bool = False):
        self._hold = hold

    def list_cycle_reports(self, limit=1):
        return []

    def operator_hold(self):
        return (self._hold, "manual" if self._hold else "")

    def get_run_schedule(self, goal_id=None):
        return {"enabled": False, "start": "00:00", "end": "00:00", "tz": "UTC"}


def _freshness(monkeypatch, goals, store):
    monkeypatch.setattr(_control, "goals", goals)
    monkeypatch.setattr(_control, "store", store)
    return _control._health_freshness()


def test_dispatch_held_but_ticking_is_not_stale(monkeypatch):
    """The load-bearing distinction: an operator hold gates DISPATCH while the
    heartbeat keeps stamping — the verdict must read "held", never "stale"."""
    from devclaw.state_store import _now_ms

    now = _now_ms()
    out = _freshness(
        monkeypatch,
        _FakeGoals(started_at_ms=now - 10 * TICK * 1000, last_tick_at_ms=now - 1000),
        _FakeStore(hold=True),
    )
    assert out["dispatch_open"] is False
    assert out["stale"] is False
    assert out["stale_reason"] is None


def test_freshness_carries_verdict(monkeypatch):
    from devclaw.state_store import _now_ms

    now = _now_ms()
    out = _freshness(
        monkeypatch,
        _FakeGoals(started_at_ms=now - 100 * TICK * 1000, last_tick_at_ms=now - WINDOW_MS - 60_000),
        _FakeStore(),
    )
    assert out["stale"] is True
    assert out["stale_reason"] == "heartbeat_stale"


def _get_health(query_string: bytes):
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/health",
        "query_string": query_string,
        "headers": [],
    }
    return _control.health(Request(scope))


async def test_health_default_shape_keeps_ok_true_when_stale(monkeypatch):
    """Back-compat: existing consumers (compose curl, ops-agent) read today's
    shape — a stale loop must not flip the default ``ok`` or the status code."""
    from devclaw.state_store import _now_ms

    now = _now_ms()
    monkeypatch.setattr(
        _control, "goals",
        _FakeGoals(started_at_ms=now - 100 * TICK * 1000, last_tick_at_ms=now - WINDOW_MS - 60_000),
    )
    monkeypatch.setattr(_control, "store", _FakeStore())
    resp = await _get_health(b"")
    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["ok"] is True
    assert body["stale"] is True


async def test_health_strict_mode_503s_on_stale_only(monkeypatch):
    from devclaw.state_store import _now_ms

    now = _now_ms()
    monkeypatch.setattr(_control, "store", _FakeStore())

    monkeypatch.setattr(
        _control, "goals",
        _FakeGoals(started_at_ms=now - 100 * TICK * 1000, last_tick_at_ms=now - WINDOW_MS - 60_000),
    )
    resp = await _get_health(b"strict=1")
    assert resp.status_code == 503
    assert json.loads(resp.body)["ok"] is False

    monkeypatch.setattr(
        _control, "goals",
        _FakeGoals(started_at_ms=now - 10 * TICK * 1000, last_tick_at_ms=now - 1000),
    )
    resp = await _get_health(b"strict=1")
    assert resp.status_code == 200
    assert json.loads(resp.body)["ok"] is True
