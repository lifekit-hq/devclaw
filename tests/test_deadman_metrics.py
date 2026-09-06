"""The dead-man signal — ``/metrics`` (tinyspec ``deadman-metrics``).

devclaw cannot report its own death or a hung heartbeat; the external
watcher (Prometheus + a provisioned Grafana rule) reads ONE number for that,
``devclaw_tick_age_seconds``. This pins the invariant the alert rule depends
on: the age is seconds since the last tick, falls back to process start when
no tick has happened (a wedged startup ages too), and dispatch/pause state
ride alongside so "held" is never mistaken for "stalled".
"""

from __future__ import annotations

import pytest

from devclaw.server.routes.metrics import render_metrics


def _render(**overrides):
    base = dict(
        now_ms=1_000_000, last_tick_at_ms=955_000, started_at_ms=100_000,
        tick_seconds=900, dispatch_open=True, pause_active=False,
        goal_phases={"idle": 2, "in_flight": 1}, running_tasks=1,
        version="1.2.3", git_sha="abc1234",
    )
    base.update(overrides)
    return render_metrics(**base)


@pytest.mark.parametrize("overrides,expected", [
    # the number the alert reads: seconds since the last tick
    ({}, "devclaw_tick_age_seconds 45\n"),
    # no tick yet → age since process start, never absent, never zero-faked
    ({"last_tick_at_ms": None}, "devclaw_tick_age_seconds 900\n"),
    # neither → NaN (Prometheus-legal), not a made-up number
    ({"last_tick_at_ms": None, "started_at_ms": None}, "devclaw_tick_age_seconds NaN\n"),
    # held is not stalled: the rule gates on dispatch_open
    ({"dispatch_open": False}, "devclaw_dispatch_open 0\n"),
    ({"pause_active": True}, "devclaw_pause_active 1\n"),
    # build identity absent → labelled unknown, still a sample
    ({"git_sha": None}, 'devclaw_build_info{version="1.2.3",git_sha="unknown"} 1\n'),
])
def test_deadman_metrics_expose_tick_age_and_dispatch_state(overrides, expected):
    assert expected in _render(**overrides)


def test_every_goal_phase_renders_a_sample_even_at_zero():
    out = _render(goal_phases={"blocked": 3})
    assert 'devclaw_goals{phase="blocked"} 3\n' in out
    for phase in ("idle", "in_flight", "verifying", "done", "cancelled"):
        assert f'devclaw_goals{{phase="{phase}"}} 0\n' in out


def test_metrics_route_is_registered():
    """Registration is an import side effect (routes/__init__): the module
    http.py forgets to import serves nothing, and the watcher goes blind."""
    import devclaw.server.http as http

    assert "metrics" in http.__dict__.get("_routes_metrics").__name__
