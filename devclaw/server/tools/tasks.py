"""Direct dispatch + deliberate abort — the one-shot task surface.

``dispatch_task`` and its kind-specific companion verbs, plus cancel_task /
cancel_program (the teardown counterparts).
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from fastmcp.exceptions import ToolError

from ... import intake as _intake
from ... import speckit_setup as _speckit
from ...project_registry import ResolvedDispatch
from ...state_store import _now_ms
from .._state import goals, mcp, queue, store
from . import _common
from ._common import _preflight_or_prep, _resolve_project_or_reject


async def _auto_file_intake(registry, *, project_id: str, goal: str) -> int:
    """Auto-file an intake issue for a prose-only dispatch (spec 022 US3 FR-010).

    Returns the GitHub issue number. Raises ``_intake.IntakeError`` on failure
    so callers can wrap it into an actionable ToolError."""
    done_when = goal if len(goal) >= 20 else f"{goal} — implemented and verified"
    result = await _intake.file_intake(
        registry,
        project_id=project_id,
        what=goal,
        done_when=done_when,
        asker="devclaw",
        channel="a2a",
        now_ms=_now_ms(),
    )
    url: str = result["issue_url"]
    return int(url.rstrip("/").split("/")[-1])


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
    # half-installed execution. Inert for a repo with no install branch and in
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
    kind: Literal["implement_feature", "fix_bug", "review_repository", "validate_product"],
    project_id: str,
    goal: str,
    issue_ref: Optional[int] = None,
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
      - ``validate_product`` — spec 015's agent-less live validation: boots the
        repo's declared ``devclaw.json`` validation contract hermetically in
        the sandbox, runs the accumulated acceptance suites, and files each
        failure as a machine issue through the spec-014 doorway. Never opens a
        PR, never commits; ``verify_cmd``/``open_pr``/branch targets are
        ignored. The manual companion trigger for a validation run.

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

    The kind-specific companion verbs ``implement_feature`` / ``fix_bug`` /
    ``review_repository`` forward here. Reach for this one when you need the
    branch targets or an explicit ``kind``; either surface is supported."""
    if not goal:
        raise ToolError("dispatch_task requires project_id and goal")
    resolved = _resolve_project_or_reject(project_id, "dispatch_task")
    await _preflight_or_prep(resolved, project_id)
    # review + validate are read-only toward the repo: no verify gate, no PR,
    # no branch targets, and no speckit-install gate (they change nothing).
    read_only = kind in ("review_repository", "validate_product")
    if not read_only:
        # Feature work is gated on a merged speckit install: a repo whose
        # install PR is still open is not run (US2, no half-installed state).
        await _block_if_speckit_pending(resolved, "dispatch_task")
    # All mutating dispatch routes through the goal lane (spec 022 US1/US3).
    # Read-only kinds are byte-unaffected (FR-008).
    if not read_only:
        if not resolved.project_id:
            raise ToolError(
                f"project {project_id!r} resolved without a project_id — "
                "dispatch requires a registered project_id"
            )
        auto_filed: int | None = None
        if issue_ref is None:
            # FR-010: no issue_ref → auto-file intake issue and proceed keyed
            # to it (spec 022 US3). Every mutating ask names an issue.
            try:
                auto_filed = await _auto_file_intake(
                    _common.registry, project_id=resolved.project_id, goal=goal
                )
            except _intake.IntakeError as exc:
                raise ToolError(
                    f"prose-only dispatch: auto-filing intake issue failed — {exc}"
                ) from exc
            issue_ref = auto_filed
        try:
            result = await goals.dispatch_issue(
                project_id=resolved.project_id,
                workspace_dir=resolved.workspace_dir,
                repo_url=resolved.repo_url,
                issue_ref=issue_ref,
                kind=kind,
                objective=goal,
                verify_cmd=verify_cmd,
                open_pr=open_pr,
            )
        except ValueError as exc:
            raise ToolError(str(exc))
        if auto_filed is not None:
            result["auto_filed_issue"] = auto_filed
        return json.dumps(result, indent=2)
    # Read-only kinds: direct queue submit, unaffected by spec 022 (FR-008).
    task_id = queue.submit(
        kind=kind,
        workspace_dir=resolved.workspace_dir,
        goal=goal,
        notify_url=notify_url,
        verify_cmd=None,
        deliver=False,
        base_branch=None if kind == "validate_product" else base_branch,
        target_branch=None if kind == "validate_product" else target_branch,
        project_id=resolved.project_id,
    )
    return json.dumps({"task_id": task_id, "status": "pending"}, indent=2)


@mcp.tool
async def implement_feature(
    project_id: str,
    goal: str,
    issue_ref: Optional[int] = None,
    notify_url: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    open_pr: bool = False,
) -> str:
    """Dispatch feature work — the kind-specific companion verb, a thin
    forwarder to ``dispatch_task(kind="implement_feature")``. Supported, not
    deprecated: this is the shape the waiter agent drives the companion path
    with. Pass ``issue_ref`` (a GitHub issue number on the project's repo) to
    key the dispatch on that issue — the call is then idempotent: a duplicate
    dispatch for the same issue attaches to the existing work rather than
    starting a new one (spec 022 US1). Use ``dispatch_task`` directly when you
    need ``base_branch`` / ``target_branch``. See ``dispatch_task`` for full
    docs."""
    return await dispatch_task(
        kind="implement_feature",
        project_id=project_id,
        goal=goal,
        issue_ref=issue_ref,
        notify_url=notify_url,
        verify_cmd=verify_cmd,
        open_pr=open_pr,
    )


@mcp.tool
async def fix_bug(
    project_id: str,
    description: str,
    issue_ref: Optional[int] = None,
    notify_url: Optional[str] = None,
    verify_cmd: Optional[str] = None,
    open_pr: bool = False,
) -> str:
    """Dispatch a bug fix — the kind-specific companion verb, a thin forwarder
    to ``dispatch_task(kind="fix_bug")``. Supported, not deprecated: this is the
    shape the waiter agent drives the companion path with. Pass ``issue_ref`` (a
    GitHub issue number on the project's repo) to key the dispatch on that issue
    — the call is then idempotent: a duplicate dispatch for the same issue
    attaches to the existing work (spec 022 US1). Use ``dispatch_task`` directly
    when you need ``base_branch`` / ``target_branch``. See ``dispatch_task`` for
    full docs."""
    if not description:
        raise ToolError("fix_bug requires project_id and description")
    return await dispatch_task(
        kind="fix_bug",
        project_id=project_id,
        goal=description,
        issue_ref=issue_ref,
        notify_url=notify_url,
        verify_cmd=verify_cmd,
        open_pr=open_pr,
    )


@mcp.tool
async def review_repository(
    project_id: str, focus: str = "", notify_url: Optional[str] = None
) -> str:
    """Dispatch a read-only repository review — the kind-specific companion
    verb, a thin forwarder to ``dispatch_task(kind="review_repository")``.
    Supported, not deprecated: this is the shape the waiter agent drives the
    companion path with. See ``dispatch_task`` for full docs."""
    return await dispatch_task(
        kind="review_repository",
        project_id=project_id,
        goal=focus or "general code review",
        notify_url=notify_url,
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
