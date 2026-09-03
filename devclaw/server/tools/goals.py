"""The durable goal layer's steer/observe surface.

Create through the full steer/resume/evaluate/cancel verbs. (The deprecated
``start_program`` alias was retired by spec 022 US3; the prose authoring
porch — ``scope_grill``, ``dry_evaluate``, ``verify_goal``, the saga-slot
arguments — was deleted by the 2026-08-29 prune: the ticket is the contract,
so authoring happens in the issue, not in tool arguments.)
"""

from __future__ import annotations

import json
from typing import Annotated, Optional

from fastmcp.exceptions import ToolError
from pydantic import Field

from .._state import goals, mcp, store
from ._common import _resolve_project_or_reject


# ===== goal layer (durable, steerable, evaluated goals) ======================
# The folded-in goalclaw. A `program` is a bounded, one-shot DAG; a `goal` is an
# open-ended standing intent that DevClaw drives across many heartbeats —
# planning the next action, dispatching it into the queue, and EVALUATING whether
# the work is actually moving toward the objective (not just shipping PRs). These
# tools are the steer/observe surface: ask what's going on, correct it.


@mcp.tool
async def create_goal(
    goal_id: str,
    objective: str,
    project_id: str,
    issues: Optional[list[int]] = None,
    cadence: str = "1d",
    verify_cmd: Optional[str] = None,
    open_pr: bool = True,
    mode: str = "long_lived",
    strictness: Optional[str] = None,
) -> str:
    """Register a goal that DevClaw drives: on each heartbeat it plans, dispatches
    to the engine, records what shipped, and only closes when a grounded review
    confirms the contract is met. Steer it any time with steer_goal; inspect it
    with get_goal.

    THE ISSUE IS THE CONTRACT (spec 024): ``issues`` — ordered issue NUMBERS on
    the project's own repository — is REQUIRED for every mode except ``qa``.
    Each dispatch fetches their LIVE state into the worker brief (never a
    creation-time copy), a closed issue drops out of the remaining scope, and
    when every referenced issue is closed the goal proposes done. The ask, the
    completion criteria, and the saga sections (out-of-scope / invariants /
    established) are authored IN the issue via the template; there is no prose
    lane. For a greenfield repo: create_repo, file the issue, then this.

    mode selects the execution dial (ADR 0003): 'long_lived' (default) is the
    per-tick loop — plan the single next action each heartbeat, steerable
    mid-flight. 'one_shot' rides the SAME advance loop and proposes done as
    soon as an advance session lands — same gates; the worker owns the plan
    (speckit, spec 008). 'qa' (spec 015) is the per-repo live-validation
    owner: it never plans feature work and never terminates (standing contract
    supplied automatically); validation runs fire on completed deploys, and a
    periodic cadence exists but SHIPS OFF — arm it by passing an explicit
    cadence (e.g. '24h'). ``qa`` is the one mode that takes no issues.

    goal_id: a short stable slug (the on-disk folder name). objective: the
    display identity (list surfaces need a name — typically the lead issue's
    title; never gate input). project_id: the registered project (see
    list_projects). verify_cmd: the gate (e.g. 'dotnet test')."""
    if not goal_id:
        raise ToolError("create_goal requires goal_id")
    if mode not in ("long_lived", "one_shot", "qa"):
        raise ToolError("create_goal mode must be 'long_lived', 'one_shot' or 'qa'")
    if mode != "qa" and not issues:
        raise ToolError(
            "create_goal requires issues — the ticket is the contract (spec "
            "024): author the ask, acceptance criteria, and saga sections in "
            "a GitHub issue on the project's repo and pass its number. "
            "(dispatch_task auto-files an intake issue for a prose ask.)"
        )
    # Omitted strictness = "not explicitly chosen": the repo's devclaw.json
    # strictnessDefault (if any) applies live; passing a value pins the goal.
    if strictness is not None and strictness not in ("trust", "strict"):
        raise ToolError("create_goal strictness must be 'trust' or 'strict'")
    # Resolve the project reference key → workspace + repo (spec 003 / #520).
    # Unknown project rejects here; the workspace is NOT preflighted for
    # existence — a goal's workspace is cloned/reset by prepare_workspace on the
    # first tick (which blocks-and-heals a non-git/no-repo workspace itself).
    resolved = _resolve_project_or_reject(project_id, "create_goal")
    # objective is checked inside admission and surfaced as a structured
    # condition — don't duplicate it here.
    from ...goal.admission import GoalAdmissionRejected

    try:
        return json.dumps(
            await goals.create_goal_async(
                goal_id, objective=objective, workspace_dir=resolved.workspace_dir,
                cadence=cadence,
                repo_url=resolved.repo_url, verify_cmd=verify_cmd, open_pr=open_pr,
                mode=mode, strictness=strictness,
                project_id=resolved.project_id,
                issues=issues,
            ),
            indent=2,
        )
    except ValueError as exc:
        # Issue-reference doorway refusals (spec 019) surface verbatim — each
        # message names the rule, the offending input, and the fixing verb.
        raise ToolError(str(exc))
    except FileExistsError:
        raise ToolError(f"goal {goal_id!r} already exists")
    except GoalAdmissionRejected as exc:
        # Structured rejection: surface the full condition list so the caller
        # can render fixable items to the customer and route on the codes.
        raise ToolError(json.dumps(exc.result.to_dict(), indent=2))


