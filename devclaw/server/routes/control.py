"""Instance health + dispatch control: ``/health``, ``/node.json``,
``/control.json``, ``/control/pause|resume|schedule``, and the self-deploy
arm ``/control/deploy-pending`` the deploy workflow posts on every push to main."""

from __future__ import annotations

import datetime as _dt

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import __version__
from ... import config as _config
from ...dispatch_gate import _parse_hhmm, operator_block
from ...state_store import _now_ms
from .._state import SERVER_NAME, goals, mcp, store


def _iso(ms) -> "str | None":
    if not ms:
        return None
    return _dt.datetime.fromtimestamp(int(ms) / 1000, tz=_dt.timezone.utc).isoformat()


def _vitals() -> dict:
    now = _now_ms()
    blocked, why = operator_block(store.operator_hold(), store.get_run_schedule(), now)
    until, reason = store.global_pause()
    paused = bool(until and until > now)
    return {
        "git_sha": _config.git_sha(),
        "built_at": _config.built_at(),
        "started_at": _iso(goals.started_at_ms),
        "last_tick_at": _iso(goals.last_tick_at_ms),
        "tick_seconds": goals.tick_seconds,
        "dispatch_open": not blocked and not paused,
        "dispatch_hold_reason": why or (f"paused: {reason}" if paused else None),
        "running": store.count_running(),
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> Response:
    return JSONResponse({"ok": True, "name": SERVER_NAME, "version": __version__, **_vitals()})


@mcp.custom_route("/node.json", methods=["GET"])
async def node_json(_request: Request) -> Response:
    return JSONResponse({**_vitals(), "goals": goals.list_goals()})


@mcp.custom_route("/control.json", methods=["GET"])
async def control_json(_request: Request) -> Response:
    now = _now_ms()
    hold = store.operator_hold()
    schedule = store.get_run_schedule()
    blocked, why = operator_block(hold, schedule, now)
    until, reason = store.global_pause()
    return JSONResponse({
        "operatorHold": {"on": hold[0], "reason": hold[1]},
        "schedule": schedule,
        "pause": {"untilMs": until, "reason": reason} if until and until > now else None,
        "blocked": blocked or bool(until and until > now),
        "whyBlocked": why or None,
        "maxConcurrent": store.max_concurrent(),
        "deployPending": store.deploy_pending(),
        "deployLast": store.deploy_last(),
    })


@mcp.custom_route("/control/pause", methods=["POST"])
async def control_pause(request: Request) -> Response:
    try:
        body = await request.json()
    except Exception:
        body = {}
    store.set_operator_hold(True, str((body or {}).get("reason") or "").strip())
    on, r = store.operator_hold()
    return JSONResponse({"operatorHold": {"on": on, "reason": r}})


@mcp.custom_route("/control/resume", methods=["POST"])
async def control_resume(_request: Request) -> Response:
    store.set_operator_hold(False)
    return JSONResponse({"operatorHold": {"on": False, "reason": ""}})


@mcp.custom_route("/control/deploy-pending", methods=["POST"])
async def control_deploy_pending(request: Request) -> Response:
    """Arm a self-deploy: main moved; the heartbeat deploys once no session
    runs. A newer sha overwrites — deploying the latest main covers both."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    sha = str((body or {}).get("sha") or "").strip()
    store.set_deploy_pending(sha=sha, goal_id="ci", since_ms=_now_ms())
    pending = store.deploy_pending()
    return JSONResponse({"deployPending": {"sha": pending[0] if pending else "",
                                           "armedBy": pending[1] if pending else ""}})


@mcp.custom_route("/control/schedule", methods=["POST"])
async def control_schedule(request: Request) -> Response:
    from zoneinfo import ZoneInfo

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    b = body or {}
    cur = store.get_run_schedule()
    enabled = bool(b.get("enabled", cur["enabled"]))
    start = str(b.get("start") or cur["start"])
    end = str(b.get("end") or cur["end"])
    tz = str(b.get("tz") or cur["tz"])
    if _parse_hhmm(start) is None or _parse_hhmm(end) is None:
        return JSONResponse({"error": "bad_time", "hint": "start/end must be HH:MM"}, status_code=400)
    try:
        ZoneInfo(tz)
    except Exception:
        return JSONResponse({"error": "bad_tz", "hint": "IANA name, e.g. Europe/Dublin"}, status_code=400)
    store.set_run_schedule(enabled, start, end, tz)
    return JSONResponse({"schedule": store.get_run_schedule()})
