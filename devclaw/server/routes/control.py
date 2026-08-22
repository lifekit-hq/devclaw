"""Instance health + dispatch control — the operator's knobs, not a resource.

``/health`` and ``/node.json`` (is this node alive, what is it running),
``/config/env.json`` (the read-only env catalog), and the dispatch controls:
the manual operator hold, the daily run window, and the automatic quota pause
(``/control.json``, ``/control/pause``, ``/control/resume``,
``/control/schedule``).

Global env is deliberately read-only here — changing it needs a container
restart, so the editable knobs are per-project and live in ``projects.py``.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ... import __version__
from .._state import SERVER_NAME, goals, mcp, store

def _health_freshness() -> dict:
    """Heartbeat freshness + build identity (#494) — one truth shared by
    ``/health`` and ``/node.json``, so the external dead-man watcher and the
    console read the same fields.

    Build identity comes from env baked into the image at build time
    (``DEVCLAW_GIT_SHA`` / ``DEVCLAW_BUILT_AT``); read per call so it needs no
    module reload. Absent values are ``null``, never faked. The cycle-report
    read inherits ``list_cycle_reports`` semantics: empty/missing table → no
    row (null here), real DB corruption raises loudly — and a /health that
    500s on a corrupt store is a true alarm, not a bug."""

    def _iso(ms: object) -> str | None:
        if not ms:
            return None
        return _dt.datetime.fromtimestamp(int(ms) / 1000, tz=_dt.timezone.utc).isoformat()

    from ...dispatch_gate import operator_block
    from ...state_store import _now_ms

    reports = store.list_cycle_reports(limit=1)
    last_report_ms = reports[0].get("created_at") if reports else None
    # Dispatch state on the token-free route so the external watchdog can
    # tell "held" from "stalled" (O3-class false positive) without auth.
    # Same computation get_run_schedule serves — not sensitive: it says
    # WHETHER dispatch is open, not what is being dispatched.
    blocked, why = operator_block(store.operator_hold(), store.get_run_schedule(), _now_ms())
    return {
        "git_sha": os.environ.get("DEVCLAW_GIT_SHA") or None,
        "built_at": os.environ.get("DEVCLAW_BUILT_AT") or None,
        "started_at": _iso(getattr(goals, "started_at_ms", None)),
        "last_tick_at": _iso(getattr(goals, "last_tick_at_ms", None)),
        "last_cycle_report_at": _iso(last_report_ms),
        "tick_seconds": getattr(goals, "tick_seconds", None),
        "dispatch_open": not blocked,
        "dispatch_hold_reason": why or None,
    }


@mcp.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> Response:
    return JSONResponse(
        {"ok": True, "name": SERVER_NAME, "version": __version__, **_health_freshness()}
    )


def _resolve_env_doc() -> Path:
    """Locate docs/reference/env-vars.md across install layouts. Under an editable
    install / the source tree it sits at the repo root above this module; under a
    NON-editable install the package is copied to site-packages WITHOUT docs/, but
    the server runs with cwd at the repo root (/app in the container), so the
    cwd-relative candidate finds it. Falls back to the module-relative path (→ the
    catalog degrades to [] if the doc is genuinely absent)."""
    default = Path(__file__).resolve().parents[2] / "docs" / "reference" / "env-vars.md"
    for c in (default, Path.cwd() / "docs" / "reference" / "env-vars.md"):
        try:
            if c.is_file():
                return c
        except OSError:
            continue
    return default


_ENV_DOC = _resolve_env_doc()
_SECRET_HINTS = ("TOKEN", "KEY", "SECRET", "PASSWORD")


def _strip_md(s: str) -> str:
    return s.replace("`", "").replace("**", "").strip()


def _env_var_catalog() -> list[dict]:
    """Parse the env-var reference doc into rows the console renders: group, key,
    default, purpose, and the CURRENT value (masked for secrets). Best-effort —
    a missing/renamed doc degrades to [] rather than 500-ing the settings view."""
    try:
        text = _ENV_DOC.read_text(encoding="utf-8")
    except OSError:
        return []
    rows: list[dict] = []
    group = ""
    for line in text.splitlines():
        st = line.strip()
        if st.startswith("## "):
            group = st[3:].strip()
            continue
        if not st.startswith("|") or "`" not in st:
            continue
        cells = [c.strip() for c in st.strip("|").split("|")]
        if len(cells) < 3:
            continue
        keycell = cells[0]
        if keycell.lower() == "var" or set(keycell) <= set("-: "):
            continue  # header / separator row
        key = keycell.split("`")[1].strip()  # first backticked token
        if not (key.isupper() and "_" in key and key.replace("_", "").isalnum()):
            continue
        default = _strip_md(cells[1])
        if default in ("—", "(unset)", "*(unset)*"):
            default = ""
        secret = any(h in key for h in _SECRET_HINTS)
        raw = os.environ.get(key, "")
        rows.append(
            {
                "group": group,
                "key": key,
                "default": default,
                "purpose": _strip_md("|".join(cells[2:])),
                "value": ("••••••" if raw else "") if secret else raw,
                "isSet": bool(raw),
                "secret": secret,
            }
        )
    return rows


# Lifecycle derivation lives in state_store.problems (the single home shared with
# the MCP `list_problems` tool, N1/#371); re-exported here under the original name
# so this route + its test keep importing it from this module.
@mcp.custom_route("/config/env.json", methods=["GET"])
async def config_env_json(_request: Request) -> Response:
    """Read-only catalog of every runtime env var + its current value (secrets
    masked). Editing global env needs a container restart, so this view is
    deliberately read-only; the editable knobs live per-project (below)."""
    return JSONResponse({"vars": _env_var_catalog()})


@mcp.custom_route("/control.json", methods=["GET"])
async def control_json(request: Request) -> Response:
    """Dispatch-control state for the console: the manual operator hold, the daily
    run-window, and the (automatic) quota pause — plus whether NEW dispatch is
    blocked right now and why. Read by the console's Dispatch panel."""
    from ...dispatch_gate import operator_block
    from ...state_store import _now_ms

    now = _now_ms()
    on, hold_reason = store.operator_hold()
    schedule = store.get_run_schedule()
    q_until, q_reason = store.global_pause()
    quota_active = q_until > now
    op_blocked, op_reason = operator_block((on, hold_reason), schedule, now)
    blocked = op_blocked or quota_active
    reason = op_reason if op_blocked else (f"quota: {q_reason}" if quota_active else "")
    return JSONResponse({
        "operatorHold": {"on": on, "reason": hold_reason},
        "schedule": schedule,
        "goalSchedules": store.list_goal_schedules(),
        "quotaPause": {"activeUntilMs": q_until if quota_active else 0, "reason": q_reason},
        "blocked": blocked,
        "reason": reason,
    })


def _node_vitals() -> dict:
    """Assemble the NODE view's vitals from projections that already exist (ADR
    0008 P1). Read-only: dispatch/heartbeat state, goal population, the
    clean-cycle headline, and a 5-layer strip. HONEST per the ADR — a layer gets
    a status only where one is genuinely derivable (L1 is serving this request;
    L2 is the dispatch/heartbeat state; L4 is whether any task is running). L3
    cognition and L5 worker have no idle probe yet, so they are ``unknown`` — a
    real per-layer health rollup is a deferred, separate build, NOT faked here."""
    from ...dispatch_gate import operator_block
    from ...state_store import _now_ms

    now = _now_ms()
    on, hold_reason = store.operator_hold()
    schedule = store.get_run_schedule()
    q_until, q_reason = store.global_pause()
    quota_active = q_until > now
    op_blocked, op_reason = operator_block((on, hold_reason), schedule, now)
    blocked = op_blocked or quota_active
    reason = op_reason if op_blocked else (f"quota: {q_reason}" if quota_active else "")

    # Goal population — bucketed the same way the morning digest triages
    # (cancelled/done are terminal; needs-you = blocked OR stalled OR a
    # stop-state verdict; everything else active is running).
    total = running = needs_you = done = cancelled = 0
    for g in goals.list_goals():
        total += 1
        phase = g.get("phase")
        prog = g.get("progress") or {}
        if phase == "cancelled":
            cancelled += 1
        elif phase in ("done", "achieved"):
            done += 1
        elif g.get("blocked_on") or prog.get("stalled") or g.get("direction") in ("stalled", "needs_human"):
            needs_you += 1
        else:
            running += 1

    # Clean-cycle headline + rolling rate over the cycle_reports window (ADR 0006).
    # IDLE cycles (the loop did no work — off/held/all-cancelled) are neither
    # clean nor wedged: they're excluded from BOTH numerator and denominator so
    # an off week of empty nights can't drift the rate toward a meaningless 100%.
    cycles = store.list_cycle_reports(limit=30)
    scored = [c for c in cycles if not c.get("idle")]
    if cycles:
        latest = cycles[0]
        latest_idle = bool(latest.get("idle"))
        # `clean` headline is None for an idle latest window (nothing to grade).
        clean = None if latest_idle else bool(latest["clean"])
        last_window_end = latest["window_end_ms"]
        clean_recent = sum(1 for c in scored if c["clean"])
    else:
        clean, last_window_end, clean_recent, latest_idle = None, None, 0, False

    running_tasks = len(store.list_tasks(status="running", limit=200))

    # L2 heartbeat status derives from the dispatch state; L4 from live tasks.
    l2 = "paused" if quota_active else ("held" if op_blocked else "up")
    l4 = "active" if running_tasks > 0 else "idle"

    return {
        "version": __version__,
        "dispatch": {
            "blocked": blocked,
            "reason": reason,
            "operatorHold": {"on": on, "reason": hold_reason},
            "schedule": schedule,
            "quotaPause": {"activeUntilMs": q_until if quota_active else 0, "reason": q_reason},
        },
        "goals": {
            "total": total,
            "running": running,
            "needsYou": needs_you,
            "done": done,
            "cancelled": cancelled,
        },
        "cleanCycle": {
            "clean": clean,
            "idle": latest_idle,
            "lastWindowEndMs": last_window_end,
            # `total` counts only SCORED (non-idle) windows — the honest
            # denominator; idle windows are surfaced separately, never counted.
            "recent": {"clean": clean_recent, "total": len(scored), "idle": len(cycles) - len(scored)},
        },
        "runningTasks": running_tasks,
        # Heartbeat freshness + build identity (#494) — same block /health
        # serves, so the console and the dead-man watcher read one truth.
        "freshness": _health_freshness(),
        # The 5-layer strip (CLAUDE.md layer map). ``unknown`` is honest, not a
        # gap to paper over: L3/L5 have no idle probe today.
        "layers": [
            {"n": 1, "key": "mcp", "name": "MCP surface", "status": "up"},
            {"n": 2, "key": "goal", "name": "GoalService + heartbeat", "status": l2},
            {"n": 3, "key": "cognition", "name": "Cognition callers", "status": "unknown"},
            {"n": 4, "key": "engine", "name": "TaskQueue + engine", "status": l4},
            {"n": 5, "key": "worker", "name": "Worker harness", "status": "unknown"},
        ],
    }


@mcp.custom_route("/node.json", methods=["GET"])
async def node_json(request: Request) -> Response:
    """The NODE view's vitals (ADR 0008 P1): dispatch/heartbeat, goal population,
    clean-cycle headline, and the 5-layer strip — all read-only over existing
    projections. The top of the console's drill-down spine."""
    return JSONResponse(_node_vitals())


@mcp.custom_route("/control/pause", methods=["POST"])
async def control_pause(request: Request) -> Response:
    """Turn on the manual operator hold — stops all NEW dispatch (in-flight tasks
    finish). Optional JSON body ``{"reason": "..."}``. Idempotent."""
    try:
        body = await request.json()
    except Exception:
        body = {}
    reason = str((body or {}).get("reason") or "").strip()
    store.set_operator_hold(True, reason)
    on, r = store.operator_hold()
    return JSONResponse({"operatorHold": {"on": on, "reason": r}})


@mcp.custom_route("/control/resume", methods=["POST"])
async def control_resume(request: Request) -> Response:
    """Clear the manual operator hold. Does NOT touch an active quota pause or the
    run-window — those gate independently, so dispatch resumes only if nothing
    else is holding it."""
    store.set_operator_hold(False)
    return JSONResponse({"operatorHold": {"on": False, "reason": ""}})


async def _apply_schedule(request: Request, goal_id: "str | None") -> Response:
    """Validate a schedule body and persist it (global when ``goal_id`` is None,
    else that goal's own window). Shared by the global and per-goal routes so the
    same fail-closed validation guards both — a typo must 400, never silently
    disable the window (the gate fails open)."""
    from zoneinfo import ZoneInfo

    from ...dispatch_gate import _parse_hhmm

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    b = body or {}
    cur = store.get_run_schedule(goal_id)
    enabled = bool(b.get("enabled", cur["enabled"]))
    start = str(b.get("start") or cur["start"])
    end = str(b.get("end") or cur["end"])
    tz = str(b.get("tz") or cur["tz"])
    if _parse_hhmm(start) is None or _parse_hhmm(end) is None:
        return JSONResponse(
            {"error": "bad_time", "hint": "start/end must be HH:MM"}, status_code=400
        )
    try:
        ZoneInfo(tz)
    except Exception:
        return JSONResponse(
            {"error": "bad_tz", "hint": "IANA name, e.g. Europe/Kyiv"}, status_code=400
        )
    store.set_run_schedule(enabled, start, end, tz, goal_id=goal_id)
    return JSONResponse({"schedule": store.get_run_schedule(goal_id)})


@mcp.custom_route("/control/schedule", methods=["POST"])
async def control_schedule(request: Request) -> Response:
    """Set the engine-wide daily run-window. Body:
    ``{"enabled": bool, "start": "HH:MM", "end": "HH:MM", "tz": "Area/City"}``.
    Missing fields keep their current value. A bad time or timezone is rejected
    (400) rather than silently accepted — the gate fails open, so a typo here
    would quietly disable the window."""
    return await _apply_schedule(request, None)
