"""Heartbeat freshness + build identity on the health surfaces (#494).

The 2026-08-12 ledger night-1 root-cause hunt needed ssh + docker inspect to
answer "which code is running?", and nothing anywhere could answer "is the
heartbeat loop actually alive?" — ``/health`` returns ``ok`` as long as the
process serves HTTP, even with the goal loop dead behind it. These pin the
fix: ``/health`` and ``/node.json`` share one freshness block carrying the
deployed build identity (env baked at image build), the loop's
``last_tick_at`` (stamped only on a COMPLETED tick pass), and the last
cycle-report time — the fields the external dead-man watcher (ops-agent O5)
keys on.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from devclaw.state_store import StateStore


@pytest.fixture
def store(tmp_path):
    return StateStore(str(tmp_path / "s.db"))


@pytest.fixture
def http_mod(store, monkeypatch):
    from devclaw.server.routes import control as http_mod

    monkeypatch.setattr(http_mod, "store", store)
    return http_mod


def _payload(resp) -> dict:
    return json.loads(resp.body)


def test_health_surfaces_heartbeat_freshness_and_build_identity(
    http_mod, store, monkeypatch
):
    monkeypatch.setenv("DEVCLAW_GIT_SHA", "abc1234")
    monkeypatch.setenv("DEVCLAW_BUILT_AT", "2026-08-12T20:00:00+00:00")
    monkeypatch.setattr(
        http_mod,
        "goals",
        SimpleNamespace(
            started_at_ms=1_700_000_000_000,
            last_tick_at_ms=1_700_000_900_000,
            tick_seconds=900,
        ),
    )
    store.record_cycle_report(
        cycle_key="2026-08-12",
        window_start_ms=1,
        window_end_ms=2,
        clean=True,
        wedges_json="[]",
        pauses_json="[]",
        summary="clean",
        sent_at=None,
    )

    body = _payload(asyncio.run(http_mod.health(None)))

    assert body["ok"] is True
    assert body["git_sha"] == "abc1234"
    assert body["built_at"] == "2026-08-12T20:00:00+00:00"
    assert body["started_at"].startswith("2023-11-14T")  # 1_700_000_000_000
    assert body["last_tick_at"].startswith("2023-11-14T")
    assert body["last_cycle_report_at"] is not None
    assert body["tick_seconds"] == 900


def test_health_is_null_safe_before_first_tick_and_without_build_env(
    http_mod, monkeypatch
):
    """A fresh boot (no tick yet, no build env, empty cycle_reports) reports
    null — honest absence, never a fake timestamp, and never a 500."""
    monkeypatch.delenv("DEVCLAW_GIT_SHA", raising=False)
    monkeypatch.delenv("DEVCLAW_BUILT_AT", raising=False)
    monkeypatch.setattr(
        http_mod,
        "goals",
        SimpleNamespace(started_at_ms=1_700_000_000_000, last_tick_at_ms=None, tick_seconds=900),
    )

    body = _payload(asyncio.run(http_mod.health(None)))

    assert body["ok"] is True
    assert body["git_sha"] is None
    assert body["built_at"] is None
    assert body["last_tick_at"] is None
    assert body["last_cycle_report_at"] is None


def test_health_reports_dispatch_state_for_the_held_vs_stalled_call(
    http_mod, store, monkeypatch
):
    """The token-free route carries dispatch_open + the hold reason so the
    external watchdog can tell "held" (run window / operator hold — normal)
    from "stalled" (a real wedge) — the O3 false-positive class."""
    monkeypatch.setattr(
        http_mod,
        "goals",
        SimpleNamespace(started_at_ms=1, last_tick_at_ms=None, tick_seconds=900),
    )
    body = _payload(asyncio.run(http_mod.health(None)))
    assert body["dispatch_open"] is True  # fresh store: no hold, window disabled
    assert body["dispatch_hold_reason"] is None

    store.set_operator_hold(True, "manual hold for test")
    body = _payload(asyncio.run(http_mod.health(None)))
    assert body["dispatch_open"] is False
    assert body["dispatch_hold_reason"]


def test_node_json_carries_the_same_freshness_block(http_mod, monkeypatch):
    monkeypatch.setattr(
        http_mod,
        "goals",
        SimpleNamespace(
            list_goals=lambda: [],
            started_at_ms=1_700_000_000_000,
            last_tick_at_ms=1_700_000_900_000,
            tick_seconds=900,
        ),
    )
    v = http_mod._node_vitals()
    # Pin the VALUE, not the agreement. Comparing _node_vitals() against
    # _health_freshness() only restates that one calls the other, so it passed
    # for any value — including a wrong one — and could never catch the drift
    # between the two surfaces that this test exists to prevent (#494).
    assert v["freshness"]["last_tick_at"] == "2023-11-14T22:28:20+00:00"
    assert v["freshness"]["tick_seconds"] == 900
    # The shared-truth property itself: both surfaces expose the same keys.
    assert set(v["freshness"]) == set(http_mod._health_freshness())


@pytest.mark.asyncio
async def test_tick_all_stamps_last_tick_at_only_on_a_completed_pass(
    store, tmp_path, monkeypatch
):
    """The stamp is the dead-man's signal: a completed pass moves it, a
    crashing pass leaves it stale (stale-on-crash IS the alert condition)."""
    from devclaw.goal import service as service_mod
    from devclaw.goal.service import GoalConfig, GoalService
    from devclaw.task_queue import TaskQueue

    cfg = GoalConfig(
        goals_dir=tmp_path / "goals",
        notify_url="",
        tick_seconds=900,
        verify_done=False,
    )
    queue = TaskQueue(store)
    svc = GoalService(queue, store, cfg)
    assert svc.last_tick_at_ms is None
    assert svc.started_at_ms > 0
    assert svc.tick_seconds == cfg.tick_seconds

    async def _ok(**_kw):
        return {}

    monkeypatch.setattr(service_mod, "tick_all", _ok)
    await svc.tick_all()
    stamped = svc.last_tick_at_ms
    assert stamped is not None

    async def _boom(**_kw):
        raise RuntimeError("tick crashed")

    monkeypatch.setattr(service_mod, "tick_all", _boom)
    with pytest.raises(RuntimeError):
        await svc.tick_all()
    assert svc.last_tick_at_ms == stamped  # unchanged — stale on crash
