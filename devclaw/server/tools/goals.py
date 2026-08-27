"""The durable goal layer's steer/observe surface + its front porch.

Everything from the scope grill and the dry-cognition previews through
create/verify and the full steer/resume/evaluate/cancel verbs, plus the
deprecated ``start_program`` alias (ADR 0003).
"""

from __future__ import annotations

import json
from typing import Annotated, Optional

from fastmcp.exceptions import ToolError
from pydantic import Field

from ... import elicitation as _elicitation
from .._state import goals, mcp, store
from ._common import _resolve_project_or_reject


def _one_shot_goal_id(goal: str) -> str:
    """A stable-ish readable slug for a start_program-sugar goal: the first
    few words of the brief + a uuid suffix (collision-proof without a retry
    loop)."""
    import re as _re
    import uuid as _uuid

    words = _re.findall(r"[a-z0-9]+", goal.lower())[:5]
    slug = "-".join(words)[:40].strip("-") or "program"
    return f"{slug}-{_uuid.uuid4().hex[:6]}"


@mcp.tool
async def start_program(
    project_id: str, goal: str, notify_url: Optional[str] = None
) -> str:
    """DEPRECATED sugar for create_goal(mode='one_shot') — ADR 0003 stage 2b:
    a program and a goal are the same thing, differing only in the
    re-evaluation dial. This tool now files a ONE-SHOT GOAL: the worker plans
    and executes via speckit in-sandbox (spec 008), with PR-per-slice
    delivery and a grounded done-gate close — plus steering, resume, and
    console visibility that raw programs never had.

    Returns {goal_id, mode, ...}; poll get_goal(goal_id) / tail_goal. The child
    program appears in list_programs once the goal dispatches it. Prefer
    calling create_goal(mode='one_shot') directly — this alias exists for
    existing waiter flows and will be retired."""
    if not goal:
        raise ToolError("start_program requires project_id and goal")
    from ...goal.admission import GoalAdmissionRejected

    resolved = _resolve_project_or_reject(project_id, "start_program")
    goal_id = _one_shot_goal_id(goal)
    try:
        # The brief rides as the SPEC (the scope contract the done-gate
        # evaluator judges against) — the same acceptance parity the old
        # direct-queue path had: there is no separate done_when.
        result = goals.create_goal(
            goal_id, objective=goal, workspace_dir=resolved.workspace_dir,
            repo_url=resolved.repo_url, spec=goal, mode="one_shot",
            project_id=resolved.project_id,
            # The saga slots are declared EMPTY on this deprecated alias, not
            # demanded from it. It is the FR-012b class: an operator is present
            # at the call and can correct a bad prompt immediately, so a
            # required-slot tax buys nothing on the one path that already has a
            # reviewer in the loop. Schemas earn their cost on unattended work —
            # which is why the self-fix pickup DOES fill them for real.
            out_of_scope=[], invariants=[], established=[],
        )
    except GoalAdmissionRejected as exc:
        raise ToolError(json.dumps(exc.result.to_dict(), indent=2))
    out = {
        "goal_id": goal_id,
        "mode": "one_shot",
        "lifecycle": result.get("lifecycle", "executing"),
        "phase": result.get("phase", "idle"),
        "note": (
            "start_program now files a one-shot GOAL (ADR 0003). Poll "
            "get_goal/tail_goal with goal_id; the child program appears in "
            "list_programs once dispatched. Deliveries arrive as reviewable "
            "PRs and the close is gated on a grounded done_when review."
        ),
    }
    if notify_url:
        # Goals notify through the configured goal-layer notifier, not a
        # per-call URL — say so instead of silently dropping the contract.
        out["notify_url_ignored"] = (
            "goal-backed programs notify via the goal layer's configured "
            "notifier; per-call notify_url is not supported"
        )
    return json.dumps(out, indent=2)


# ===== scope grill (waiter-side conversation, chef-side craft) ===============
# The OpenClaw devclaw waiter holds the Telegram conversation; this tool gives it
# the chef's craft — *which* questions matter for a software scope and what 'good'
# looks like. The waiter calls scope_grill each turn with the running transcript;
# the chef returns the next question (with a recommended answer) or, when enough
# is shared, the finalized spec. Stateless: the waiter owns the transcript and,
# once 'done' lands, calls create_goal(spec=...) to file the order.


@mcp.tool
async def scope_grill(
    idea: str,
    transcript: Optional[list[dict]] = None,
) -> str:
    """Take one turn of a scope-alignment grill with the OpenClaw waiter. Given a
    rough project ``idea`` and the ``transcript`` so far (a list of turns each
    with question/recommended/answer), return either the next question to ask
    the customer or the finalized spec when enough is shared.

    The waiter is expected to keep the transcript across turns (it lives in the
    Telegram chat), pass it back unchanged on each call, and append the user's
    reply to the last turn before the next call. This is a stateless cognition
    call — the chef stores nothing here. When the response is ``{"action":
    "done", "spec": ...}``, the waiter calls ``create_goal(..., spec=<spec>)``
    to file the order.

    Response shape:
      {"action": "ask", "question": "<next q>", "recommended": "<your default>"}
      {"action": "done", "spec": "<full spec.md markdown>"}
    """
    if not idea or not idea.strip():
        raise ToolError("scope_grill requires a non-empty idea")
    transcript = transcript or []
    try:
        step = await _elicitation.next_step(idea, transcript)
    except Exception as err:  # noqa: BLE001 — surface as a tool error, not a crash
        raise ToolError(f"scope_grill failed: {err}")
    return json.dumps(step, indent=2)


