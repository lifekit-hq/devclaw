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
    from ...task_queue import GLOBAL_MAX_CONCURRENT

    override = store.max_concurrent()
    out = {
        "goal_id": goal_id,
        "schedule": schedule,
        "operator_hold": {"on": hold[0], "reason": hold[1]},
        "dispatch_open": not blocked,
        "why_blocked": why or None,
        "next_window_open_ms": next_window_open_ms(schedule, now),
        "max_concurrent": {
            "effective": override or GLOBAL_MAX_CONCURRENT,
            "override": override,
            "default": GLOBAL_MAX_CONCURRENT,
        },
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


@mcp.tool
async def clear_usage_pause() -> str:
    """Clear an active account-wide usage/auth pause NOW — the operator's
    "I fixed the cause" verb. The automatic pause assumes its reason still
    holds until the re-probe (quota: the cap reset; auth: a fixed cadence);
    when the operator has already repaired the cause — re-login, a rotated
    setup-token, a deployed fix — this clears the persisted ``paused_until``
    so dispatch resumes immediately instead of waiting out the clock.
    Safe: a no-op when no pause is active, and clearing a pause whose cause
    is still real merely lets the next limited call re-pause with a fresh
    timestamp. Distinct from the manual hold (set_operator_hold) and the
    run-window — those gate independently and are untouched."""
    until, reason = store.global_pause()
    store.clear_global_pause()
    return json.dumps(
        {"cleared": bool(until), "was_until_ms": until, "was_reason": reason},
        indent=2,
    )


@mcp.tool
async def set_quiet_mode(on: bool, until: "Optional[str]" = None, reason: str = "") -> str:
    """Arm (``on=true``) or disarm quiet mode (spec 025 US3). While armed,
    ONLY instance-dead pings reach the owner (an auth pause a re-probe can't
    heal; a failed self-deploy rollback) — every other ping class is recorded
    and readable on return via ``list_suppressed_pings``. ``until`` (ISO
    date/datetime, UTC assumed when naive) sets a self-disarm expiry —
    RECOMMENDED for a holiday window so a forgotten toggle can't mute the
    instance forever. Disarming keeps the suppressed backlog."""
    until_ms: "int | None" = None
    if on and until:
        from datetime import datetime, timezone

        try:
            dt = datetime.fromisoformat(until)
        except ValueError:
            raise ToolError(
                f"until must be an ISO date/datetime, got {until!r}"
            ) from None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        until_ms = int(dt.timestamp() * 1000)
    store.set_quiet_mode(bool(on), until_ms=until_ms, armed_at_ms=_now_ms())
    armed, until_out = store.quiet_mode()
    return json.dumps({
        "quiet": armed,
        "until_ms": until_out,
        "suppressed_so_far": store.suppressed_ping_count(),
    }, indent=2)


@mcp.tool
async def list_suppressed_pings(limit: int = 200) -> str:
    """The quiet-mode catch-up surface (spec 025 FR-014): every owner ping
    withheld while quiet mode was armed, oldest first, LIMIT-bounded. A
    record, not state — reading it changes nothing."""
    return json.dumps({
        "count": store.suppressed_ping_count(),
        "quiet": store.quiet_mode()[0],
        "pings": store.list_suppressed_pings(limit),
    }, indent=2, default=str)


@mcp.tool
async def set_max_concurrent(n: Optional[int] = None) -> str:
    """Set the global cap on concurrently-running sandboxed tasks — the
    backpressure dial. ``n=1`` is strictly serial (one sandbox at a time), the
    unattended-operation setting: concurrent sandboxes contend for ONE account
    quota while the usage-limit pause budget is counted per task, so a high cap
    trades reliability for parallelism. Omit ``n`` (or pass null) to CLEAR the
    override and fall back to the ``DEVCLAW_MAX_CONCURRENT`` default.

    Takes effect on the next queue pump — no restart, no redeploy. In-flight
    tasks always finish; lowering the cap never kills running work, it just
    stops new launches until the count drops below it.

    This is backpressure, not a safety gate: it cannot stop dispatch entirely
    (``n`` must be >= 1). To halt all new work use set_operator_hold; to gate it
    by time of day use set_run_schedule. Read the current value with
    get_run_schedule.

    Host-side cognition subprocesses are NOT counted here — they have their own
    cap (``DEVCLAW_MAX_HOST_COGNITION``)."""
    if n is not None and (isinstance(n, bool) or not isinstance(n, int) or n < 1):
        raise ToolError(
            "n must be a whole number >= 1, or null to clear the override — "
            "0 would wedge every dispatch; use set_operator_hold to stop work"
        )
    try:
        store.set_max_concurrent(n)
    except ValueError as exc:
        raise ToolError(str(exc)) from None
    from ...task_queue import GLOBAL_MAX_CONCURRENT

    override = store.max_concurrent()
    return json.dumps(
        {
            "max_concurrent": {
                "effective": override or GLOBAL_MAX_CONCURRENT,
                "override": override,
                "default": GLOBAL_MAX_CONCURRENT,
            }
        },
        indent=2,
    )
