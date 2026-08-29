"""Read-only observability — tasks, programs, events, scorecard, problems.

Pure SELECTs over the state store (plus the trends file); nothing here
dispatches, mutates, or wakes the goal loop.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Optional

from fastmcp.exceptions import ToolError
from pydantic import Field

from .._state import goals, mcp, registry, store


@mcp.tool
async def review_trends(scope: str = "harness_self", limit_chars: int = 5000) -> str:
    """Read recent trend observations produced by devclaw's cross-session trend
    detector. Returns the tail of the matching ``trends.md`` as JSON
    ``{scope, path, trends}``.

    Pass ``scope='harness_self'`` (default) for devclaw's own self-observability
    file (in Denys's vault by default). Pass a workspace path for that project's
    per-repo trends (``<workspace>/.devclaw/trends.md``). The detector observes
    and surfaces patterns (recurring fixes, AGENTS.md drift, steering frequency,
    etc.); humans decide which to promote into AGENTS.md."""
    return json.dumps(goals.read_trends(scope=scope, limit_chars=limit_chars), indent=2)


@mcp.tool
async def get_status(task_id: str) -> str:
    """Return the current status + (when terminated) the result or error of a
    task. Status values: pending | running | done | failed | cancelled."""
    task = store.get_task(task_id)
    if not task:
        raise ToolError(f"unknown task_id: {task_id}")
    return json.dumps(task.to_dict(), indent=2)


@mcp.tool
async def get_events(
    task_id: str,
    since_id: Optional[int] = None,
    limit: Annotated[int, Field(ge=1, le=5000)] = 500,
) -> str:
    """Return events emitted by the worker runner for one task, in emission
    order. Each event has an id (monotonic cursor), type, source, payload_json
    (the raw SDK Event), and ts. Pass since_id to resume."""
    events = store.list_events(task_id=task_id, since_id=since_id, limit=limit)
    return json.dumps([e.to_dict() for e in events], indent=2)


@mcp.tool
async def list_tasks(
    status: Optional[Literal["pending", "running", "done", "failed", "cancelled"]] = None,
    kind: Optional[Literal["implement_feature", "fix_bug", "review_repository", "onboard"]] = None,
    limit: Annotated[int, Field(ge=1, le=1000)] = 20,
) -> str:
    """List recent tasks, most-recent first. Optionally filter by status or kind."""
    tasks = store.list_tasks(status=status, kind=kind, limit=limit)
    return json.dumps([t.to_dict() for t in tasks], indent=2)


@mcp.tool
async def get_scorecard_metrics(
    window_hours: Annotated[int, Field(ge=1, le=24 * 30)] = 168,
) -> str:
    """L8 rolling scorecard: ground-truth distinct-PR merge state (spec 018 US2), evaluator verdict distribution, steer
    rate, per-goal convergence (first-pass rate + rounds-to-close, spec 018),
    workspace-break count — computed over the last
    ``window_hours`` (default 168 = one week). Reads state_store directly, so
    it's cheap and can be called from Telegram or a dashboard without waking
    the goal loop. See ``plan.md`` §Measurement direction for how the numbers
    relate to the C1-C8 production-ready scorecard."""
    from ...telemetry import compute_scorecard
    return json.dumps(compute_scorecard(store, window_hours=int(window_hours), registry=registry), indent=2)


@mcp.tool
async def list_problems(
    category: Optional[
        Literal[
            "block",
            "task_fail",
            "gate",
            "delivery",
            "limit",
            "cognition",
            "subprocess",
            "other",
        ]
    ] = None,
    limit: Annotated[int, Field(ge=1, le=1000)] = 100,
) -> str:
    """The deduplicated problems catalog — a **gatherer-signal readout**, NOT a
    backlog (issue-driven-pipelines, N1/#371). The single canonical store of
    *intent* ("what to do about a failure") is **GitHub Issues**; this catalog is
    the mechanical feeder upstream of it. Each recurring root cause is filed there
    by the self-improving loop, and every row here points back at that Issue via
    ``issue_number``/``issue_state`` and a derived ``lifecycle``
    (``identified`` → ``filed`` → ``resolved``). Read this to see *what devclaw is
    hitting and where it is in the pipeline* — act on it in the Issue, not here.

    Each row is ONE root cause (fingerprinted on ``category | kind |
    normalize(message)``), so N recurrences collapse to a single row with
    ``count`` incremented rather than N rows, most-frequent first. ``recovered_count``
    is how often devclaw carried on past it (a usage-limit pause that auto-resumes,
    a mechanical block that self-heals); ``terminal_count`` is how often it was a
    dead stop (a failed task, a human-gated block). ``sample_message`` keeps one
    un-normalized example for context.

    Pass ``category`` to filter to one class of failure (block / task_fail /
    gate / delivery / limit / cognition / subprocess / other). Pure SELECT over
    state_store — cheap, read-only, never wakes the goal loop."""
    from ...state_store.problems import problem_lifecycle

    problems = store.list_problems(
        category=category, limit=int(limit), include_issue=True
    )
    for p in problems:
        p["lifecycle"] = problem_lifecycle(p)
    return json.dumps(
        {"count": len(problems), "problems": problems}, indent=2
    )