# ===== dry cognition (test the rail without filing a goal) ===================
# The customer wants to *think about* a project — grill it, see the world-research
# brief, see the decomposition, see how the evaluator would grade the finished
# thing — WITHOUT committing to workspace_dir / repo_url / a persisted goal. These
# tools expose the exact cognition modules the chef runs during a real goal's
# lifecycle, but each one is one-shot and pure: it constructs a throwaway in-memory
# ``Goal``, runs the module's ``default_caller`` (same model tier as production),
# and returns the artifact. Zero writes to /var/lib/devclaw/goals/. Zero admission.


def _dry_goal(
    *,
    objective: str,
    done_when: str = "",
    backlog: Optional[list[str]] = None,
    stub_acceptable: Optional[list[str]] = None,
):
    """Build a throwaway :class:`Goal` for the dry-cognition tools. Persistence
    fields (``workspace_dir``, ``repo_url``, ``verify_cmd``) get harmless
    placeholders — the dry tools NEVER touch disk or clone, and the cognition
    modules only read the fields the prompts actually reference."""
    from ...goal.models import Goal

    return Goal(
        id="dry-run",
        objective=objective,
        cadence="1d",
        engine="devclaw",
        workspace_dir="/dev/null",
        repo_url=None,
        verify_cmd=None,
        open_pr=False,
        done_when=done_when,
        backlog=backlog or [],
        stub_acceptable=stub_acceptable or [],
    )


@mcp.tool
async def dry_evaluate(
    objective: str,
    done_when: str,
    review_report: str,
    spec: str = "",
    backlog: Optional[list[str]] = None,
    stub_acceptable: Optional[list[str]] = None,
    deliveries: str = "",
    recent_log: str = "",
    at_done_gate: bool = True,
) -> str:
    """PURE COGNITION — no goal filed, no workspace, no side effects.

    Runs the direction evaluator (the cognition that grades a goal at the
    done-gate) against hypothetical inputs and returns the JSON verdict:
    ``{verdict, rationale, corrections, question, clauses}``. Use this to
    sanity-check the harness's judgement on "here's what shipped vs. what was
    asked" — including whether it would refuse stub-disguise on a specific
    review — without touching a real goal.

    Defaults to ``at_done_gate=True`` (strict per-clause grading, the mode the
    real done-gate runs). Pass a ``review_report`` shaped like a
    ``review_repository`` task's output (``## Per-clause evidence`` +
    ``## Structural health`` sections) to exercise the full done-gate path.
    """
    if not objective or not objective.strip():
        raise ToolError("dry_evaluate requires a non-empty objective")
    if not done_when or not done_when.strip():
        raise ToolError("dry_evaluate requires done_when (the completion contract)")
    from dataclasses import asdict

    from ...goal import evaluator as _eval
    from ...goal.models import GoalStatus

    goal = _dry_goal(
        objective=objective, done_when=done_when, backlog=backlog,
        stub_acceptable=stub_acceptable,
    )
    status = GoalStatus(phase="done" if at_done_gate else "in_flight")
    try:
        result = await _eval.evaluate(
            goal, status, recent_log, deliveries,
            claude_caller=_eval.default_caller(),
            review_report=review_report or None,
            at_done_gate=at_done_gate,
            spec=spec,
        )
    except Exception as err:  # noqa: BLE001
        raise ToolError(f"dry_evaluate failed: {err}")
    return json.dumps(asdict(result), indent=2)


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
    done_when: str = "",
    backlog: Optional[list[str]] = None,
    cadence: str = "1d",
    verify_cmd: Optional[str] = None,
    open_pr: bool = True,
    spec: str = "",
    mode: str = "long_lived",
    strictness: Optional[str] = None,
    out_of_scope: Optional[list[str]] = None,
    invariants: Optional[list[str]] = None,
    established: Optional[list[str]] = None,
    issues: Optional[list[int]] = None,
) -> str:
    """Register a goal that DevClaw drives: on each heartbeat it plans, dispatches
    to the engine, records what shipped, and only closes when a grounded review
    confirms done_when is met. Steer it any time with steer_goal; inspect it with
    get_goal.

    issues (spec 019, the referenced lane): ordered issue NUMBERS on the
    project's own repository. Each dispatch fetches their LIVE state into the
    worker brief — never a creation-time copy — a closed issue drops out of
    the remaining scope, and when every referenced issue is closed the goal
    proposes done without spending a worker session. Omit for the issue-less
    lane (bench/greenfield), which behaves exactly as before.

    mode selects the execution dial (ADR 0003): 'long_lived' (default) is the
    per-tick loop — plan the single next action each heartbeat, steerable
    mid-flight. 'one_shot' rides the SAME advance loop and proposes done as
    soon as an advance session lands — same gates; the worker owns the plan
    (speckit, spec 008). Use one_shot for a fully-specified batch of work;
    long_lived for a direction driven over time. 'qa' (spec 015) is the
    per-repo live-validation owner: it never plans feature work and never
    terminates (standing done_when supplied automatically); validation runs
    fire on completed deploys, and a periodic cadence exists but SHIPS OFF —
    arm it by passing an explicit cadence (e.g. '24h'); saga slots and
    done_when may be omitted for this mode.

    goal_id: a short stable slug (the on-disk folder name). objective: the durable
    aim. done_when: the prose completion test the evaluator judges against. backlog:
    a starting work-list. project_id: the registered project (see list_projects)
    whose workspace + repo devclaw resolves and keeps fresh per action — an unknown
    project is rejected synchronously. verify_cmd: the gate (e.g. 'dotnet test').
    spec: optional pre-aligned scope contract — when the OpenClaw waiter has
    grilled the customer (via scope_grill) before filing the order, pass the
    finalized spec.md here and the evaluator judges done against it.

    THE SAGA SLOTS (spec 012 US2) — ``out_of_scope``, ``invariants`` and
    ``established`` are REQUIRED alongside objective/done_when. A saga is
    authored from named slots, not prose, so that two people describing the
    same work file the same saga. Pass a list of short statements, or an EMPTY
    LIST to declare explicitly that there are none — omitting a slot is
    rejected here, naming it, rather than discovered by a worker mid-run.
    out_of_scope: what this goal deliberately does NOT include (the worker will
    not build into it). invariants: what must still hold after every increment
    (a change that breaks one is not shippable). established: settled decisions
    the worker must build on instead of re-deriving."""
    if not goal_id:
        raise ToolError("create_goal requires goal_id")
    if mode not in ("long_lived", "one_shot", "qa"):
        raise ToolError("create_goal mode must be 'long_lived', 'one_shot' or 'qa'")
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
                done_when=done_when, backlog=backlog, cadence=cadence,
                repo_url=resolved.repo_url, verify_cmd=verify_cmd, open_pr=open_pr,
                spec=spec, mode=mode, strictness=strictness,
                project_id=resolved.project_id, out_of_scope=out_of_scope,
                invariants=invariants, established=established,
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
        # Structured rejection: surface the full condition list so the waiter
        # can render fixable items to the customer and route on the codes.
        raise ToolError(json.dumps(exc.result.to_dict(), indent=2))


