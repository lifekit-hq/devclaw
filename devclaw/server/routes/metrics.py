"""``/metrics`` — the dead-man signal, in Prometheus exposition format.

devclaw reports everything it can see about itself (Telegram pings, the
JSON routes); what it structurally cannot report is its own death or a hung
heartbeat. ``/health`` publishes ``last_tick_at`` for exactly that reader —
this route is the same freshness shaped for the watcher that exists on the
box: the lifekit-stack Prometheus scrapes it and provisioned Grafana rules
alert to Telegram directly (``up{job="devclaw"} == 0`` = dead;
``devclaw_tick_age_seconds > 3 * devclaw_tick_seconds`` while
``devclaw_dispatch_open == 1`` = hung — "held" is never "stalled").

Hand-rendered: seven gauges over the vitals ``/health`` already serves, no
dependency. ``devclaw_tick_age_seconds`` is the one number the alert reads:
seconds since the last heartbeat tick, or since process start when no tick
has happened yet — a wedged startup ages like a wedged loop. Token-free,
auth-free (``lifecycle.AuthMiddleware`` leaves it open like ``/health``: the
scrape job carries no bearer, and a 401 is a blind watcher). Never raises on
an absent value: an unknown build identity renders as ``unknown``, an unknown
age as ``NaN`` — never a faked number.
"""

from __future__ import annotations

import math

from starlette.requests import Request
from starlette.responses import Response

from ... import __version__
from ... import config as _config
from ...dispatch_gate import operator_block
from ...state_store import _now_ms
from .._state import goals, mcp, store

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

#: Every goal state word ``goal.service._state_word`` can produce — open ones
#: first, then the two close outcomes — so a state with zero goals still
#: renders a 0 sample and a dashboard never sees a series vanish.
_STATES: tuple[str, ...] = ("new", "running", "waiting", "interrupted", "blocked",
                            "proposed done", "achieved", "cancelled")


def _fmt(value: float | int | None) -> str:
    if value is None:
        return "NaN"
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "NaN"
        return repr(value) if value != int(value) else str(int(value))
    return str(value)


def _label(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics(
    *,
    now_ms: int,
    last_tick_at_ms: int | None,
    started_at_ms: int | None,
    tick_seconds: int | None,
    dispatch_open: bool,
    pause_active: bool,
    goal_states: dict[str, int],
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
        f'devclaw_build_info{{version="{_label(version)}",git_sha="{_label(sha)}"}} 1',
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
        "# HELP devclaw_goals Goals by state (the word the console shows).",
        "# TYPE devclaw_goals gauge",
    ]
    for state in _STATES:
        lines.append(f'devclaw_goals{{state="{_label(state)}"}} {int(goal_states.get(state, 0))}')
    for state, n in goal_states.items():
        if state not in _STATES:  # a word the list does not know yet still counts
            lines.append(f'devclaw_goals{{state="{_label(state)}"}} {int(n)}')
    lines += [
        "# HELP devclaw_tasks_running Sessions currently running in a sandbox.",
        "# TYPE devclaw_tasks_running gauge",
        f"devclaw_tasks_running {int(running_tasks)}",
    ]
    return "\n".join(lines) + "\n"


def _collect() -> str:
    now = _now_ms()
    until, _reason = store.global_pause()
    pause_active = bool(until and until > now)
    blocked, _why = operator_block(store.operator_hold(), store.get_run_schedule(), now)
    states: dict[str, int] = {}
    for g in goals.list_goals():
        word = str(g.get("state") or "unknown")
        states[word] = states.get(word, 0) + 1
    return render_metrics(
        now_ms=now,
        last_tick_at_ms=goals.last_tick_at_ms,
        started_at_ms=goals.started_at_ms,
        tick_seconds=goals.tick_seconds,
        dispatch_open=not blocked and not pause_active,
        pause_active=pause_active,
        goal_states=states,
        running_tasks=store.count_running(),
        version=__version__,
        git_sha=_config.git_sha(),
    )


@mcp.custom_route("/metrics", methods=["GET"])
async def metrics(_request: Request) -> Response:
    return Response(_collect(), media_type=CONTENT_TYPE)