@mcp.tool
async def get_goal(goal_id: str) -> str:
    """Inspect a durable goal: its objective + done_when, current phase, what's
    in flight, the last direction-evaluation verdict, and the recent log. This is
    the 'what's going on / what direction' surface."""
    try:
        return json.dumps(goals.get_goal(goal_id), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


@mcp.tool
async def tail_goal(
    goal_id: str,
    log_lines: int = 40,
    deliveries_chars: int = 6000,
    event_limit: int = 30,
) -> str:
    """Watch a goal run — the deep, read-only observability surface. Beyond
    get_goal's phase/direction it returns the grounded deliveries tail (what each
    action actually shipped: agent summary + gate verdict + PR), the discovery
    brief + any pre-aligned spec, and the tail of the LIVE event stream from
    whatever task is in flight — so you can see the engineer acting in near real
    time without SSHing to the box. Everything is bounded; call repeatedly to
    follow progress."""
    if not goal_id:
        raise ToolError("tail_goal requires goal_id")
    try:
        return json.dumps(
            goals.tail_goal(
                goal_id,
                log_lines=log_lines,
                deliveries_chars=deliveries_chars,
                event_limit=event_limit,
            ),
            indent=2,
        )
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


@mcp.tool
async def list_goals() -> str:
    """List all durable goals with their phase + latest direction verdict."""
    return json.dumps(goals.list_goals(), indent=2)


@mcp.tool
async def steer_goal(goal_id: str, message: str) -> str:
    """Change a durable goal's DIRECTION. The message is recorded as steering and
    the next advance honors it (the tick is poked immediately). Use for a genuine
    change of course — 'use Postgres, not SQLite', 'skip the admin UI'. It is NOT
    the answer to a problem: a goal with an open problem refuses steering and
    hands back the problem with the two resolution verbs, correct_implementation
    and decide (spec 031)."""
    if not goal_id or not message:
        raise ToolError("steer_goal requires goal_id and message")
    try:
        return json.dumps(goals.steer_goal(goal_id, message), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")
    except ValueError as exc:
        raise ToolError(str(exc))


@mcp.tool
async def correct_implementation(goal_id: str, problem_id: str, correction: str) -> str:
    """Resolve a goal's open problem: the requirement was right, the WORK was wrong.
    Records the correction as a decision against the problem's done_when clause,
    unblocks the goal with its full budget, and the next session builds on it as
    settled fact. One of exactly two resolution verbs (spec 031); the other is
    `decide`. Read the problem with get_goal first."""
    if not goal_id or not problem_id or not (correction or "").strip():
        raise ToolError("correct_implementation requires goal_id, problem_id and a correction")
    try:
        return json.dumps(
            goals.resolve_problem(goal_id, problem_id, verb="correct_implementation", text=correction),
            indent=2,
        )
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")
    except ValueError as exc:
        raise ToolError(str(exc))


@mcp.tool
async def decide(
    goal_id: str, problem_id: str,
    option: Optional[str] = None, text: Optional[str] = None,
) -> str:
    """Resolve a goal's open problem by taking an action: pick one of the problem's
    options by key, or write a free-form decision text (exactly one of the two).
    The decision is recorded into the contract as a devclaw-controlled fact —
    never as a steering line — and the done-gate treats that clause as settled.
    One of exactly two resolution verbs (spec 031); the other is
    `correct_implementation`."""
    if not goal_id or not problem_id:
        raise ToolError("decide requires goal_id and problem_id")
    try:
        return json.dumps(
            goals.resolve_problem(goal_id, problem_id, verb="decide", option=option, text=text),
            indent=2,
        )
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")
    except ValueError as exc:
        raise ToolError(str(exc))


@mcp.tool
async def resume_goal(goal_id: str) -> str:
    """Resume a BLOCKED goal whose blocker has been cleared out-of-band — the
    recovery verb. Fires the goal's existing UNBLOCK transition and forces a
    re-plan on the next heartbeat tick (poked immediately), re-attempting the
    SAME contract: no steering is recorded and the objective/done_when/backlog
    are untouched. This does NOT change direction (use steer_goal for that)
    and is NOT a field-patch/update tool — nothing about the goal is edited.

    Idempotent: on a goal that is not blocked it no-ops with a message."""
    if not goal_id:
        raise ToolError("resume_goal requires goal_id")
    try:
        return json.dumps(goals.resume_goal(goal_id), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


@mcp.tool
async def set_goal_strictness(goal_id: str, strictness: str) -> str:
    """Set a goal's gate strictness dial (ADR 0007). ``strict`` = a dial-able gate
    that fails BLOCKS the goal (fail closed). ``trust`` (the default) = a dial-able
    gate that fails is recorded loud + surfaced in the PR body, but the change
    SHIPS — the human merge is the backstop. Only the two review-shaped gates
    (browser-E2E, adversarial review) obey the dial; the evidence-integrity gates
    (test-integrity, delivery-trust, done-gate) stay hard in BOTH modes.

    A narrow single-field toggle, NOT a contract patch — objective/done_when/
    backlog are untouched. Applies to future dispatches; in-flight work keeps the
    value it was dispatched with. Reserve ``strict`` for goals whose output you
    actually depend on."""
    if not goal_id or not strictness:
        raise ToolError("set_goal_strictness requires goal_id and strictness")
    try:
        return json.dumps(goals.set_strictness(goal_id, strictness), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")
    except ValueError as e:
        raise ToolError(str(e))


@mcp.tool
async def set_goal_verify_cmd(goal_id: str, verify_cmd: str) -> str:
    """Override the goal's verification command (issue #711). The verify_cmd is
    the shell command the sandbox gate runs after the worker finishes; its exit
    code decides done-vs-failed. Pass an empty string to CLEAR the goal-level
    value, letting the project manifest's ``verifyCmd`` (if any) take effect on
    the next dispatch.

    A narrow single-field override, NOT a contract patch — objective/done_when/
    backlog are untouched. Applies to future dispatches; in-flight work keeps the
    value it was dispatched with."""
    if not goal_id:
        raise ToolError("set_goal_verify_cmd requires goal_id")
    try:
        return json.dumps(goals.set_verify_cmd(goal_id, verify_cmd or None), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


@mcp.tool
async def evaluate_goal(goal_id: str) -> str:
    """Force an on-demand direction evaluation NOW, grounded in the goal's
    artifacts. Reads recent deliveries + log + spec, runs the evaluator, and
    returns the fresh verdict (``on_track`` / ``off_track`` / ``achieved`` /
    ``stalled`` / ``needs_human``) with the evaluator's rationale. Any
    corrections are appended to the goal's ``inbox.md`` as steering (the
    next-action planner picks them up) AND the heartbeat is poked.

    Distinct from the per-tick evaluator (which runs on cadence inside the
    heartbeat) — this is the surface the owner OR the operations agent calls
    to wake a stuck goal, get a fresh direction read, or ground a
    "should I close this?" decision in evidence on demand.

    Returns::

        {"goal_id": "...", "verdict": "...", "rationale": "...",
         "corrections": [...], "question": "..."}
    """
    if not goal_id:
        raise ToolError("evaluate_goal requires goal_id")
    try:
        return json.dumps(await goals.evaluate_goal(goal_id), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


@mcp.tool
async def get_trace(
    goal_id: str,
    since_id: int = 0,
    limit: Annotated[int, Field(ge=1, le=2000)] = 200,
    kind: Optional[str] = None,
) -> str:
    """Read durable trace events for a goal — every cognition call, dispatch,
    delivery, subprocess, and notification a heartbeat tick has emitted, in
    emission order. Grouped by ``trace_id`` (one per goal-tick).

    Use this to inspect what actually happened during a cascade: which prompts
    fired with what role, how long each cognition call took, real input/output
    tokens + cost from the CLI's usage envelope (``tokens_in``/``tokens_out``/
    ``cost_usd``; a call with no usage envelope — stub cognition, an errored or
    timed-out call, the raw-stdout degrade path — carries only the ``_est`` len/4
    estimates, labeled as estimates), the FULL response text, and the chain of
    dispatches that followed. Goal-scoped cognition rows also carry
    ``transcript_file`` — the full prompt+response transcript under the goal
    dir's ``transcripts/``. Pair with ``get_goal`` for the high-level state +
    this for the causal detail.

    Returns ``{"events": [...], "totals": {...}}``. Totals prefer real tokens
    per row and report ``cognition_rows_estimated`` for how many rows fell back
    to estimates. Pass ``since_id`` (the monotonic id of the last event you've
    seen) to incrementally tail; pass ``kind`` to filter (e.g. ``cognition``
    for prompts only).
    """
    if not goal_id:
        raise ToolError("get_trace requires goal_id")
    events = store.read_traces(
        goal_id=goal_id, since_id=since_id, limit=limit, kind=kind,
    )
    totals = store.trace_totals(goal_id=goal_id)
    return json.dumps({"events": events, "totals": totals}, indent=2, default=str)


@mcp.tool
async def cancel_goal(goal_id: str) -> str:
    """Permanently stop a durable goal. Sets its phase to 'cancelled' (a terminal
    state — DevClaw will skip it on every future heartbeat) and tears down any
    in-flight task or program associated with it. Returns a graceful no-op response
    if the goal is already in a terminal phase (done or cancelled) — safe to call
    more than once. Use when you no longer want DevClaw to drive a goal."""
    if not goal_id:
        raise ToolError("cancel_goal requires goal_id")
    try:
        return json.dumps(goals.cancel_goal(goal_id), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")
