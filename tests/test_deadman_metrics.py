"""The dead-man signal — ``/metrics`` (tinyspec ``deadman-metrics``).

devclaw cannot report its own death or a hung heartbeat; the external
watcher (the box's Prometheus + a provisioned Grafana rule) reads ONE number
for that, ``devclaw_tick_age_seconds``. This pins the invariant the alert
rule depends on: the age is seconds since the last tick, falls back to
process start when no tick has happened (a wedged startup ages too), and
dispatch/pause state ride alongside so "held" is never mistaken for
"stalled". Observability / fail-closed class: a missing route or a 401 on
the scrape is a blind watcher (#911 — the v2 rewrite dropped the import and
the box paged against a healthy devclaw).
"""

from __future__ import annotations

import pytest

from devclaw.server.routes.metrics import render_metrics


def _render(**overrides):
    base = dict(
        now_ms=1_000_000, last_tick_at_ms=955_000, started_at_ms=100_000,
        tick_seconds=900, dispatch_open=True, pause_active=False,
        goal_states={"new": 2, "running": 1}, running_tasks=1,
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


def test_every_goal_state_renders_a_sample_even_at_zero():
    out = _render(goal_states={"blocked": 3, "proposed done": 1})
    assert 'devclaw_goals{state="blocked"} 3\n' in out
    assert 'devclaw_goals{state="proposed done"} 1\n' in out
    for state in ("new", "running", "waiting", "interrupted", "achieved", "cancelled"):
        assert f'devclaw_goals{{state="{state}"}} 0\n' in out


def test_metrics_route_is_registered_and_open():
    """Registration is an import side effect (routes/__init__): the module
    http.py forgets to import serves nothing — exactly how #909 blinded the
    watcher. And the scrape carries no bearer, so the auth gate must leave
    the path open like /health."""
    import devclaw.server.http as http
    from devclaw.server.lifecycle import OPEN_PATHS

    assert http._routes_metrics.__name__.endswith("routes.metrics")
    assert "/metrics" in OPEN_PATHS


# ---- the money domain's one read (spec 047 US3) -----------------------------
# The per-day token history is Grafana's (`increase(devclaw_tokens_total[1d])`),
# so the counter must render every kind, always — a missing series is a blind
# panel — and an instance with nothing reported renders 0 with the reported
# count at 0, never a fabricated spend.


@pytest.mark.parametrize("tokens,total,reported,expected", [
    ({"input_tokens": 1200, "output_tokens": 340, "cache_read_tokens": 5000, "cache_creation_tokens": 800}, 3, 2,
     ['devclaw_tokens_total{kind="input"} 1200\n', 'devclaw_tokens_total{kind="output"} 340\n',
      'devclaw_tokens_total{kind="cache_read"} 5000\n', 'devclaw_tokens_total{kind="cache_creation"} 800\n',
      "devclaw_sessions_total 3\n", "devclaw_sessions_reported_usage 2\n"]),
    (None, 0, 0,
     ['devclaw_tokens_total{kind="input"} 0\n', 'devclaw_tokens_total{kind="cache_creation"} 0\n',
      "devclaw_sessions_total 0\n", "devclaw_sessions_reported_usage 0\n"]),
])
def test_token_counter_renders_every_kind_and_never_fakes_a_spend(tokens, total, reported, expected):
    out = _render(tokens=tokens, sessions_total=total, sessions_reported=reported)
    assert "# TYPE devclaw_tokens_total counter\n" in out
    for line in expected:
        assert line in out
