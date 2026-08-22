"""All MCP tool decorators — the chef's menu.

Each tool delegates to the long-lived services in ``_state`` (queue, store,
goals, registry) or to a sibling module (``deploy``, ``repo``). The tools stay
thin on purpose: validate inputs, dispatch, return JSON. Cognition lives below
(planner / evaluator / review gate), not here.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal, Optional

from fastmcp.exceptions import ToolError
from pydantic import Field

from ..delivery import deploy as _deploy
from .. import host_resources as _host_resources
from .. import elicitation as _elicitation
from .. import intake as _intake
from ..delivery import repo as _repo
from ..dispatch_gate import (
    _parse_hhmm,
    next_window_open_ms,
    operator_block,
    schedule_blocks,
)
from pathlib import Path as _Path

from ..engine.workspace import (
    WorkspaceError,
    prepare_workspace,
    workspace_is_dispatchable,
)
from ..project_registry import (
    ProjectExists,
    ResolvedDispatch,
    UnknownProject,
    project_rollup,
)
from ..state_store import _now_ms
from .. import speckit_setup as _speckit
from ._state import goals, mcp, queue, registry, store


def _resolve_project_or_reject(project_id: str, tool: str) -> ResolvedDispatch:
    """Resolve a dispatch reference key (spec 003 / #520) into its concrete
    workspace + repo, or reject the tool call synchronously. This is the single
    seam every dispatch/goal tool crosses instead of taking a raw path — an
    unknown project fails HERE with an actionable ToolError and zero task/engine
    work, never a claimed task that dies deep in the engine."""
    if not project_id:
        raise ToolError(f"{tool} requires project_id")
    try:
        return registry.resolve_dispatch(project_id)
    except UnknownProject:
        raise ToolError(
            f"unknown project_id: {project_id!r} — register it first "
            f"(register_project / list_projects)"
        )
    except ValueError as exc:
        raise ToolError(str(exc))


def _project_hold_warning(project_id: "str | None", workspace_dir: str) -> "str | None":
    """The FR-009 loud warning: name the goal currently working this project, or
    ``None`` when nothing holds it.

    Direct dispatches are deliberately EXEMPT from the single-writer hold (the
    clarify ruling: an operator-present task is the human judgement call, and
    the hold exists to stop unattended concurrent planners) — so this only
    warns, never blocks. Best-effort: a lookup hiccup must not fail a dispatch
    the operator explicitly asked for."""
    try:
        from ..goal import project_hold as _project_hold

        holders = _project_hold.holder_map(goals._goal_store)
        holder = holders.get((project_id or "").strip() or (workspace_dir or "").strip().rstrip("/"))
        if not holder:
            return None
        return (
            f"goal {holder} is actively working this project. This dispatch is "
            "exempt from the one-goal-per-project rule because you are driving "
            "it, but the two of you are now writing to the same repository — "
            "expect to reconcile."
        )
    except Exception:  # noqa: BLE001 — advisory only; never fail an explicit dispatch
        return None


async def _preflight_or_prep(resolved: ResolvedDispatch, project_id: str) -> None:
    """Direct-path dispatch preflight + auto-prep (spec 003 US2/US4, #520 P2).

    P1 (US2): reject at admission if the resolved workspace isn't a real git
    checkout, BEFORE queue.submit claims the task — never a late sandbox failure.

    P2 (US4, #523): when the workspace is simply ABSENT and the project carries a
    ``repo_url``, auto-clone it from that repo instead of rejecting — extending
    to the direct path the convenience the goal path already has (its workspace
    is cloned by ``prepare_workspace`` on the tick). Scope is deliberately narrow:
    - absent + repo_url  → clone, then proceed (or reject loud if the clone fails)
    - absent + no repo_url → reject (nothing to clone from)
    - exists-but-non-git → reject (git clone can't land in a non-empty dir; and
      resetting an existing checkout is not the direct path's job)

    Still zero-token (git subprocess only; no LLM). The GOAL path routes through
    ``prepare_workspace`` + ``_block_on_prep_failure`` and does not use this."""
    reason = workspace_is_dispatchable(resolved.workspace_dir)
    if reason is None:
        return
    absent = not _Path(resolved.workspace_dir).exists()
    if absent and resolved.repo_url:
        try:
            await prepare_workspace(resolved.workspace_dir, resolved.repo_url)
        except WorkspaceError as exc:
            raise ToolError(
                f"cannot dispatch to project {project_id!r}: auto-prep from "
                f"{resolved.repo_url!r} failed: {exc}"
            )
        return
    raise ToolError(f"cannot dispatch to project {project_id!r}: {reason}")


async def _block_if_speckit_pending(resolved: ResolvedDispatch, tool: str) -> None:
    """Block FEATURE dispatch for a repo whose speckit install PR is unmerged —
    no half-installed execution (spec 008 US2, contracts/onboard-adopt-install).

    Cheap and off the idle path: consulted only at dispatch time (never on the
    heartbeat/idle tick). Skips the network probe entirely when the repo already
    commits ``.specify/`` (speckit-ready). A repo with no ``.specify/`` and no
    open install PR has simply never been onboarded and is NOT blocked — only an
    actually-open install PR blocks."""
    if await _speckit.has_committed_speckit(resolved.workspace_dir):
        return
    pr = await _speckit.open_install_pr(resolved.workspace_dir)
    if pr:
        raise ToolError(
            f"cannot dispatch feature work via {tool}: the speckit install PR "
            f"({pr}) is still open — review and merge it first, then dispatch "
            f"(no half-installed execution)."
        )
    # A None above is fail-open — it also means "gh couldn't tell". Fail CLOSED
    # when there's concrete local evidence of a pending install (an install
    # branch) AND gh is unavailable: a gh hiccup must not silently admit
    # half-installed execution. Inert for legacy repos (no install branch) and in
    # the stubbed suite (open_install_pr is faked, so gh is never consulted).
    if (
        await _speckit.local_install_branch_exists(resolved.workspace_dir)
        and await _speckit.gh_unavailable(resolved.workspace_dir)
    ):
        raise ToolError(
            f"cannot dispatch feature work via {tool}: the speckit install state "
            f"for this repo is unverifiable (gh unavailable) and a local install "
            f"branch exists — resolve the install first (no half-installed execution)."
        )


@mcp.tool
async def dispatch_task(
    kind: Literal["implement_feature", "fix_bug", "review_repository"],
    project_id: str,
    goal: str,
    notify_url: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    open_pr: bool = False,
    base_branch: Optional[str] = None,
    target_branch: Optional[str] = None,
) -> str:
    """One-shot dispatch of a code task to the worker in a REGISTERED project's
    workspace. ``project_id`` names a project (see ``list_projects`` /
    ``register_project``); devclaw resolves its workspace + repo from the
    registry row — you never pass a raw path. An unknown project, or one whose
    workspace isn't a real git checkout, is rejected immediately. Returns a
    task_id; the task runs asynchronously. Poll get_status(task_id), or pass
    notify_url to be pushed the result.

    ``kind`` selects the prompt bias:
      - ``implement_feature`` — new features / open-ended changes.
      - ``fix_bug`` — biases toward reading existing code first, making the
        smallest fix, not refactoring unrelated code, and running the tests.
      - ``review_repository`` — READ-ONLY code review; the agent inspects the
        workspace and writes a report, prompt-instructed NOT to modify any
        files. ``verify_cmd`` and ``open_pr`` are ignored for this kind.

    Pass verify_cmd (e.g. "dotnet test", "npm run build && npm run test:ci") to
    gate the task: after the agent finishes, DevClaw runs that command in the
    workspace and the task only succeeds if it exits 0 — the agent's own
    "I'm done" is not trusted. A failing gate marks the task failed with the
    command output captured.

    Pass open_pr=True to DELIVER a successful change as something you review: on
    ``done``, DevClaw commits it to a branch, pushes, and opens a PR (best-effort;
    needs git push auth + a GitHub remote), recording the PR URL on the task.

    Branch targeting (both optional; defaults = today's behavior — a fresh
    auto-named branch, PR to the repo's default branch):
      - ``target_branch`` — CONTINUE this branch: before the agent runs, the
        workspace is force-checked-out to it (fetched to its origin tip if it
        exists, else created off ``base_branch``; uncommitted local changes are
        discarded), and the delivery must land on it — reusing its single open
        PR when one exists. Delivery landing anywhere else fails the task.
        Also selects the branch a ``review_repository`` task reviews.
      - ``base_branch`` — the PR base and diff range (e.g. "develop"). It must
        resolve on the workspace's origin; a base that doesn't fails the task
        up front with an actionable message.

    Prefer this over the older ``implement_feature`` / ``fix_bug`` /
    ``review_repository`` tools — those are kept as back-compat aliases and
    forward here."""
    if not goal:
        raise ToolError("dispatch_task requires project_id and goal")
    resolved = _resolve_project_or_reject(project_id, "dispatch_task")
    await _preflight_or_prep(resolved, project_id)
    is_review = kind == "review_repository"
    # Feature work (not read-only review) is gated on a merged speckit install:
    # a repo whose install PR is still open is not run (US2, no half-installed state).
    if not is_review:
        await _block_if_speckit_pending(resolved, "dispatch_task")
    task_id = queue.submit(
        kind=kind,
        workspace_dir=resolved.workspace_dir,
        goal=goal,
        notify_url=notify_url,
        verify_cmd=None if is_review else verify_cmd,
        deliver=False if is_review else open_pr,
        base_branch=base_branch,
        target_branch=target_branch,
        project_id=resolved.project_id,
    )
    out = {"task_id": task_id, "status": "pending"}
    # Spec 010 FR-009: a goal-less direct dispatch is EXEMPT from the
    # single-writer project hold — an operator-present task IS the human
    # judgement call, and the hold exists to stop UNATTENDED concurrent
    # planners. Exempt, but never silent: say loudly that a goal is working
    # this project, then proceed (constitution VI).
    warning = _project_hold_warning(resolved.project_id, resolved.workspace_dir)
    if warning:
        out["warning"] = warning
    return json.dumps(out, indent=2)


@mcp.tool
async def implement_feature(
    project_id: str,
    goal: str,
    notify_url: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    open_pr: bool = False,
) -> str:
    """DEPRECATED — thin forwarder to ``dispatch_task(kind="implement_feature")``.
    Kept for back-compat with existing MCP callers; prefer ``dispatch_task``
    for new integrations. See ``dispatch_task`` for full docs."""
    return await dispatch_task(
        kind="implement_feature",
        project_id=project_id,
        goal=goal,
        notify_url=notify_url,
        verify_cmd=verify_cmd,
        open_pr=open_pr,
    )


@mcp.tool
async def fix_bug(
    project_id: str,
    description: str,
    notify_url: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    open_pr: bool = False,
) -> str:
    """DEPRECATED — thin forwarder to ``dispatch_task(kind="fix_bug")``.
    Kept for back-compat with existing MCP callers; prefer ``dispatch_task``
    for new integrations. See ``dispatch_task`` for full docs."""
    if not description:
        raise ToolError("fix_bug requires project_id and description")
    return await dispatch_task(
        kind="fix_bug",
        project_id=project_id,
        goal=description,
        notify_url=notify_url,
        verify_cmd=verify_cmd,
        open_pr=open_pr,
    )


@mcp.tool
async def review_repository(
    project_id: str, focus: str = "", notify_url: Optional[str] = None
) -> str:
    """DEPRECATED — thin forwarder to ``dispatch_task(kind="review_repository")``.
    Kept for back-compat with existing MCP callers; prefer ``dispatch_task``
    for new integrations. See ``dispatch_task`` for full docs."""
    return await dispatch_task(
        kind="review_repository",
        project_id=project_id,
        goal=focus or "general code review",
        notify_url=notify_url,
    )


@mcp.tool
async def file_intake(
    project_id: str,
    what: str,
    done_when: str,
    asker: str,
    channel: Literal["chat", "telegram", "a2a", "other"],
    context: Optional[str] = None,
) -> str:
    """Stage 1 of the single intake doorway: record an ask as a durable,
    labeled GitHub issue on the target registered project's repo, and return
    the issue URL — the asker's receipt. EVERY ask from every source (human or
    agent) enters devclaw through this tool; it can only create issues, never
    dispatch. Execution admission (stage 2) is a separate step by the
    authorized dispatcher via ``dispatch_task``/``create_goal``, referencing
    the intake issue.

    - ``project_id`` — a registered project (see ``list_projects``); the issue
      is filed on its ``repoUrl`` repo. Unknown project ⇒ synchronous reject.
    - ``what`` — the ask, one paragraph.
    - ``done_when`` — verifiable completion criteria (≥ 20 chars).
    - ``asker`` / ``channel`` — provenance, recorded (not authenticated) and
      stamped server-side along with the filing timestamp.
    - ``context`` — optional evidence: where seen, repro, links.

    Returns ``{issue_url, project_id, repo}``. A filing failure raises with an
    actionable message — there is no receipt unless the issue really exists."""
    try:
        result = await _intake.file_intake(
            registry,
            project_id=project_id,
            what=what,
            done_when=done_when,
            asker=asker,
            channel=channel,
            context=context,
            now_ms=_now_ms(),
        )
    except _intake.IntakeError as exc:
        raise ToolError(str(exc)) from exc
    # Async readiness grade (spec 006): the receipt is already real; the grade
    # lands moments later as a durable label. Scheduled here (intake path only),
    # NEVER on the heartbeat — the zero-token idle guard stays intact (FR-009).
    # Filing is never blocked on cognition (FR-011): a slow/paused grade cannot
    # delay this return.
    _schedule_readiness_grade(result, what=what, done_when=done_when, context=context)
    return json.dumps(result, indent=2)


#: strong refs to in-flight background grades so the event loop doesn't GC them.
_GRADE_TASKS: set = set()


def _schedule_readiness_grade(
    result: dict, *, what: str, done_when: str, context: Optional[str]
) -> None:
    import asyncio
    import sys as _sys

    project = registry.get(result["project_id"])
    workspace_dir = getattr(project, "workspace_dir", "") or "" if project else ""

    async def _run() -> None:
        try:
            from ..intake_readiness import default_caller

            await _intake.grade_and_label(
                repo=result["repo"],
                issue=result["issue_url"],
                what=what,
                done_when=done_when,
                context=context,
                workspace_dir=workspace_dir,
                claude_caller=default_caller(),
            )
        except Exception as exc:  # noqa: BLE001 — background task, log and drop
            _sys.stderr.write(f"file_intake: readiness grade failed: {exc}\n")

    try:
        task = asyncio.create_task(_run())
    except RuntimeError:
        # No running loop (unusual for the async tool path) — skip scheduling
        # rather than crash the receipt. The grade can be re-triggered manually.
        return
    _GRADE_TASKS.add(task)
    task.add_done_callback(_GRADE_TASKS.discard)


@mcp.tool
async def regrade_intake(project_id: str, issue_url: str) -> str:
    """Grade (or re-grade) ANY open issue on a registered project's repo —
    devclaw intake format or hand-written (spec 006 FR-010 + spec 009 universal
    adoption). Intake-format issues keep their structured sections; any other
    format is read as-is: title + body become the ask, and an issue with no
    verifiable completion intent grades ``needs-refinement`` with the missing
    element named. Re-reads the issue on demand — devclaw does NOT auto-watch
    issue edits. Grading is admission to *grading*, never execution: it does not
    dispatch, does not edit the issue, and does not alter provenance.

    - ``project_id`` — the registered project the issue lives on.
    - ``issue_url`` — the issue's URL (must be open; closed issues reject).

    Returns ``{issue_url, project_id, repo, readiness}``. The grade fails
    CLOSED: any failure to reach a confident ready verdict lands
    ``needs-refinement``, never ``devclaw-ready``."""
    try:
        result = await _intake.regrade(
            registry, project_id=project_id, issue=issue_url
        )
    except _intake.IntakeError as exc:
        raise ToolError(str(exc)) from exc
    return json.dumps(result, indent=2)


@mcp.tool
async def grade_backlog(project_id: str) -> str:
    """Bulk-onboard a registered project's existing backlog into the readiness
    pipeline (spec 009): grade up to 20 open, not-yet-graded issues — any
    format, priority-band-first (P0…P5, oldest first within a band) — through
    the same fail-closed grade as ``regrade_intake``. Already-graded issues are
    skipped with zero cognition. When more remain beyond the cap they are
    reported ``not_yet_graded``; continuing is a fresh explicit call — there is
    NO automatic continuation. The pending set is derived from the readiness
    labels themselves, so re-invocation resumes exactly where the last run
    stopped. Grades only — never dispatches, never edits issues.

    Returns the per-issue report: ``graded_ready`` / ``graded_needs_refinement``
    / ``failed`` (with reasons) / ``skipped_already_graded`` / ``not_yet_graded``,
    plus ``cap`` and the ``listing_limit`` page bound. A listing failure raises
    loudly — an explicit call never silently degrades to an empty sweep."""
    try:
        result = await _intake.grade_backlog(registry, project_id=project_id)
    except _intake.IntakeError as exc:
        raise ToolError(str(exc)) from exc
    return json.dumps(result, indent=2)


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
async def onboard(
    project_id: str, focus: str = "", notify_url: Optional[str] = None
) -> str:
    """Onboard a repository: analyze it and write a DRAFT documentation set
    (plus the project's dev-container boilerplate) so future tasks + humans
    start informed and in the project's real environment. The worker inspects the
    workspace READ-ONLY (it modifies no file except the four named onboarding
    artifacts below) — three scoped docs plus one build artifact:

      - AGENTS.md      — a THIN, BOUNDED pointer (~1 page): what the repo is,
                         exact build/run/test commands (with the verify gate),
                         layout pointers, links out to ARCHITECTURE.md /
                         .agent/skills/ / specs/. Devclaw-owned content sits
                         between ``devclaw:managed`` markers; a re-onboard
                         replaces within the markers and preserves everything
                         outside them.
      - README.md      — human-facing: one-paragraph purpose, quickstart,
                         high-level pointer at layout, one-line status.
      - ARCHITECTURE.md — component map, data flow, cross-cutting concerns,
                         notable design decisions (cross-links the feature's
                         ``specs/`` artifacts — the spec is the decision
                         memory; no separate ADR log).
      - .devcontainer/Dockerfile — the DEV environment a human and the agent
                         share (SDK/toolchain image); authored ONLY when the
                         repo has none, so the agent runs in the project's real
                         toolchain instead of re-deriving it per task.

    The onboarding skill (`skills/onboard/00-onboard.md`) enforces boundary
    discipline (no ADR reasoning in README, no quickstart in ARCHITECTURE,
    no narrative in AGENTS.md) so the three docs don't blur into each other.

    Human-in-the-loop: the doc set arrives as a REVIEWABLE PR (a `docs`-typed
    delivery, so it stays behind a human merge), and each doc lands with a
    top-of-file DRAFT marker — not authoritative until you review it. The agent
    won't clobber a substantive existing doc — it validates each part against
    the real repo and only corrects what's wrong or missing. A re-onboard that
    finds everything already accurate is a success with no PR. Returns task_id
    immediately; same optional notify_url as implement_feature.

    Speckit substrate (spec 008 US2): every repo devclaw works uses speckit.
    Onboard decides on the repo's COMMITTED ``.specify/`` directory —
      - present ⇒ ADOPT (the repo already uses speckit) — writes no plan file,
        opens no scaffolding PR, and proceeds to the comprehension-doc pass.
      - absent  ⇒ INSTALL — generates the ``.specify/`` scaffold and opens a
        REVIEWABLE PR (never a silent commit to the default branch). The repo
        isn't run for feature work until that PR merges (no half-installed
        state); re-run onboard after merge to produce the docs."""
    resolved = _resolve_project_or_reject(project_id, "onboard")
    await _preflight_or_prep(resolved, project_id)

    # Bare repo (no committed .specify/) ⇒ install speckit via a reviewable PR
    # before anything else. Never a silent commit to the default branch (FR-003).
    if not await _speckit.has_committed_speckit(resolved.workspace_dir):
        result = await _speckit.install_speckit_pr(
            resolved.workspace_dir, project_id=resolved.project_id
        )
        return json.dumps(
            {
                "speckit": "install_pr",
                "pr_url": result.get("pr_url"),
                "branch": result.get("branch"),
                "created": result.get("created", []),
                "error": result.get("error"),
                "note": (
                    "speckit was absent — opened a reviewable install PR carrying "
                    "the .specify/ scaffold. Merge it, then re-run onboard to "
                    "generate the comprehension docs. Feature dispatch is blocked "
                    "for this repo until the install PR merges."
                ),
            },
            indent=2,
        )

    # Committed .specify/ present ⇒ adopt: no plan file, no scaffolding PR; run the
    # comprehension-doc onboarding pass as usual.
    task_id = queue.submit(
        kind="onboard",
        workspace_dir=resolved.workspace_dir,
        goal=focus or "general onboarding",
        notify_url=notify_url,
        # #598: without this the generated docs are left UNTRACKED in the
        # workspace and the task still settles `done` — the next dispatch's
        # `git clean -fdx` then deletes them. Routing through the shared
        # deliver seam (never a second one) also buys the three outcomes for
        # free: delivered => done + pr_url, nothing-to-deliver => done with no
        # PR, delivery broken => failed (#183). `_KIND_TYPE["onboard"]` is
        # already "docs", so the PR lands behind a human merge as intended.
        deliver=True,
        project_id=resolved.project_id,
    )
    return json.dumps(
        {"task_id": task_id, "status": "pending", "speckit": "adopted"}, indent=2
    )


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
    from ..goal.admission import GoalAdmissionRejected

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


@mcp.tool
async def get_status(task_id: str) -> str:
    """Return the current status + (when terminated) the result or error of a
    task. Status values: pending | running | done | failed | cancelled."""
    task = store.get_task(task_id)
    if not task:
        raise ToolError(f"unknown task_id: {task_id}")
    return json.dumps(task.to_dict(), indent=2)


@mcp.tool
async def get_program(program_id: str) -> str:
    """Return a program row and all its tasks in dependency order. Program-level
    observability one layer below the goal: for a goal-dispatched program the goal
    (get_goal / tail_goal) is the unit you own and steer — this is its internal
    task DAG. Use to poll a program submitted via start_program, or to inspect a
    goal's child program at per-task detail."""
    program = store.get_program(program_id)
    if not program:
        raise ToolError(f"unknown program_id: {program_id}")
    tasks = store.list_program_tasks(program_id)
    return json.dumps(
        {"program": program.to_dict(), "tasks": [t.to_dict() for t in tasks]}, indent=2
    )


@mcp.tool
async def list_programs(limit: Annotated[int, Field(ge=1, le=1000)] = 50) -> str:
    """List recent programs (parallel task DAGs), most-recent first. Program-level
    plumbing one layer below the goal: goals dispatch programs, and the
    start_program alias files a one-shot GOAL whose child program lands here once
    dispatched — steer and cancel via the goal, not the program. Use to discover
    program_ids for get_program or get_events."""
    programs = store.list_programs(limit=limit)
    return json.dumps([p.to_dict() for p in programs], indent=2)


@mcp.tool
async def get_events(
    program_id: Optional[str] = None,
    task_id: Optional[str] = None,
    since_id: Optional[int] = None,
    limit: Annotated[int, Field(ge=1, le=5000)] = 500,
) -> str:
    """Return events emitted by the worker runner for one program or one
    task, in emission order. Each event has an id (monotonic cursor), type,
    source, payload_json (the raw SDK Event), and ts. Pass since_id to resume —
    same semantics as the /programs/:id/events SSE Last-Event-Id."""
    if not program_id and not task_id:
        raise ToolError("get_events requires program_id or task_id")
    events = store.list_events(
        program_id=program_id, task_id=task_id, since_id=since_id, limit=limit
    )
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
    """L8 rolling scorecard: merge rate, evaluator verdict distribution, steer
    rate, first-pass hit rate, workspace-break count — computed over the last
    ``window_hours`` (default 168 = one week). Reads state_store directly, so
    it's cheap and can be called from Telegram or a dashboard without waking
    the goal loop. See ``plan.md`` §Measurement direction for how the numbers
    relate to the C1-C8 production-ready scorecard."""
    from ..telemetry import compute_scorecard
    return json.dumps(compute_scorecard(store, window_hours=int(window_hours)), indent=2)


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
    from ..state_store.problems import problem_lifecycle

    problems = store.list_problems(
        category=category, limit=int(limit), include_issue=True
    )
    for p in problems:
        p["lifecycle"] = problem_lifecycle(p)
    return json.dumps(
        {"count": len(problems), "problems": problems}, indent=2
    )


# ===== cancellation (deliberate abort) =======================================


@mcp.tool
async def cancel_task(task_id: str) -> str:
    """Abort a running or pending task. Tears down its sandbox and marks it
    'cancelled' (a terminal state distinct from 'failed' — it won't be retried or
    resurrected on restart). Cancelling a task that belongs to a program also
    stops that program. No-op if the task already finished. Returns whether an
    abort actually happened."""
    if not task_id:
        raise ToolError("cancel_task requires task_id")
    if not store.get_task(task_id):
        raise ToolError(f"unknown task_id: {task_id}")
    cancelled = queue.cancel_task(task_id)
    return json.dumps(
        {"task_id": task_id, "cancelled": cancelled, "status": "cancelled" if cancelled else None},
        indent=2,
    )


@mcp.tool
async def cancel_program(program_id: str) -> str:
    """Abort a whole standalone program: stop scheduling new tasks, tear down
    every running task's sandbox, and mark the program 'cancelled'. No-op if the
    program already terminated. Returns whether an abort happened.

    Program-level plumbing, not the operator's primary kill switch. A program
    dispatched by a goal (``parent_goal_id`` set — every one_shot goal, every
    start_program) is OWNED by that goal: cancel it with ``cancel_goal``, which
    cascades DOWN and tears this program down as part of stopping the goal. This
    tool therefore REJECTS a goal-owned program — cancelling it directly does not
    cascade UP, and would leave the goal executing and desynced from its dead
    program (the tick then has to reconcile a program it never chose to lose)."""
    if not program_id:
        raise ToolError("cancel_program requires program_id")
    program = store.get_program(program_id)
    if not program:
        raise ToolError(f"unknown program_id: {program_id}")
    if program.parent_goal_id:
        raise ToolError(
            f"program {program_id} is owned by goal '{program.parent_goal_id}' — "
            f"cancel the goal instead: cancel_goal('{program.parent_goal_id}') "
            f"stops the goal and cascades down to tear this program down. "
            f"Cancelling the program directly does not cascade up and would leave "
            f"the goal executing and desynced from its dead program."
        )
    cancelled = queue.cancel_program(program_id)
    return json.dumps(
        {"program_id": program_id, "cancelled": cancelled, "status": "cancelled" if cancelled else None},
        indent=2,
    )


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
    from ..goal.models import Goal

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

    from ..goal import evaluator as _eval
    from ..goal.models import GoalStatus

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
    strictness: str = "trust",
) -> str:
    """Register a goal that DevClaw drives: on each heartbeat it plans, dispatches
    to the engine, records what shipped, and only closes when a grounded review
    confirms done_when is met. Steer it any time with steer_goal; inspect it with
    get_goal.

    mode selects the execution dial (ADR 0003): 'long_lived' (default) is the
    per-tick loop — plan the single next action each heartbeat, steerable
    mid-flight. 'one_shot' rides the SAME advance loop and proposes done as
    soon as an advance session lands — same gates; the worker owns the plan
    (speckit, spec 008). Use one_shot for a fully-specified batch of work;
    long_lived for a direction driven over time.

    goal_id: a short stable slug (the on-disk folder name). objective: the durable
    aim. done_when: the prose completion test the evaluator judges against. backlog:
    a starting work-list. project_id: the registered project (see list_projects)
    whose workspace + repo devclaw resolves and keeps fresh per action — an unknown
    project is rejected synchronously. verify_cmd: the gate (e.g. 'dotnet test').
    spec: optional pre-aligned scope contract — when the OpenClaw waiter has
    grilled the customer (via scope_grill) before filing the order, pass the
    finalized spec.md here and the evaluator judges done against it."""
    if not goal_id:
        raise ToolError("create_goal requires goal_id")
    if mode not in ("long_lived", "one_shot"):
        raise ToolError("create_goal mode must be 'long_lived' or 'one_shot'")
    if strictness not in ("trust", "strict"):
        raise ToolError("create_goal strictness must be 'trust' or 'strict'")
    # Resolve the project reference key → workspace + repo (spec 003 / #520).
    # Unknown project rejects here; the workspace is NOT preflighted for
    # existence — a goal's workspace is cloned/reset by prepare_workspace on the
    # first tick (which blocks-and-heals a non-git/no-repo workspace itself).
    resolved = _resolve_project_or_reject(project_id, "create_goal")
    # objective is checked inside admission and surfaced as a structured
    # condition — don't duplicate it here.
    from ..goal.admission import GoalAdmissionRejected

    try:
        return json.dumps(
            goals.create_goal(
                goal_id, objective=objective, workspace_dir=resolved.workspace_dir,
                done_when=done_when, backlog=backlog, cadence=cadence,
                repo_url=resolved.repo_url, verify_cmd=verify_cmd, open_pr=open_pr,
                spec=spec, mode=mode, strictness=strictness,
                project_id=resolved.project_id,
            ),
            indent=2,
        )
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
            verify_cmd=verify_cmd, spec=spec,
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
    ``cost_usd``; legacy rows and fallback calls carry only the ``_est`` len/4
    estimates — labeled as estimates), the FULL response text, and the chain of
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


# ===== build a project from scratch ==========================================


@mcp.tool
async def create_repo(
    name: str,
    private: bool = False,
    description: str = "",
) -> str:
    """Create a fresh GitHub repo under the configured account so a from-scratch
    goal has somewhere to live. Returns {created, existed, repo, clone_url}. The
    repo is seeded with a README (initial commit + a 'main' default branch) so it
    can be cloned and PR'd against immediately. Idempotent: if the name already
    exists it returns that repo instead of failing. Feed the returned clone_url
    into create_goal(repo_url=...). Auth is gh's own login (repo write access).
    Public by default — Actions on private repos never start under the
    account's billing lock; pass private=true only for sensitive content."""
    if not name:
        raise ToolError("create_repo requires a name")
    try:
        result = await _repo.create_repo(name, private=private, description=description)
    except _repo.RepoError as err:
        raise ToolError(str(err))
    if result.get("created"):
        # Provenance: only repos devclaw itself stood up enter the managed-repo
        # ledger — an `existed` hit is somebody else's repo and must never
        # become deletable through delete_repo.
        registry.record_managed_repo(result["repo"])
    return json.dumps(result, indent=2)


@mcp.tool
async def delete_repo(name: str, confirm: str = "") -> str:
    """Permanently DELETE a GitHub repo that devclaw itself created — the teardown
    counterpart of create_repo, for retiring scratch/bench repos without operator
    gh gymnastics. IRREVERSIBLE, and guarded four ways: the repo must be in
    devclaw's managed-repo ledger (recorded by create_repo — a pre-existing,
    human-owned repo can NEVER be deleted here, whatever is passed); `confirm`
    must echo the repo's exact 'owner/name' slug (call once without it and the
    error tells you the exact string); the repo must not be referenced by any
    registered project — delete_project first; and the gh token must carry the
    delete_repo scope (grant once via `gh auth refresh -h github.com -s
    delete_repo`). An unknown repo raises, so a typo never silently no-ops. This
    is an operator verb on the MCP surface only — nothing in the autonomous loop
    calls it."""
    if not name:
        raise ToolError("delete_repo requires a name")
    # Ownership is the first gate: devclaw only deletes what devclaw stood up.
    # Check BOTH the input-derived slug and the confirm slug — gh follows
    # renames, and the ledger holds the slug as it was at creation time.
    managed_candidates = {_repo.full_slug(name)}
    if "/" in confirm:
        managed_candidates.add(confirm)
    if not any(registry.is_managed_repo(c) for c in managed_candidates):
        raise ToolError(
            f"repo '{name}' is not in devclaw's managed-repo ledger — devclaw "
            "only deletes repos it created itself via create_repo. A pre-existing "
            "or human-owned repo must be deleted by hand with gh."
        )
    # Second gate: a repo still referenced by a registered project can't be
    # deleted out from under it. Matches on BOTH slugs for the same rename
    # reason (deletion only proceeds when `confirm` echoes the canonical slug).
    candidates = {_repo.slug_repo_name(name).lower()}
    if "/" in confirm:
        candidates.add(confirm.rsplit("/", 1)[-1].lower())
    referenced = [
        p.id
        for p in registry.list()
        if any(
            (p.repo_url or "").rstrip("/").removesuffix(".git").lower().endswith(f"/{c}")
            for c in candidates
        )
    ]
    if referenced:
        raise ToolError(
            f"repo '{name}' is still referenced by registered project(s) "
            f"{referenced} — delete_project (or update_project) first, "
            "then delete the repo"
        )
    try:
        out = await _repo.delete_repo(name, confirm=confirm)
    except _repo.RepoError as err:
        raise ToolError(str(err))
    # The repo is gone — retire every ledger alias for it (creation-time slug
    # and canonical slug can differ after a rename).
    for slug in managed_candidates | {out["repo"]}:
        registry.forget_managed_repo(slug)
    return json.dumps(out, indent=2)


# ===== durable deploy hosting ================================================
# Long-lived, reboot-surviving container at a STABLE per-slug URL over Tailscale.
# Auto-fires when a goal reaches `achieved` (see goal_tick). See devclaw/deploy.py.


@mcp.tool
async def deploy_project(workspace_dir: str, slug: str) -> str:
    """Deploy a project's BUILT app as a DURABLE host on the VPS and return its stable
    Tailscale URL — so the owner is HANDED a running product to open, not a diff to
    read. Survives reboots (--restart unless-stopped), lives at a fixed per-slug
    port so the URL never changes across redeploys, and is reachable over Tailscale
    (https, auto-TLS, never public). Idempotent: redeploying the same slug replaces
    the container at the same URL. workspace_dir = the goal's checkout; slug = a
    short stable name."""
    if not workspace_dir or not slug:
        raise ToolError("deploy_project requires workspace_dir and slug")
    try:
        return json.dumps(await _deploy.deploy_project(workspace_dir, slug), indent=2)
    except _deploy.DeployError as err:
        raise ToolError(str(err))


@mcp.tool
async def deploy_status(slug: str) -> str:
    """Status of a durable deploy: whether it exists, is running, is answering
    (ready), its stable Tailscale URL, and the one-time serve command."""
    if not slug:
        raise ToolError("deploy_status requires slug")
    return json.dumps(await _deploy.deploy_status(slug), indent=2)


@mcp.tool
async def stop_deploy(slug: str) -> str:
    """Stop and remove a durable deploy, tear down its Tailscale serve, and free its
    VPS resources."""
    if not slug:
        raise ToolError("stop_deploy requires slug")
    return json.dumps(await _deploy.stop_deploy(slug), indent=2)


@mcp.tool
async def list_deploys() -> str:
    """List all durable deploys (running + stopped) with their status."""
    return json.dumps(await _deploy.list_deploys(), indent=2)


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
    out = {
        "goal_id": goal_id,
        "schedule": schedule,
        "operator_hold": {"on": hold[0], "reason": hold[1]},
        "dispatch_open": not blocked,
        "why_blocked": why or None,
        "next_window_open_ms": next_window_open_ms(schedule, now),
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


# ===== project registry (control plane) ======================================
# The portfolio view: which repos devclaw owns + the live status of each. The
# registry links repos to their driving goals; status is joined live.


@mcp.tool
async def register_project(
    project_id: str,
    name: str,
    repo_url: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    preview_url: Optional[str] = None,
    notes: str = "",
    automerge: Optional[Literal["on", "off"]] = None,
    merge_strategy: Optional[Literal["squash", "merge", "rebase"]] = None,
    autodeploy: Optional[Literal["on", "off"]] = None,
    review_gate: Optional[Literal["on", "off"]] = None,
    verify_done: Optional[Literal["on", "off"]] = None,
) -> str:
    """Register a repo in the project registry — the control plane's source of
    truth for 'what is devclaw working on'. ``project_id`` is a stable slug (e.g.
    'todo-fullstack-demo'). Link the goal(s) driving it with link_goal. Idempotent
    failure: a taken id is an error (use update_project to change it).

    Per-project override knobs (each overrides its devclaw-wide env default;
    omit to inherit — the usual choice). This is the ONLY place these are
    configured per repo; a goal itself carries none of them:
      - ``automerge`` — auto-merge gate-passed PRs (DEVCLAW_GOAL_AUTOMERGE).
      - ``merge_strategy`` — squash|merge|rebase for the merge (DEVCLAW_GOAL_MERGE_STRATEGY).
      - ``autodeploy`` — deploy on goal completion (devclaw default: conditional —
        on only when the workspace has an app surface the preview launcher can
        serve; a pure library gets no preview container. 'on'/'off' pins it).
      - ``review_gate`` — run the pre-PR review gate (devclaw default: on).
      - ``verify_done`` — grounded done-gate re-check before closing (devclaw default: on)."""
    if not project_id or not name:
        raise ToolError("register_project requires project_id and name")
    _onoff = {"on": True, "off": False}
    try:
        p = registry.create(
            id=project_id, name=name, repo_url=repo_url,
            workspace_dir=workspace_dir, preview_url=preview_url, notes=notes,
            automerge=(None if automerge is None else automerge == "on"),
            merge_strategy=merge_strategy,
            autodeploy=(None if autodeploy is None else _onoff[autodeploy]),
            review_gate=(None if review_gate is None else _onoff[review_gate]),
            verify_done=(None if verify_done is None else _onoff[verify_done]),
        )
    except ProjectExists:
        raise ToolError(f"project already exists: {project_id}")
    return json.dumps(p.to_dict(), indent=2)


@mcp.tool
async def list_projects(status: Optional[str] = None) -> str:
    """List registered projects with a live status rollup (each project's linked
    goals' phase/direction + a derived health: working/blocked/done/idle/archived).
    Filter by status (active|paused|archived). This is the 'show me everything'
    surface for chat / API / CLI."""
    all_goals = goals.list_goals()
    items = [
        project_rollup(p, all_goals)
        for p in registry.list(status=status)  # type: ignore[arg-type]
    ]
    return json.dumps(items, indent=2)


@mcp.tool
async def project_status(project_id: str) -> str:
    """Full status of one registered project: its facts (repo, workspace, preview)
    plus the LIVE status of every goal driving it and a derived health signal."""
    p = registry.get(project_id)
    if p is None:
        raise ToolError(f"unknown project_id: {project_id}")
    return json.dumps(project_rollup(p, goals.list_goals()), indent=2)


@mcp.tool
async def update_project(
    project_id: str,
    name: Optional[str] = None,
    repo_url: Optional[str] = None,
    workspace_dir: Optional[str] = None,
    preview_url: Optional[str] = None,
    status: Optional[Literal["active", "paused", "archived"]] = None,
    notes: Optional[str] = None,
    automerge: Optional[Literal["on", "off", "inherit"]] = None,
    merge_strategy: Optional[Literal["squash", "merge", "rebase", "inherit"]] = None,
    autodeploy: Optional[Literal["on", "off", "inherit"]] = None,
    review_gate: Optional[Literal["on", "off", "inherit"]] = None,
    verify_done: Optional[Literal["on", "off", "inherit"]] = None,
    sandbox_image: Optional[str] = None,
) -> str:
    """Update a registered project's facts — only the fields you pass change. Use to
    record a preview URL, pause/archive it, or correct the repo/workspace.

    Per-project override knobs — ``automerge`` / ``merge_strategy`` /
    ``autodeploy`` / ``review_gate`` / ``verify_done`` / ``sandbox_image`` —
    each take a concrete value to PIN this project (overriding its devclaw-wide
    env default), 'inherit' to CLEAR a prior override back to that default, or
    omit to leave whatever is currently set untouched. (bool knobs take
    'on'/'off'; merge_strategy takes 'squash'/'merge'/'rebase'; sandbox_image
    takes a docker image ref — ADR 0005's escape hatch, e.g. pin
    'devclaw-sandbox-dotnet:local' until the mise path passes its gate.)"""
    override_kwargs: dict = {}
    _onoff = {"on": True, "off": False, "inherit": None}
    for field, val in (("automerge", automerge), ("autodeploy", autodeploy),
                       ("review_gate", review_gate), ("verify_done", verify_done)):
        if val is not None:
            override_kwargs[field] = _onoff[val]
    if merge_strategy is not None:
        override_kwargs["merge_strategy"] = None if merge_strategy == "inherit" else merge_strategy
    if sandbox_image is not None:
        override_kwargs["sandbox_image"] = None if sandbox_image == "inherit" else sandbox_image
    try:
        p = registry.update(
            project_id, name=name, repo_url=repo_url, workspace_dir=workspace_dir,
            preview_url=preview_url, status=status, notes=notes,
            **override_kwargs,
        )
    except KeyError:
        raise ToolError(f"unknown project_id: {project_id}")
    except ValueError as exc:
        # flag-shaped/empty sandbox_image etc. — surface the registry's
        # validation verdict instead of a bare 500
        raise ToolError(str(exc))
    return json.dumps(p.to_dict(), indent=2)


_TERMINAL_GOAL_PHASES = {"done", "cancelled", "error", "achieved"}


def _project_active_goal_ids(project) -> list[str]:
    """All non-terminal goal ids that belong to this project — by ``project_id``
    match (the authoritative join, #524 P3) OR by the advisory ``goal_ids`` list.

    Used by the one-goal-per-project warn (2026-07-04): both entry points
    (create_goal against this project, or link_goal directly) count toward the
    "already-has-active-goal" state. Re-keyed off the old normalized-workspace
    match so a workspace rename can't drift the count."""
    seen: set[str] = set()
    active: list[str] = []
    for g in goals.list_goals():
        gid = g.get("id")
        if not gid:
            continue
        if g.get("phase") in _TERMINAL_GOAL_PHASES:
            continue
        matches_project = g.get("project_id") == project.id
        matches_link = gid in (project.goal_ids or [])
        if matches_project or matches_link:
            if gid not in seen:
                seen.add(gid)
                active.append(gid)
    return active


@mcp.tool
async def link_goal(project_id: str, goal_id: str, unlink: bool = False) -> str:
    """Attach (or, with unlink=True, detach) a durable goal to/from a project. The
    link is by id only — the goal's status is joined live in list_projects /
    project_status, never copied. Idempotent.

    Warn-first one-goal-per-project (2026-07-04): if the project already has
    an active goal, linking a second one still succeeds but the response
    carries a ``warning`` field and the console renders a banner. Hard reject
    lands in a follow-up PR after the warn phase has bake time. Under the
    standing rule, a project pursues one well-defined goal at a time — if
    you need a new direction, cancel + refile."""
    try:
        p = (
            registry.unlink_goal(project_id, goal_id)
            if unlink
            else registry.link_goal(project_id, goal_id)
        )
    except KeyError:
        raise ToolError(f"unknown project_id: {project_id}")
    out = p.to_dict()
    if not unlink:
        other_active = [gid for gid in _project_active_goal_ids(p) if gid != goal_id]
        if other_active:
            out["warning"] = {
                "code": "multiple_active_goals",
                "message": (
                    "This project already has "
                    f"{len(other_active)} active goal(s): "
                    f"{', '.join(other_active)}. Under the one-goal-per-project "
                    "rule (2026-07-04) a project pursues one goal at a time — "
                    "cancel + refile instead of stacking. This will become a "
                    "hard error after the warn-first phase."
                ),
                "otherActiveGoalIds": other_active,
            }
    return json.dumps(out, indent=2)


@mcp.tool
async def delete_project(
    project_id: str,
    release_resources: bool = True,
    dry_run: bool = False,
) -> str:
    """Permanently remove a project from the registry (HARD delete) AND release the
    durable host resources it owned — its workspace checkout and its per-project
    toolchain volume. The goals it linked are untouched as records (they live in
    the goal store and are just unlinked from this view). To retire a project while
    keeping its record + a paper trail, prefer update_project(status='archived').
    Raises if the id is unknown (so a typo doesn't silently no-op).

    Release is REFUSED — and the registry row is kept — while any goal on that
    workspace is non-terminal or any task on it is still running; the response
    names the blockers. Pass release_resources=False to drop the record only (the
    pre-#595 behavior, which leaks the disk), or dry_run=True to see what would be
    released without deleting anything.
    """
    project = registry.get(project_id)
    if project is None:
        raise ToolError(f"unknown project_id: {project_id}")

    out: dict = {"project_id": project_id}
    if release_resources:
        release = _host_resources.release_for_project(
            getattr(project, "workspace_dir", None),
            goals=goals.list_goals(),
            running_tasks=store.list_tasks(status="running", limit=500),
            dry_run=dry_run,
        )
        out["resources"] = release
        if release["blocked"]:
            # Live state on that workspace — keep the record too. Deleting it
            # while its resources must stay would orphan them permanently: the
            # record is the only thing that can ever find them again.
            out["deleted"] = False
            return json.dumps(out, indent=2)

    if dry_run:
        out["deleted"] = False
        return json.dumps(out, indent=2)

    if not registry.delete(project_id):
        raise ToolError(f"unknown project_id: {project_id}")
    out["deleted"] = True
    return json.dumps(out, indent=2)
