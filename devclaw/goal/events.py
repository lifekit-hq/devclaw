"""Event-driven triggers (spec 023) — tracker events WAKE the machinery.

The design rule, stated once: an event never grows a second transition path.
A qualifying tracker event stamps a trigger-named goal-log line and calls
``poke()`` — the same in-process wake the MCP verbs use — so the work runs in
the very next tick through exactly the code the heartbeat would have run
minutes later. FR-002 ("the heartbeat is a complete fallback") holds by
construction: both triggers execute the same path. State is never written
here (constitution IV); the append-only goal log is the one record.

The single direct action is grading (US2): an ``issues opened/edited`` event
on a registered repo runs the SAME readiness grade the manual
``regrade_intake`` verb runs — identical cognition spend (FR-007), identical
fail-closed rule, verdict landing on the issue as label + mirror comment
(the existing intake machinery). Everything else is zero-cognition.

Everything is best-effort toward the CALLER (the webhook route must answer
GitHub fast and never 5xx over a payload) and loud toward the logs.
"""

from __future__ import annotations

import sys
from typing import Any, Awaitable, Callable

#: events whose only meaning is "repo state advanced — look now"
_WAKE_EVENTS = {
    ("pull_request", "closed"),
    ("issues", "closed"),
    ("check_run", "completed"),
    ("check_suite", "completed"),
}

#: events that trigger grading (US2)
_GRADE_ACTIONS = {"opened", "edited"}


def _repo_full_name(payload: dict) -> str:
    return str(((payload.get("repository") or {}).get("full_name")) or "").strip()


def _project_for_repo(registry, full_name: str):
    """The registered project whose repo_url names ``full_name``, or None.
    Mechanical string match — the registry is small (single digits)."""
    if not full_name:
        return None
    want = full_name.lower()
    try:
        for project in registry.list():
            url = (project.repo_url or "").strip().rstrip("/").removesuffix(".git").lower()
            if url.endswith("/" + want) or url == want:
                return project
    except Exception:  # noqa: BLE001 — a registry hiccup drops the event, loudly below
        return None
    return None


def _goals_on_repo(goal_store, repo_url: str) -> list[str]:
    """Non-terminal goals working ``repo_url`` — the goals whose log names the
    trigger. Best-effort: a bad goal.yaml is skipped (holder_map's rule)."""
    want = (repo_url or "").strip().rstrip("/").removesuffix(".git").lower()
    out: list[str] = []
    for goal_id in goal_store.list_goal_ids():
        try:
            status = goal_store.load_status(goal_id)
            if status.phase in ("done", "cancelled"):
                continue
            url = (goal_store.load_goal(goal_id).repo_url or "")
            if url.strip().rstrip("/").removesuffix(".git").lower() == want:
                out.append(goal_id)
        except Exception:  # noqa: BLE001
            continue
    return out


async def route_event(
    event: str,
    payload: dict,
    *,
    registry,
    goal_store,
    poke: Callable[[], Any],
    regrade: "Callable[..., Awaitable[Any]] | None" = None,
) -> dict:
    """Handle one authenticated webhook delivery. Returns a small outcome dict
    (``{"outcome": ..., "detail": ...}``) the route echoes; never raises.

    Idempotent under duplicate delivery (FR-003): the wake is a no-op when
    nothing is waiting, and every actual transition happens behind the tick's
    CAS. A replayed grading event re-yields the same verdict, exactly like
    calling the manual verb twice.
    """
    action = str(payload.get("action") or "")
    full_name = _repo_full_name(payload)
    project = _project_for_repo(registry, full_name)
    if project is None:
        # unknown/unregistered repo: dropped with a recorded reason, never an
        # error loop (spec edge case)
        sys.stderr.write(
            f"webhook: dropped {event}/{action} for unregistered repo "
            f"{full_name or '(unknown)'}\n"
        )
        return {"outcome": "dropped", "detail": f"repo {full_name!r} not registered"}

    # US2: grading on issue open/edit — the one direct (cognition) action.
    if event == "issues" and action in _GRADE_ACTIONS:
        issue = (payload.get("issue") or {})
        issue_url = str(issue.get("html_url") or "")
        if not issue_url:
            return {"outcome": "dropped", "detail": "issues event without an issue url"}
        if regrade is None:
            return {"outcome": "dropped", "detail": "grading not bound"}
        try:
            await regrade(registry, project_id=project.id, issue=issue_url)
            return {"outcome": "graded", "detail": issue_url}
        except Exception as exc:  # noqa: BLE001 — loud + recorded, never a 5xx to GitHub
            sys.stderr.write(f"webhook: grading failed for {issue_url}: {exc}\n")
            return {"outcome": "grade_failed", "detail": str(exc)[:200]}

    # PR merged: pull_request closed is only a wake when it actually merged.
    if event == "pull_request" and action == "closed":
        if not (payload.get("pull_request") or {}).get("merged"):
            return {"outcome": "ignored", "detail": "PR closed without merge"}

    if (event, action) in _WAKE_EVENTS:
        number = ((payload.get("pull_request") or payload.get("issue")
                   or payload.get("check_run") or {}).get("number") or "")
        woke = _goals_on_repo(goal_store, project.repo_url or "")
        for goal_id in woke:
            try:
                # FR-008: the trigger is named in the goal's own log so the
                # heartbeat demotion is observable per goal.
                goal_store.append_log(
                    goal_id,
                    f"event: {event}/{action} {full_name}"
                    + (f"#{number}" if number else "")
                    + " — advancing now (webhook)",
                )
            except Exception:  # noqa: BLE001 — the wake still fires
                pass
        poke()
        return {"outcome": "woke", "detail": f"{len(woke)} goal(s) on {full_name}"}

    return {"outcome": "ignored", "detail": f"{event}/{action} carries no trigger"}
