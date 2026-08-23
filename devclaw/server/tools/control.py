"""Operator dispatch controls — run-window + manual hold.

The same levers the CLI (``devclaw schedule ...``) and the /control/* HTTP
routes drive; they write the control-plane meta table, never goal state.
"""

from __future__ import annotations

import json
from typing import Optional

from fastmcp.exceptions import ToolError

from ...dispatch_gate import (
    _parse_hhmm,
    next_window_open_ms,
    operator_block,
    schedule_blocks,
)
from ...state_store import _now_ms
from .._state import mcp, store


# ===== operator dispatch controls ============================================
# Flip the run-window / manual hold from MCP — the same levers the CLI
# (`devclaw schedule ...`) and the /control/* HTTP routes drive, so the operator
# can open or close dispatch on demand without SSHing to the box. These write the
# control-plane meta table (StateStore.ControlPlaneMixin), NOT goal state, so the
# goal-transition CAS choke point doesn't apply — they mirror the existing HTTP
# write path 1:1. Both gates affect NEW dispatch only; in-flight tasks finish.


@mcp.tool
async def get_run_schedule(goal_id: Optional[str] = None) -> str:
    """Show the current dispatch controls: the daily run-window, the manual
    operator hold, and whether new dispatch is open RIGHT NOW. With ``goal_id``,
    returns that goal's OWN window (an extra narrowing on top of the global one)
    instead of the engine-wide window.

    Read-only. New dispatch is gated when the manual hold is on OR the current
    time is outside the (enabled) window; in-flight tasks always finish. Change it
    with set_run_schedule / set_operator_hold."""
    now = _now_ms()
    hold = store.operator_hold()
    schedule = store.get_run_schedule(goal_id)
    if goal_id:
        blocked, why = schedule_blocks(schedule, now)
    else:
        blocked, why = operator_block(hold, schedule, now)
    out = {
        "goal_id": goal_id,
        "schedule": schedule,
        "operator_hold": {"on": hold[0], "reason": hold[1]},
        "dispatch_open": not blocked,
        "why_blocked": why or None,
        "next_window_open_ms": next_window_open_ms(schedule, now),
    }
    return json.dumps(out, indent=2)


@mcp.tool
async def set_run_schedule(
    enabled: bool,
    start: Optional[str] = None,
    end: Optional[str] = None,
    tz: Optional[str] = None,
    goal_id: Optional[str] = None,
) -> str:
    """Set the daily run-window during which new dispatch is allowed. Outside it,
    new dispatch is gated (in-flight finishes). ``start``/``end`` are ``'HH:MM'``
    (24h) in IANA ``tz`` (e.g. "Europe/Kyiv"); omitted fields keep their current
    value.

    To OPEN the window on demand (let held work dispatch now), pass
    ``enabled=false`` — a disabled window never gates. With ``goal_id`` this sets
    that goal's OWN window (a narrowing on top of the global one) rather than the
    engine-wide window.

    A malformed time or unknown timezone is REJECTED (the gate fails open, so a
    typo must not silently disable the window) — mirrors POST /control/schedule."""
    from zoneinfo import ZoneInfo

    cur = store.get_run_schedule(goal_id)
    start = start or cur["start"]
    end = end or cur["end"]
    tz = tz or cur["tz"]
    if _parse_hhmm(start) is None or _parse_hhmm(end) is None:
        raise ToolError("start/end must be HH:MM (24h)")
    try:
        ZoneInfo(tz)
    except Exception:
        raise ToolError(
            f"unknown timezone {tz!r} — use an IANA name, e.g. Europe/Kyiv"
        ) from None
    store.set_run_schedule(bool(enabled), start, end, tz, goal_id=goal_id)
    return json.dumps(
        {"goal_id": goal_id, "schedule": store.get_run_schedule(goal_id)}, indent=2
    )


@mcp.tool
async def set_operator_hold(on: bool, reason: str = "") -> str:
    """Manually pause (``on=true``) or resume (``on=false``) ALL new dispatch —
    the big red button. The manual hold WINS over the run-window (an explicit
    pause is never overridden by an open window) and is independent of the
    automatic quota pause. In-flight tasks always finish. Mirrors
    POST /control/pause + /control/resume."""
    store.set_operator_hold(bool(on), reason)
    hold = store.operator_hold()
    return json.dumps({"operator_hold": {"on": hold[0], "reason": hold[1]}}, indent=2)
