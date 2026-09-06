"""``/metrics`` — the dead-man signal, in Prometheus exposition format.

devclaw reports everything it can see about itself over Telegram (the
notifier) and the JSON routes; what it structurally cannot report is its own
death or a hung heartbeat. ``/health`` has published ``last_tick_at`` for an
"external dead-man watcher" since #494 — this route is the same freshness,
shaped for the watcher that actually exists on the box: Prometheus scrapes it
and a provisioned Grafana rule alerts to Telegram directly, so a dead
notify-relay cannot swallow the alarm either.

Seven gauges, hand-rendered: the exposition text format is a dozen lines and
carries no label value that needs escaping (a sha, a version, a phase), so
this stays a zero-dependency read over the projections ``/node.json`` already
serves. ``devclaw_tick_age_seconds`` is the one number the alert rule reads:
seconds since the last heartbeat tick, or since process start when no tick
has happened yet — a wedged startup ages just like a wedged loop. Token-free,
auth-free (the auth gate leaves it open like ``/health``), never raises on an
absent value: unknown build identity renders as ``unknown``, never fakes a
number.
"""

from __future__ import annotations

import math

from starlette.requests import Request
from starlette.responses import Response

from ... import __version__
from .._state import goals, mcp, store

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: Every goal phase, so a phase with zero goals still renders a 0 sample and a
#: dashboard never sees a series vanish.
_PHASES: tuple[str, ...] = ("idle", "in_flight", "verifying", "blocked", "done", "cancelled")


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "NaN"
        return repr(value) if value != int(value) else str(int(value))
    return str(value)


def render_metrics(
    *,
    now_ms: int,
    last_tick_at_ms: int | None,
    started_at_ms: int | None,
    tick_seconds: int | None,
    dispatch_open: bool,
    pause_active: bool,
    goal_phases: dict[str, int],
    running_tasks: int,
    version: str,
    git_sha: str | None,
) -> str:
    """The pure half: data in, exposition text out. Tested directly."""
    anchor = last_tick_at_ms or started_at_ms
    tick_age: float | None = (now_ms - anchor) / 1000.0 if anchor else None
    sha = git_sha or "unknown"
    lines = [
        "# HELP devclaw_build_info Build identity; value is always 1.",
        "# TYPE devclaw_build_info gauge",
        f'devclaw_build_info{{version="{version}",git_sha="{sha}"}} 1',
        "# HELP devclaw_tick_seconds The heartbeat's configured tick length.",
        "# TYPE devclaw_tick_seconds gauge",
        f"devclaw_tick_seconds {_fmt(tick_seconds)}",
        "# HELP devclaw_tick_age_seconds Seconds since the last heartbeat tick (since start when none yet).",
        "# TYPE devclaw_tick_age_seconds gauge",
        f"devclaw_tick_age_seconds {_fmt(tick_age)}",
        "# HELP devclaw_dispatch_open 1 when new dispatch is open (no hold, inside the window, not paused).",
        "# TYPE devclaw_dispatch_open gauge",
        f"devclaw_dispatch_open {int(dispatch_open)}",
        "# HELP devclaw_pause_active 1 while the account-wide usage/auth pause holds.",
        "# TYPE devclaw_pause_active gauge",
        f"devclaw_pause_active {int(pause_active)}",
        "# HELP devclaw_goals Goals by phase.",
        "# TYPE devclaw_goals gauge",
    ]
    for phase in _PHASES:
        lines.append(f'devclaw_goals{{phase="{phase}"}} {int(goal_phases.get(phase, 0))}')
    lines += [
        "# HELP devclaw_tasks_running Tasks currently running in a sandbox.",
        "# TYPE devclaw_tasks_running gauge",
        f"devclaw_tasks_running {int(running_tasks)}",
    ]
    return "\n".join(lines) + "\n"


def _collect() -> str:
    from ... import config as _config
    from ...dispatch_gate import operator_block
    from ...state_store import _now_ms

    now = _now_ms()
    q_until, _q_reason = store.global_pause()
    pause_active = q_until > now
    op_blocked, _why = operator_block(store.operator_hold(), store.get_run_schedule(), now)
    phases: dict[str, int] = {}
    for g in goals.list_goals():
        phase = str(g.get("phase") or "unknown")
        phases[phase] = phases.get(phase, 0) + 1
    return render_metrics(
        now_ms=now,
        last_tick_at_ms=getattr(goals, "last_tick_at_ms", None),
        started_at_ms=getattr(goals, "started_at_ms", None),
        tick_seconds=getattr(goals, "tick_seconds", None),
        dispatch_open=not (op_blocked or pause_active),
        pause_active=pause_active,
        goal_phases=phases,
        running_tasks=len(store.list_tasks(status="running", limit=200)),
        version=__version__,
        git_sha=_config.git_sha(),
    )


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(_request: Request) -> Response:
    return Response(_collect(), media_type=CONTENT_TYPE)