@mcp.tool
async def verify_goal(
    objective: str,
    project_id: str,
    done_when: str = "",
    backlog: Optional[list[str]] = None,
    verify_cmd: Optional[str] = None,
    spec: str = "",
    out_of_scope: Optional[list[str]] = None,
    invariants: Optional[list[str]] = None,
    established: Optional[list[str]] = None,
) -> str:
    """Pre-flight check for a goal BEFORE you call create_goal. Runs the same
    structural validations the chef applies at goal-creation time and returns
    a list of conditions (severity ``reject`` or ``warn``) with machine-readable
    codes the waiter can route on.

    Use this to preview rejections so the customer sees fixable conditions
    before they think the order was filed. ``admitted: false`` means
    create_goal would reject; ``admitted: true`` with warnings means
    create_goal would accept but flag. ``project_id`` names the registered
    project whose workspace + repo the goal would run in (same reference key as
    create_goal); an unknown project is rejected synchronously.

    Response shape:
      {"admitted": bool,
       "conditions": [{"code": "...", "severity": "reject"|"warn",
                       "message": "...", "field": "..."}, ...]}
    """
    resolved = _resolve_project_or_reject(project_id, "verify_goal")
    return json.dumps(
        goals.verify_goal(
            objective=objective, workspace_dir=resolved.workspace_dir,
            done_when=done_when, backlog=backlog, repo_url=resolved.repo_url,
            verify_cmd=verify_cmd, spec=spec, out_of_scope=out_of_scope,
            invariants=invariants, established=established,
        ),
        indent=2,
    )


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
    """Correct or redirect a durable goal. The message is recorded as steering and
    the next-action planner honors it over the backlog on the next tick (which is
    poked immediately). Unblocks a blocked goal. Use to change direction, add work,
    or answer what a goal is blocked on — e.g. 'use Postgres, not SQLite' or
    'skip the admin UI, focus on the API'."""
    if not goal_id or not message:
        raise ToolError("steer_goal requires goal_id and message")
    try:
        return json.dumps(goals.steer_goal(goal_id, message), indent=2)
    except KeyError:
        raise ToolError(f"unknown goal_id: {goal_id}")


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
