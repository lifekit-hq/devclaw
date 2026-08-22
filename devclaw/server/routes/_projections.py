"""Row + label projections shared across route modules.

The console renders goals, tasks and projects in several places (the goal
page, the project page, the goals list) and every one needs the same shaped
row. These are the shared projections: pure functions over store rows, no
I/O, no route registration.

Kept apart from ``_common`` on purpose — ``_common`` holds small request
utilities, this holds the domain projections the console's data model is
made of.
"""

from __future__ import annotations

import datetime as _dt
import json

from .._state import _goal_get

def _safe_parse(s: str) -> object:
    try:
        return json.loads(s)
    except Exception:
        return s


def _last_activity_ms(goals_list: list[dict]) -> int | None:
    """Newest `progress.last_at` (ISO ts) across a project's linked goals,
    converted to epoch ms. `None` when no goal has fired progress yet.

    Kept here (not on Project) so the registry stays free of goal-shape
    knowledge — reading live phase/progress is the rollup's job."""
    best: int | None = None
    for g in goals_list:
        if g.get("missing"):
            continue
        last_at = (g.get("progress") or {}).get("last_at")
        if not isinstance(last_at, str):
            continue
        try:
            ts = _dt.datetime.fromisoformat(last_at)
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_dt.timezone.utc)
        ms = int(ts.timestamp() * 1000)
        if best is None or ms > best:
            best = ms
    return best


def _active_goal_count(goals_list: list[dict]) -> int:
    """A goal is 'active' from the console's POV when it isn't terminal — the
    Projects Home column matches the design's semantics ('Active goals')."""
    terminal = {"done", "cancelled", "error", "achieved"}
    return sum(
        1
        for g in goals_list
        if not g.get("missing") and (g.get("phase") not in terminal)
    )


_TERMINAL_PHASES = {"done", "cancelled", "error", "achieved"}


def _phase_label(phase: str | None) -> str:
    """Map internal phase to the design's label vocabulary. `done` is presented
    as `Achieved` per the mock (Project Detail archived section).
    """
    if phase is None:
        return "—"
    return {"done": "Achieved"}.get(phase, phase.capitalize())


def _goal_action_label(goal_id: str) -> str:
    """One-line 'what's this goal currently doing' — the design's In-flight
    action column. Terminal goals fall back to their last direction note; active
    goals surface the human `next` hint, then the in_flight tool. Returns '—'
    when nothing useful is known."""
    try:
        g = _goal_get(goal_id)
    except KeyError:
        return "—"
    phase = g.get("phase")
    if phase in _TERMINAL_PHASES:
        direction = g.get("direction") or {}
        note = direction.get("note") or ""
        return note.strip() or "—"
    nxt = (g.get("next") or "").strip()
    if nxt:
        return nxt
    in_flight = g.get("in_flight") or {}
    tool = in_flight.get("tool")
    return tool if tool else "—"


def _goal_last_update_ms(goal_id: str) -> int | None:
    try:
        g = _goal_get(goal_id)
    except KeyError:
        return None
    last_at = (g.get("progress") or {}).get("last_at")
    if not isinstance(last_at, str):
        return None
    try:
        ts = _dt.datetime.fromisoformat(last_at)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=_dt.timezone.utc)
    return int(ts.timestamp() * 1000)


def _goal_row(goal_id: str) -> dict:
    try:
        g = _goal_get(goal_id)
    except KeyError:
        return {
            "id": goal_id,
            "objective": "",
            "phase": None,
            "phaseLabel": "Missing",
            "action": "—",
            "lastUpdateMs": None,
        }
    phase = g.get("phase")
    return {
        "id": goal_id,
        # The durable goal statement — a goal's IDENTITY, distinct from `action`
        # (its current motion). The list views render this as the primary label
        # so the operator reads "what each goal IS" instead of a wall of IDs.
        "objective": g.get("objective") or "",
        "phase": phase,
        "phaseLabel": _phase_label(phase),
        "action": _goal_action_label(goal_id),
        "lastUpdateMs": _goal_last_update_ms(goal_id),
    }


# Fixed left-to-right order the Goal Detail phase-timeline renders. Keep in
# sync with `phaseNames` in the Claude Design mock (Goal Detail.dc.html:373).
_TIMELINE_PHASES = ["executing", "verifying", "done"]


def _phase_index(current: str | None) -> int:
    """Where along the timeline the goal is right now. Non-timeline phases
    (idle, in_flight, blocked, cancelled, error — and legacy pre-shrink
    investigating/firming stamps) collapse to 'executing'."""
    if current is None:
        return 0
    if current in _TIMELINE_PHASES:
        return _TIMELINE_PHASES.index(current)
    return _TIMELINE_PHASES.index("executing")


# Design taxonomy from Goal Detail.dc.html: cognition/subprocess/dispatch/
# delivery/notify. Real backend event types are runner-specific and irregular,
# so we normalize here with a best-effort mapper. PR#7 will tighten this by
# stamping the kind at emit time.
_KIND_EXACT = {
    "cancelled": "notify",
    "reaped": "notify",
    "workspace_break_tripped": "notify",
    "StdoutLine": "subprocess",
    "StderrLine": "subprocess",
    "StubBuildEvent": "dispatch",
}


def _event_kind(event_type: str) -> str:
    if event_type in _KIND_EXACT:
        return _KIND_EXACT[event_type]
    t = event_type.lower()
    if any(k in t for k in ("message", "llm", "think", "plan", "cognition")):
        return "cognition"
    if any(k in t for k in ("stdout", "stderr", "cmd", "shell", "bash", "exec")):
        return "subprocess"
    if any(k in t for k in ("action", "tool", "dispatch")):
        return "dispatch"
    if any(k in t for k in ("delivery", "merge", "commit", "pull_request", " pr ", "pr_")):
        return "delivery"
    return "notify"


def _project_event_row(ev, *, kind: str, payload: object) -> dict:
    """Frame shape the console's Goal Detail feed reads. Kept flat so the
    React side can render without another normalization pass."""
    return {
        "id": ev.id,
        "kind": kind,
        "type": ev.type,
        "source": ev.source,
        "ts": ev.ts,
        "payload": payload,
    }
