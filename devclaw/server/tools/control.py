"""Operator dispatch controls — the run window, the manual hold, the quota
pause, the concurrency dial. They write control-plane flags, never goal state."""

from __future__ import annotations

import json
from typing import Optional

from fastmcp.exceptions import ToolError

from ...dispatch_gate import _parse_hhmm, next_window_open_ms, operator_block
from ...state_store import _now_ms
from .._state import mcp, store


@mcp.tool
async def get_run_schedule() -> str:
    """The daily run window, the manual hold, the quota pause, and whether
    new dispatch is open right now. Read-only."""
    from ...task_queue import GLOBAL_MAX_CONCURRENT

    now = _now_ms()
    hold = store.operator_hold()
    schedule = store.get_run_schedule()
    blocked, why = operator_block(hold, schedule, now)
    until, reason = store.global_pause()
    override = store.max_concurrent()
    return json.dumps({
        "schedule": schedule,
        "operator_hold": {"on": hold[0], "reason": hold[1]},
        "pause": {"until_ms": until, "reason": reason} if until and until > now else None,
        "dispatch_open": not blocked and not (until and until > now),
        "why_blocked": why or None,
        "next_window_open_ms": next_window_open_ms(schedule, now),
        "max_concurrent": {"effective": override or GLOBAL_MAX_CONCURRENT, "override": override,
                           "default": GLOBAL_MAX_CONCURRENT},
    }, indent=2)


@mcp.tool
async def set_run_schedule(enabled: bool, start: Optional[str] = None, end: Optional[str] = None,
                           tz: Optional[str] = None) -> str:
    """Set the daily window (``HH:MM`` in IANA ``tz``) during which new
    sessions start; in-flight sessions finish. ``enabled=false`` = 24/7."""
    from zoneinfo import ZoneInfo

    cur = store.get_run_schedule()
    start = start or cur["start"]
    end = end or cur["end"]
    tz = tz or cur["tz"]
    if _parse_hhmm(start) is None or _parse_hhmm(end) is None:
        raise ToolError("start/end must be HH:MM (24h)")
    try:
        ZoneInfo(tz)
    except Exception:
        raise ToolError(f"unknown timezone {tz!r} — use an IANA name, e.g. Europe/Dublin") from None
    store.set_run_schedule(bool(enabled), start, end, tz)
    return json.dumps({"schedule": store.get_run_schedule()}, indent=2)


@mcp.tool
async def set_operator_hold(on: bool, reason: str = "") -> str:
    """The big red button: hold (``on=true``) or release ALL new dispatch."""
    store.set_operator_hold(bool(on), reason)
    hold = store.operator_hold()
    return json.dumps({"operator_hold": {"on": hold[0], "reason": hold[1]}}, indent=2)


@mcp.tool
async def clear_usage_pause() -> str:
    """Clear an active quota/auth pause now — "I fixed the cause". A no-op
    when nothing is paused; a still-real limit simply re-pauses."""
    until, reason = store.global_pause()
    store.clear_global_pause()
    store.set_pause_notified(False)
    return json.dumps({"cleared": bool(until), "was_until_ms": until, "was_reason": reason}, indent=2)


@mcp.tool
async def set_max_concurrent(n: Optional[int] = None) -> str:
    """The cap on concurrently-running sandboxes (``1`` = strictly serial,
    the unattended setting). Omit ``n`` to clear the override."""
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
        raise ToolError("n must be a whole number >= 1, or null to clear the override")
    try:
        store.set_max_concurrent(n)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    from ...task_queue import GLOBAL_MAX_CONCURRENT

    override = store.max_concurrent()
    return json.dumps({"max_concurrent": {"effective": override or GLOBAL_MAX_CONCURRENT,
                                          "override": override, "default": GLOBAL_MAX_CONCURRENT}}, indent=2)
