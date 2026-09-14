"""What a goal needs from the owner — a pure projection (spec 047 US1).

Derived on request from rows the host already holds: the last session's
exit and payload, the decisions, the last-seen world fingerprint, and the
dispatch facts. Nothing here is stored (constitution IV) and nothing here
is the host's opinion: options and the recommendation are the session's
words; host-authored stops (a red CI, a done-gate refusal, an env gap)
carry their fact and no options.
"""

from __future__ import annotations

import json
from typing import Optional

from ...goal import donegate as _donegate
from ...state_store.rows import (
    EXIT_BLOCKED, EXIT_DONE, EXIT_REFUSED, EXIT_REVIEW, Decision, Task,
)


def dispatch_facts(*, hold: bool, window_closed: bool, paused: bool, running: int) -> dict:
    return {"hold": hold, "window": window_closed, "pause": paused, "running": running}


def _waiting_on(control: dict) -> str:
    if control.get("hold"):
        return "hold"
    if control.get("pause"):
        return "pause"
    if control.get("window"):
        return "window"
    if int(control.get("running") or 0) > 0:
        return "lane busy"
    return "tick"


def _result(task: Task) -> dict:
    try:
        r = json.loads(task.result_json or "{}")
    except ValueError:
        return {}
    return r if isinstance(r, dict) else {}


def _pr_url(task: Optional[Task], goal: dict) -> str:
    if task is not None and task.pr_url:
        return task.pr_url
    last = goal.get("lastSession") or {}
    return str(last.get("prUrl") or "")


def _issue_url(goal: dict) -> str:
    issues = goal.get("issues") or []
    repo = str(goal.get("repoUrl") or "").removesuffix(".git")
    return f"{repo}/issues/{issues[0]}" if repo and issues else ""


def attention(goal: dict, last: Optional[Task], decisions: list[Decision],
              last_seen: Optional[dict], control: dict) -> Optional[dict]:
    """``None`` when the goal needs nothing; else the row of data-model §2."""
    if goal.get("outcome") or last is None or last.status in ("pending", "running"):
        return None
    row: Optional[dict] = None
    since = last.completed_at or last.created_at
    if last.exit == EXIT_BLOCKED:
        b = _donegate.block_fields(last.result_json, last.exit_detail)
        if b["kind"] == "env":
            row = {"kind": "env", "question": f"waiting for {b['item'] or 'a credential'}",
                   "options": [], "recommended": -1, "default": "", "link": _issue_url(goal)}
        else:
            row = {"kind": "session", "question": b["question"], "options": b["options"],
                   "recommended": b["recommended"], "default": b["default"], "link": _issue_url(goal)}
    elif last.exit == EXIT_REVIEW:
        v = _donegate.parse_verdict(str(_result(last).get("agent_output") or ""))
        if last.status != "done" or v.unreadable:
            row = {"kind": "done-gate refused", "question": "the review produced no readable verdict"
                   + (f": {v.raw_error}" if v.raw_error else "")}
        elif not v.achieved:
            missing = [c["clause"] for c in v.clauses if not (c["satisfied"] and c["evidence"])]
            row = {"kind": "done-gate refused",
                   "question": (v.summary or "not achieved") + (("\n- " + "\n- ".join(missing)) if missing else "")}
        if row is not None:
            row.update({"options": [], "recommended": -1, "default": "", "link": _pr_url(last, goal) or _issue_url(goal)})
    elif last.exit == EXIT_REFUSED:
        row = {"kind": "delivery refused", "question": last.exit_detail or "", "options": [],
               "recommended": -1, "default": "", "link": _issue_url(goal)}
    elif last.exit == EXIT_DONE and (last_seen or {}).get("pr") == "none":
        row = {"kind": "DONE without a PR", "question": "the session proposed DONE but nothing was delivered",
               "options": [], "recommended": -1, "default": "", "link": _issue_url(goal)}
    if row is None and last_seen and last_seen.get("pr") == "open" and last_seen.get("ci") == "red":
        row = {"kind": "red CI", "question": f"CI is red on {str(last_seen.get('head') or '')[:12]}",
               "options": [], "recommended": -1, "default": "", "link": _pr_url(last, goal) or _issue_url(goal)}
        since = int(goal.get("lastSeenAt") or since)
    if row is None:
        return None
    row["since"] = since
    newest = max(decisions, key=lambda d: d.made_at, default=None)
    if newest is not None and newest.made_at > since:
        row["answered"] = {"text": newest.text, "madeAt": newest.made_at,
                           "commentUrl": newest.comment_url, "waitingOn": _waiting_on(control)}
    else:
        row["answered"] = None
    return row
