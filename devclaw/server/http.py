"""HTTP custom routes — console, SSE event stream, Telegram answer hook.

The operator surface is the console SPA under ``/console`` (ADRs 0008–0010);
the legacy server-rendered dashboard routes 302 onto their console
equivalents (#549 — one operator surface). JSON/SSE routes here stay thin —
fetch data, serialize, respond.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import mimetypes
import os
import time
from pathlib import Path
from urllib.parse import quote as _quote

from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
)

from .. import __version__
from .. import telemetry as _telemetry
from ..goal import slice_guard
from ..goal.slice_guard import _TASKS_PATH_RE
from ..project_registry import project_rollup
from ._state import (
    SERVER_NAME,
    _goal_get,
    goals,
    mcp,
    registry,
    store,
)

# Route modules. These imports are NOT unused: `@mcp.custom_route` registers at
# import time, so a module nothing imports serves nothing. See routes/__init__.
from .routes import evals as _routes_evals  # noqa: F401,E402
from .routes import observability as _routes_observability  # noqa: F401,E402
from .routes import projects as _routes_projects  # noqa: F401,E402
from .routes import tasks as _routes_tasks  # noqa: F401,E402
from .routes._common import _task_row, json_limit  # noqa: E402
from .routes._projections import (  # noqa: E402
    _active_goal_count,
    _event_kind,
    _goal_action_label,
    _goal_last_update_ms,
    _goal_row,
    _last_activity_ms,
    _phase_index,
    _phase_label,
    _project_event_row,
    _safe_parse,
    _TERMINAL_PHASES,
    _TIMELINE_PHASES,
)


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

    from ..dispatch_gate import operator_block
    from ..state_store import _now_ms

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


# ---- Legacy dashboard → console redirects (#549, one operator surface) ----
# The server-rendered dashboard pages are retired behind 302s onto their
# console equivalents (their renderers are deleted). Deep links map where a
# mapping exists, else fall back to the console goals list; the incoming
# query string (the `?token=` auth) rides along so gated deployments stay
# reachable after the hop.


def _console_redirect(request: Request, to: str) -> Response:
    qs = request.url.query
    return RedirectResponse(url=f"{to}?{qs}" if qs else to, status_code=302)


@mcp.custom_route("/dashboard", methods=["GET"])
async def dashboard_index(request: Request) -> Response:
    return _console_redirect(request, "/console/goals")


@mcp.custom_route("/dashboard/{program_id}", methods=["GET"])
async def dashboard_program(request: Request) -> Response:
    """A program's operator view is the goal that dispatched it — GoalDetail
    carries the live event tail and per-task drill-ins the old program page
    showed. A parent-less legacy program falls back to the goals list; the
    raw SSE feed at /programs/{id}/events is unchanged for scripts."""
    program = store.get_program(request.path_params["program_id"])
    parent = getattr(program, "parent_goal_id", None) if program else None
    to = f"/console/goals/{_quote(parent)}" if parent else "/console/goals"
    return _console_redirect(request, to)


@mcp.custom_route("/programs/{program_id}/events", methods=["GET"])
async def program_events(request: Request) -> Response:
    """Resumable SSE stream of one program's events.

    Resume protocol: the EventSource Last-Event-Id header (sent by the browser
    on auto-reconnect) is the cursor; each frame's id is the event row's PK.
    Live tail: SQLite has no LISTEN/NOTIFY, so we poll every 750ms after the
    initial backlog (cheap, indexed). Termination: when the program is terminal
    AND the last poll returned nothing new, emit a final `done` frame and close.
    """
    from sse_starlette.sse import EventSourceResponse  # local import: http-only dep path

    program_id = request.path_params["program_id"]
    if not store.get_program(program_id):
        return PlainTextResponse(f"unknown program: {program_id}", status_code=404)

    leh = request.headers.get("last-event-id")
    cursor = int(leh) if (leh and leh.isdigit() and int(leh) > 0) else 0

    async def gen():
        nonlocal cursor
        yield {"comment": "ok"}  # forces EventSource onopen even with zero events
        while True:
            if await request.is_disconnected():
                return
            try:
                drained = store.list_events(program_id=program_id, since_id=cursor, limit=200)
            except Exception as err:
                yield {"event": "error", "data": json.dumps({"message": str(err)})}
                return
            for ev in drained:
                yield {
                    "id": str(ev.id),
                    "data": json.dumps(
                        {
                            "id": ev.id,
                            "type": ev.type,
                            "source": ev.source,
                            "ts": ev.ts,
                            "payload": _safe_parse(ev.payload_json),
                        }
                    ),
                }
                cursor = ev.id
            current = store.get_program(program_id)
            terminal = current is not None and current.status in ("done", "failed")
            if terminal and not drained:
                yield {"event": "done", "data": json.dumps({"status": current.status})}
                return
            await asyncio.sleep(0.75)

    return EventSourceResponse(gen())


@mcp.custom_route("/goals", methods=["GET"])
async def dashboard_goals(request: Request) -> Response:
    return _console_redirect(request, "/console/goals")


@mcp.custom_route("/projects", methods=["GET"])
async def dashboard_projects(request: Request) -> Response:
    return _console_redirect(request, "/console/projects")


# ---- Console (Vite + React SPA, served as a static bundle) ----------------
# The three-screen web console lives under `console/`. `npm run
# build` writes `console/dist/`; the bytes on disk are what these routes serve.
# The SPA does client-side routing under basename="/console", so any path that
# doesn't map to a file falls through to `index.html`.

_CONSOLE_DIST = Path(__file__).resolve().parent / "console_dist"


def _serve_console_file(rel: str) -> Response:
    if not _CONSOLE_DIST.exists():
        return PlainTextResponse(
            "devclaw console bundle not built — run `npm --prefix "
            "console run build`",
            status_code=503,
        )
    # Resolve safely inside dist. `Path.resolve()` normalizes `..`, then we
    # verify the resolved path stays inside the dist tree.
    target = (_CONSOLE_DIST / rel).resolve()
    try:
        target.relative_to(_CONSOLE_DIST)
    except ValueError:
        return PlainTextResponse("forbidden", status_code=403)
    if target.is_file():
        media, _ = mimetypes.guess_type(str(target))
        return FileResponse(str(target), media_type=media)
    # SPA fallback: unknown paths serve the app shell so client-side routing works.
    index = _CONSOLE_DIST / "index.html"
    if not index.is_file():
        return PlainTextResponse("console index.html missing from bundle", status_code=500)
    return FileResponse(str(index), media_type="text/html")


@mcp.custom_route("/", methods=["GET"])
async def root_redirect(_request: Request) -> Response:
    """The human-facing surface is the console; a bare hostname visit should
    land there, not on a 404 (live-found 2026-07-09: the operator's bookmark
    pointed at `/`)."""
    return RedirectResponse(url="/console", status_code=307)


@mcp.custom_route("/console", methods=["GET"])
async def console_index(_request: Request) -> Response:
    return _serve_console_file("index.html")


@mcp.custom_route("/console/{path:path}", methods=["GET"])
async def console_asset(request: Request) -> Response:
    return _serve_console_file(request.path_params["path"] or "index.html")


# ---- JSON API surfaces the console reads ----------------------------------


@mcp.custom_route("/goals/{goal_id}/cancel", methods=["POST"])
async def goal_cancel(request: Request) -> Response:
    """Console-facing cancel button. Wraps goal_service.cancel_goal — same
    entrypoint the MCP tool uses, so behavior (terminal-phase no-op, in-flight
    teardown) is identical whether the caller is Claude or the browser."""
    goal_id = request.path_params["goal_id"]
    try:
        result = goals.cancel_goal(goal_id)
    except KeyError:
        return JSONResponse({"error": "not_found", "id": goal_id}, status_code=404)
    return JSONResponse(result)


@mcp.custom_route("/goals/{goal_id}/steer", methods=["POST"])
async def goal_steer(request: Request) -> Response:
    """Console-facing steer button. Body is JSON `{"message": "..."}`.

    Steering is additive — appends to the goal's inbox and pokes the loop
    (goal_service.steer_goal), so it can flip a blocked goal back to idle.
    Empty or missing message returns 400 rather than a silent no-op."""
    goal_id = request.path_params["goal_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    message = (body or {}).get("message")
    if not isinstance(message, str) or not message.strip():
        return JSONResponse(
            {"error": "message_required", "hint": "POST {\"message\": str}"},
            status_code=400,
        )
    try:
        result = goals.steer_goal(goal_id, message.strip())
    except KeyError:
        return JSONResponse({"error": "not_found", "id": goal_id}, status_code=404)
    return JSONResponse(result)


@mcp.custom_route("/goals/{goal_id}/resume", methods=["POST"])
async def goal_resume(request: Request) -> Response:
    """Console-facing Resume button — the recovery verb. Wraps
    goal_service.resume_goal: re-attempts the SAME contract on a blocked goal
    whose blocker was cleared out-of-band (no steering recorded, objective
    untouched). Idempotent — a no-op on a goal that isn't blocked."""
    goal_id = request.path_params["goal_id"]
    try:
        result = goals.resume_goal(goal_id)
    except KeyError:
        return JSONResponse({"error": "not_found", "id": goal_id}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": "cannot_resume", "detail": str(exc)}, status_code=400)
    return JSONResponse(result)


@mcp.custom_route("/goals/{goal_id}/strictness", methods=["POST"])
async def goal_strictness(request: Request) -> Response:
    """Console-facing strictness toggle (ADR 0007). Body is JSON
    `{"strictness": "trust"|"strict"}`. Wraps goal_service.set_strictness — the
    same entrypoint the MCP tool uses. `trust` = dial-able gate failures ship
    with a caveat surfaced in the PR; `strict` = they block. A bad value or a
    missing field returns 400 rather than a silent no-op."""
    goal_id = request.path_params["goal_id"]
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    value = (body or {}).get("strictness")
    if value not in ("trust", "strict"):
        return JSONResponse(
            {"error": "strictness_required",
             "hint": "POST {\"strictness\": \"trust\"|\"strict\"}"},
            status_code=400,
        )
    try:
        result = goals.set_strictness(goal_id, value)
    except KeyError:
        return JSONResponse({"error": "not_found", "id": goal_id}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": "bad_strictness", "detail": str(exc)}, status_code=400)
    return JSONResponse(result)


# ── configuration surfaces ─────────────────────────────────────────────────
# A: a READ-ONLY catalog of the runtime env vars, parsed live from the enforced
#    single-source-of-truth doc (docs/reference/env-vars.md) so the table never
#    drifts. Secret values are masked — the value is never echoed to the browser.
# B: the EDITABLE per-project overrides (already DB-backed + live-resolved by the
#    registry; no restart needed). Global env stays read-only on purpose — those
#    are read at process start and can't be hot-edited, and a free-form env
#    editor would be a vector to inject a metered API key (the OAuth-only
#    invariant strips ANTHROPIC_* — never make them settable here).

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
    from ..dispatch_gate import operator_block
    from ..state_store import _now_ms

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
    from ..dispatch_gate import operator_block
    from ..state_store import _now_ms

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

    from ..dispatch_gate import _parse_hhmm

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


@mcp.custom_route("/goals/{goal_id}/schedule", methods=["GET"])
async def goal_schedule_get(request: Request) -> Response:
    """This goal's OWN run-window (a night/off-hours narrowing on top of the
    engine-wide window). A disabled default means the goal follows only the
    global window."""
    goal_id = request.path_params["goal_id"]
    return JSONResponse({"goalId": goal_id, "schedule": store.get_run_schedule(goal_id)})


@mcp.custom_route("/goals/{goal_id}/schedule", methods=["POST"])
async def goal_schedule_set(request: Request) -> Response:
    """Set THIS goal's own daily run-window — same body + validation as the global
    route. Confines a token-heavy standing goal to off-hours without gating the
    rest of the engine. Send ``{"enabled": false}`` to stop it restricting."""
    goal_id = request.path_params["goal_id"]
    return await _apply_schedule(request, goal_id)


@mcp.custom_route("/goals/{goal_id}/events", methods=["GET"])
async def goal_events(request: Request) -> Response:
    """SSE stream of events for the goal's CURRENT in_flight task/program.

    Contract: the stream is keyed to the ref that was in_flight at connect
    time. When the goal moves off that ref (new task, or no in_flight), we
    emit a `done` frame; the client reconnects to pick up the new ref.
    Resume: EventSource sends `last-event-id` on auto-reconnect; we use it as
    the SQLite events.id cursor (same pattern as the existing programs SSE)."""
    from sse_starlette.sse import EventSourceResponse  # local import: http-only

    goal_id = request.path_params["goal_id"]
    try:
        g = goals.get_goal(goal_id)
    except KeyError:
        return PlainTextResponse(f"unknown goal: {goal_id}", status_code=404)

    in_flight = g.get("in_flight")
    if not in_flight:
        # No live task — return an empty stream that immediately closes with a
        # `done` frame. The client can reconnect once phase/in_flight change.
        async def empty_gen():
            yield {"comment": "no in_flight"}
            yield {"event": "done", "data": json.dumps({"reason": "no_in_flight"})}

        return EventSourceResponse(empty_gen())

    # Pin the ref at connect time. list_events wants program_id OR task_id.
    ref_kind = in_flight.get("ref_kind") or ("task" if in_flight.get("id") else "program")
    ref_id = in_flight.get("id")
    list_kwargs = (
        {"task_id": ref_id} if ref_kind == "task" else {"program_id": ref_id}
    )

    leh = request.headers.get("last-event-id")
    cursor = int(leh) if (leh and leh.isdigit() and int(leh) > 0) else 0

    async def gen():
        nonlocal cursor
        yield {"comment": "ok"}
        while True:
            if await request.is_disconnected():
                return
            try:
                drained = store.list_events(since_id=cursor, limit=200, **list_kwargs)
            except Exception as err:
                yield {"event": "error", "data": json.dumps({"message": str(err)})}
                return
            for ev in drained:
                payload = _safe_parse(ev.payload_json)
                yield {
                    "id": str(ev.id),
                    "data": json.dumps(
                        _project_event_row(
                            ev, kind=_event_kind(ev.type), payload=payload
                        )
                    ),
                }
                cursor = ev.id
            # Re-check the goal's in_flight — if it changed under us, close so
            # the client reconnects and re-pins.
            try:
                current = goals.get_goal(goal_id)
            except KeyError:
                yield {"event": "done", "data": json.dumps({"reason": "goal_gone"})}
                return
            current_ref = (current.get("in_flight") or {}).get("id")
            if current_ref != ref_id:
                yield {
                    "event": "done",
                    "data": json.dumps({"reason": "in_flight_rotated"}),
                }
                return
            await asyncio.sleep(0.75)

    return EventSourceResponse(gen())


@mcp.custom_route("/goals/{goal_id}.json", methods=["GET"])
async def goal_json(request: Request) -> Response:
    """Goal Detail feed — header, objective, phase-timeline shape, pills.

    Reuses goal_service.get_goal so the observe surface stays a single source
    of truth. Timeline node timestamps arrive in PR#7 (phase_history)."""
    goal_id = request.path_params["goal_id"]
    try:
        g = goals.get_goal(goal_id)
    except KeyError:
        return JSONResponse({"error": "not_found", "id": goal_id}, status_code=404)
    phase = g.get("phase")
    current_index = _phase_index(phase)
    # Timeline slots are the fixed 5-slot design contract. For each slot, if the
    # goal's phase_history recorded arriving at that phase, we stamp the FIRST
    # arrival. Repeated visits (idle → executing → idle → executing) don't
    # rewrite the label — matches the design's "when did this phase happen"
    # semantic, not "most recent".
    history_first_at: dict[str, str] = {}
    for entry in g.get("phase_history") or []:
        pn = str(entry.get("phase") or "")
        if pn and pn not in history_first_at and entry.get("at"):
            history_first_at[pn] = str(entry["at"])

    def _iso_to_ms(iso: str) -> int | None:
        try:
            ts = _dt.datetime.fromisoformat(iso)
        except ValueError:
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        return int(ts.timestamp() * 1000)

    timeline = []
    for i, name in enumerate(_TIMELINE_PHASES):
        stamp_iso = history_first_at.get(name)
        timeline.append(
            {
                "name": name,
                "reached": i <= current_index,
                "current": i == current_index,
                "timestampMs": _iso_to_ms(stamp_iso) if stamp_iso else None,
            }
        )
    # Dispatch cap = the runaway backstop the goal tick enforces
    # (len(backlog)+2 — see goal/tick_dispatch._dispatch_action). Surface it so
    # the console can show "N / cap" and, when phase=blocked, the banner can
    # render "N of N dispatched — merge to unblock".
    dispatch_cap = len(g.get("backlog") or []) + 2
    # Dispatched tasks — every Task the goal heartbeat filed against this goal
    # (parent_goal_id match). Includes both live and terminal tasks; the
    # console renders them as a timeline of what the goal actually dispatched.
    # One fetch serves both the task timeline (newest 50) and the usage sum
    # below (all 500 — a goal past the dispatch cap rarely nears that, and a
    # 50-row sum would quietly understate the "what did this goal cost"
    # number the block exists to answer).
    task_rows = store.list_tasks(parent_goal_id=goal_id, limit=500)
    dispatched_tasks = [_task_row(t) for t in task_rows[:50]]
    # Frozen empty shape kept for console compat: ADR 0010's planner-emitted
    # decision menus died with the 008 shrink, but the JSON field stays.
    block_options: dict = {}
    # Usage rollup — cognition from the goal's trace totals, worker from the
    # per-task "usage" blocks the runner records into result_json. Pure reads;
    # best-effort: a torn trace/row degrades to null, never 500s the view.
    usage: dict | None = None
    try:
        totals = store.trace_totals(goal_id=goal_id)
        worker = _telemetry.sum_task_usage(t.result_json for t in task_rows)
        usage = {
            "cognitionTokensIn": totals["cognition_tokens_in"],
            "cognitionTokensOut": totals["cognition_tokens_out"],
            "cognitionCostUsd": totals["cognition_cost_usd"],
            "workerInputTokens": worker["input_tokens"],
            "workerOutputTokens": worker["output_tokens"],
            "workerCostUsd": worker["cost_usd"],
            "tasksWithUsage": worker["tasks_with_usage"],
            "totalTokens": (
                totals["cognition_tokens_in"] + totals["cognition_tokens_out"]
                + worker["input_tokens"] + worker["output_tokens"]
            ),
            "totalCostUsd": round(totals["cognition_cost_usd"] + worker["cost_usd"], 6),
        }
    except Exception:
        usage = None
    return JSONResponse(
        {
            "id": g["id"],
            "objective": g.get("objective") or "",
            "phase": phase,
            "phaseLabel": _phase_label(phase),
            "lifecycle": g.get("lifecycle"),
            "direction": g.get("direction"),
            "strictness": g.get("strictness", "trust"),
            "actionsDispatched": g.get("actions_dispatched", 0),
            "dispatchCap": dispatch_cap,
            "inFlight": g.get("in_flight"),
            "timeline": timeline,
            "blockedOn": g.get("blocked_on"),
            "blockedKind": g.get("blocked_kind", ""),
            "blockOptions": block_options,
            "usage": usage,
            "tasks": dispatched_tasks,
        }
    )


_GH_PR_URL_RE = __import__("re").compile(
    r"^https?://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+)/pull/(\d+)/?$"
)


def _parse_pr_url(url: str) -> tuple[str, str, int] | None:
    """Return (owner, repo, number) or None. Rejects non-github.com URLs — the
    merge endpoint uses this as its allow-check so a spoofed pr_url can't
    trick us into shelling `gh` at an arbitrary host/repo."""
    if not isinstance(url, str):
        return None
    m = _GH_PR_URL_RE.match(url.strip())
    if not m:
        return None
    return m.group(1), m.group(2), int(m.group(3))


def _collect_goal_pr_rows(goal_id: str) -> list[dict]:
    """Read delivery traces for this goal, extract PRs, dedupe by URL — the
    LAST delivery for a given PR wins so if a mission re-mentions a PR (e.g.
    on a retry) the newer action_label surfaces. Merge/close-state enrichment
    happens in the endpoint (per-row `gh pr view` probe); this step only
    reads local state.

    Dedup relies on ``read_traces`` returning ascending id order — trace ids
    are monotonic, so we don't need to compare wall-clock ts (which can tie
    inside the same millisecond)."""
    seen: dict[str, dict] = {}
    for ev in store.read_traces(goal_id=goal_id, kind="delivery", limit=1000):
        payload = ev.get("payload") or {}
        pr_url = str(payload.get("pr_url") or "").strip()
        if not pr_url:
            continue
        parsed = _parse_pr_url(pr_url)
        if parsed is None:
            continue
        owner, repo, number = parsed
        seen[pr_url] = {
            "prUrl": pr_url,
            "prNumber": number,
            "repo": f"{owner}/{repo}",
            "actionLabel": str(payload.get("action_label") or ""),
            "gatePassed": payload.get("gate_passed"),
            "ts": ev.get("ts") or "",
            "_id": ev.get("id") or 0,
        }
    rows = list(seen.values())
    rows.sort(key=lambda r: r.get("_id") or 0, reverse=True)
    for r in rows:
        r.pop("_id", None)
    return rows


async def _probe_pr_state(repo: str, number: int) -> dict:
    """Live-fetch PR state via `gh pr view`. Failures degrade to unknown state
    so a network hiccup or a deleted branch never blocks the whole page."""
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "view", str(number),
        "--repo", repo,
        "--json", "state,mergeable,mergeStateStatus,title,mergedAt",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=12.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {"state": "UNKNOWN", "mergeable": "UNKNOWN", "error": "timeout"}
    if proc.returncode != 0:
        return {
            "state": "UNKNOWN",
            "mergeable": "UNKNOWN",
            "error": (stderr.decode("utf-8", "replace") or "gh failed").strip()[:200],
        }
    try:
        return json.loads(stdout.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return {"state": "UNKNOWN", "mergeable": "UNKNOWN", "error": "parse"}


@mcp.custom_route("/goals/{goal_id}/prs.json", methods=["GET"])
async def goal_prs_json(request: Request) -> Response:
    """PRs opened by this goal, with live GitHub state so the console can show
    the correct Merge button per row without another round-trip.

    Sources: `traces(kind='delivery')` rows carry `pr_url` — parsed and deduped
    locally. Each surviving row is enriched with a live `gh pr view` probe
    (state/mergeable/title/mergedAt) so `state==OPEN and mergeable==MERGEABLE`
    is the exact condition the Merge button enables on. Probes run in parallel
    to keep page-load reasonable when a mission has many PRs open. Traces are
    the intentional source over `deliveries.md`: structured payload, not
    markdown extraction; and stays consistent with the SSE feed."""
    goal_id = request.path_params["goal_id"]
    try:
        goals.get_goal(goal_id)
    except KeyError:
        return JSONResponse({"error": "not_found", "id": goal_id}, status_code=404)

    rows = _collect_goal_pr_rows(goal_id)
    if not rows:
        return JSONResponse({"prs": []})

    states = await asyncio.gather(
        *[_probe_pr_state(r["repo"], r["prNumber"]) for r in rows]
    )
    for row, state in zip(rows, states):
        row["state"] = state.get("state") or "UNKNOWN"
        row["mergeable"] = state.get("mergeable") or "UNKNOWN"
        row["mergeStateStatus"] = state.get("mergeStateStatus") or None
        row["title"] = state.get("title") or ""
        row["mergedAt"] = state.get("mergedAt") or None
        if state.get("error"):
            row["error"] = state["error"]
    return JSONResponse({"prs": rows})


async def _git_show(workspace_dir: str, ref: str) -> str | None:
    """`git -C <ws> show <ref>` → the file contents, or None on any failure —
    reads a file off a ref without checking it out. Best-effort, never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", workspace_dir, "show", ref,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        return None
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=8.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return None
    if proc.returncode != 0:
        return None
    text = stdout.decode("utf-8", "replace")
    return text if text.strip() else None


async def _tasks_paths_at_ref(workspace_dir: str, ref: str) -> "list[str]":
    """Tracked ``specs/*/tasks.md`` paths at ``ref``, newest feature last.

    Ordered lexically, so the highest-numbered feature dir sorts last — the
    working-tree mtime ordering :func:`slice_guard.current_feature_dir_sync`
    uses is unavailable off a ref. Best-effort, never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "-C", workspace_dir, "ls-tree", "-r", "--name-only", ref,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except (OSError, asyncio.TimeoutError):
        return []
    if proc.returncode != 0:
        return []
    names = stdout.decode("utf-8", "replace").splitlines()
    return sorted(n.strip() for n in names if _TASKS_PATH_RE.match(n.strip()))


async def _read_plan(workspace_dir: str, goal_id: str) -> dict:
    """The goal's current speckit plan, for the console's Plan view.

    Reads the ACTIVE feature's ``specs/NNN-*/tasks.md`` — the worker-owned
    execution contract since spec 008. This used to read ``PLAN.md``; nothing
    has written that file since the speckit shrink, so the console's DEFAULT
    tab returned ``content: None`` for every goal. The tab was not broken by
    deleting PLAN.md — it had been empty since 008, and pointing it at the file
    the worker actually maintains is what restores it.

    Tries the goal's LIVE delivery branch first (so an in-flight plan shows,
    not only a merged one), then whatever is checked out, then the working
    tree. A repo with no speckit contract returns ``content=None`` (a goal that
    has not planned yet), never an error."""
    # The workspace resets to the default branch between actions, so a short,
    # bounded fetch keeps origin/goal/<id> current for an in-flight goal. Never
    # let a plan view hang on the network.
    try:
        fetch = await asyncio.create_subprocess_exec(
            "git", "-C", workspace_dir, "fetch", "--quiet", "origin", f"goal/{goal_id}",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(fetch.communicate(), timeout=8.0)
    except (OSError, asyncio.TimeoutError):
        pass
    for source, ref in (
        ("branch", f"origin/goal/{goal_id}"),
        ("branch", f"goal/{goal_id}"),
        ("head", "HEAD"),
    ):
        paths = await _tasks_paths_at_ref(workspace_dir, ref)
        if not paths:
            continue
        content = await _git_show(workspace_dir, f"{ref}:{paths[-1]}")
        if content:
            return {"content": content, "source": source, "ref": ref,
                    "path": paths[-1]}
    try:
        rel = slice_guard.current_feature_dir_sync(workspace_dir)
        if rel:
            path = os.path.join(workspace_dir, rel, "tasks.md")
            if os.path.isfile(path):
                with open(path, encoding="utf-8", errors="replace") as fh:
                    content = fh.read()
                if content.strip():
                    return {"content": content, "source": "worktree", "ref": None,
                            "path": f"{rel}/tasks.md"}
    except OSError:
        pass
    return {"content": None, "source": None, "ref": None, "path": None}


@mcp.custom_route("/goals/{goal_id}/plan.json", methods=["GET"])
async def goal_plan_json(request: Request) -> Response:
    """The goal's speckit plan — the worker-owned execution contract
    (``specs/NNN-*/tasks.md``); plan-state lives in files the worker maintains
    in the repo, not the control plane. Surfaced read-only so the operator can
    read and evaluate the plan itself. Human-initiated (the Plan tab), so the git read is off the tick path
    — no zero-token concern. Never mutates goal state."""
    goal_id = request.path_params["goal_id"]
    try:
        g = goals.get_goal(goal_id)
    except KeyError:
        return JSONResponse({"error": "not_found", "id": goal_id}, status_code=404)
    workspace_dir = g.get("workspace_dir") or ""
    if not workspace_dir or not os.path.isdir(workspace_dir):
        return JSONResponse({"content": None, "source": None, "ref": None})
    return JSONResponse(await _read_plan(workspace_dir, goal_id))


@mcp.custom_route("/prs/merge", methods=["POST"])
async def pr_merge(request: Request) -> Response:
    """Console-facing merge button. Body: `{"prUrl": "https://github.com/…"}`.

    Guarded by `_parse_pr_url`: only URLs matching a canonical github.com PR
    path are accepted, so a spoofed body can't turn this into an arbitrary
    shell. Squash + delete-branch matches the merge policy we already use for
    the closeloop mission chain — one-shot slice per PR."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)
    pr_url = (body or {}).get("prUrl")
    parsed = _parse_pr_url(pr_url if isinstance(pr_url, str) else "")
    if parsed is None:
        return JSONResponse(
            {"error": "invalid_pr_url", "hint": "expected https://github.com/<owner>/<repo>/pull/<n>"},
            status_code=400,
        )
    owner, repo, number = parsed
    slug = f"{owner}/{repo}"
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "merge", str(number),
        "--repo", slug,
        "--squash",
        "--delete-branch",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=45.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return JSONResponse(
            {"merged": False, "error": "timeout"}, status_code=504
        )
    if proc.returncode != 0:
        err = (stderr.decode("utf-8", "replace") or stdout.decode("utf-8", "replace")).strip()
        return JSONResponse(
            {"merged": False, "error": err[:400] or "gh pr merge failed"},
            status_code=502,
        )
    return JSONResponse(
        {"merged": True, "prUrl": pr_url, "output": stdout.decode("utf-8", "replace").strip()[:200]}
    )




def _goals_dir() -> Path:
    """The directory holding per-goal transcripts, resolved exactly as the live
    GoalService does (``DEVCLAW_GOALS_DIR`` → ``self._cfg.goals_dir``). A module
    accessor so http tests can monkeypatch the ``goals`` service and have these
    routes follow it."""
    return Path(goals._cfg.goals_dir)


def _transcripts_dir(goal_id: str) -> Path:
    """``<goals_dir>/<goal_id>/transcripts``. ``goal_id`` is validated by the
    caller before it reaches here (no path separators / traversal)."""
    return _goals_dir() / goal_id / "transcripts"


def _valid_goal_id(goal_id: str) -> bool:
    """A goal id is a single path segment — anything with a separator or a
    parent-dir hop is rejected before it can compose a path outside the goals
    dir."""
    return bool(goal_id) and "/" not in goal_id and "\\" not in goal_id and ".." not in goal_id


@mcp.custom_route("/goals/{goal_id}/transcripts.json", methods=["GET"])
async def goal_transcripts_json(request: Request) -> Response:
    """Index of a goal's cognition calls — one row per ``claude --print`` call,
    oldest first, with size/cost/error metadata but NOT the full text (that's the
    per-file route below). Reuses ``trace_view.load_dir`` so the parse can never
    drift from what ``PersistentTracer.write_transcript`` emits.

    A goal that has never run cognition (no transcripts dir yet) returns an empty
    list, not a 404 — the console links here from a known goal and an empty index
    is the honest answer. Read-only."""
    from .. import trace_view

    goal_id = request.path_params["goal_id"]
    if not _valid_goal_id(goal_id):
        return JSONResponse({"error": "bad_goal_id"}, status_code=400)
    # Point straight at ``<goals_dir>/<goal_id>/transcripts`` rather than letting
    # the resolver fall through to the goal dir — a goal dir that exists but has
    # no transcripts subdir would otherwise glob its generated VIEW files
    # (STATUS.md/log.md/inbox.md) as bogus rows. Absent subdir ⇒ empty index.
    tdir = _transcripts_dir(goal_id)
    transcripts = trace_view.load_dir(tdir) if tdir.is_dir() else []
    rows = [
        {
            "seq": i,
            "filename": t.filename,
            "ts": t.ts,
            "role": t.role,
            "model": t.model,
            "promptChars": t.prompt_chars,
            "responseChars": t.response_chars,
            "tokensIn": t.tokens_in,
            "tokensOut": t.tokens_out,
            "costUsd": t.cost_usd,
            "error": t.error,
        }
        for i, t in enumerate(transcripts, 1)
    ]
    return JSONResponse({"transcripts": rows, "count": len(rows)})


@mcp.custom_route("/goals/{goal_id}/transcripts/{filename}", methods=["GET"])
async def goal_transcript_full(request: Request) -> Response:
    """The FULL prompt + response of one cognition call — no truncation, that's
    the whole point. ``filename`` must be a bare ``*.md`` basename inside this
    goal's transcripts dir; anything with a path separator, a ``..`` hop, or a
    non-``.md`` suffix is rejected (path-traversal guard), and the resolved path
    is re-checked to live under the transcripts dir before it is read. Read-only."""
    from .. import trace_view

    goal_id = request.path_params["goal_id"]
    filename = request.path_params["filename"]
    if not _valid_goal_id(goal_id):
        return JSONResponse({"error": "bad_goal_id"}, status_code=400)
    # Reject traversal / nested paths / non-markdown up front — a transcript is
    # always a flat ``<ts>-<role>.md`` basename.
    if (
        not filename
        or "/" in filename
        or "\\" in filename
        or ".." in filename
        or not filename.endswith(".md")
    ):
        return JSONResponse({"error": "bad_filename"}, status_code=400)

    tdir = _transcripts_dir(goal_id).resolve()
    target = (tdir / filename).resolve()
    # Belt-and-suspenders: even after the string guard, prove the resolved path
    # is inside the transcripts dir and is a real file before reading it.
    if not (target == tdir / filename and target.parent == tdir and target.is_file()):
        return JSONResponse({"error": "not_found"}, status_code=404)

    from . import prompt_anatomy

    t = trace_view.parse_transcript(target)
    # Byte-anatomy of the prompt: which sections are instructions we author vs
    # goal state re-fed into every stateless call (the "why is this 105 KB"
    # answer, made visible on the surface the operator already reads). Pure,
    # post-hoc, never-raises — no change to the capture path.
    anatomy = prompt_anatomy.to_dict(prompt_anatomy.anatomize(t.prompt, t.role))
    return JSONResponse(
        {
            "filename": t.filename,
            "ts": t.ts,
            "role": t.role,
            "model": t.model,
            "goalId": t.goal_id,
            "tokensIn": t.tokens_in,
            "tokensOut": t.tokens_out,
            "costUsd": t.cost_usd,
            "error": t.error,
            "promptChars": t.prompt_chars,
            "responseChars": t.response_chars,
            "prompt": t.prompt,
            "response": t.response,
            "anatomy": anatomy,
            "extra": t.extra,
        }
    )


# ── worker execution trace (the WORKER's turn-by-turn run of one task) ────────
# The mirror of the cognition-transcript surface above, one layer down: the
# cognition routes show the CONTROL-PLANE claude --print calls; this shows the
# WORKER's actual in-sandbox execution. The full turn log is already captured in
# the append-only events table (one row per SDK ActionEvent/ObservationEvent/
# MessageEvent) — this route reads it back POST-HOC (the SSE /goals/{id}/events
# stream is live-only, pinned to the current in_flight ref) and decodes each raw
# event into readable {title, summary, detail, raw} via worker_events. Read-only
# observability, same bar as get_trace: no state write, no goal-loop wake, no
# LLM/subprocess.


@mcp.custom_route("/goals/{goal_id}", methods=["GET"])
async def dashboard_goal(request: Request) -> Response:
    """Legacy HTML goal detail → the console's GoalDetail (same data, richer:
    plan, PRs, transcripts, task drill-ins). The JSON feed stays at
    /goals/{goal_id}.json."""
    goal_id = request.path_params["goal_id"]
    return _console_redirect(request, f"/console/goals/{_quote(goal_id)}")
