"""The single intake doorway + backlog grading + repo onboarding.

Stage 1 of every ask (``file_intake`` -> a labeled GitHub issue), the
fail-closed readiness grades, and ``onboard`` (adopt-or-install speckit, then
the comprehension-doc pass).
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from fastmcp.exceptions import ToolError

from ... import intake as _intake
from ... import speckit_setup as _speckit
from ...state_store import _now_ms
from .._state import mcp, queue, registry
from ._common import _preflight_or_prep, _resolve_project_or_reject


@mcp.tool
async def file_intake(
    project_id: str,
    what: str,
    done_when: str,
    asker: str,
    channel: Literal["chat", "telegram", "a2a", "other"],
    context: Optional[str] = None,
    expected_increments: Optional[int] = None,
    increment_basis: Optional[str] = None,
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
    - ``expected_increments`` — the filer's claim of how many units of work
      (one atomic, verified, PR-able change-set each) the ask takes. Recorded
      verbatim and never re-derived; grading validates it and never overwrites
      it. Omit ONLY when you genuinely cannot estimate — omission is recorded
      as ``unstated`` and surfaced for a human, never defaulted to a number.
      The count sizes the plan; it never selects an execution shape (every work
      item runs as a saga).
    - ``increment_basis`` — why that count, or why no count could be given.
      REQUIRED whenever ``expected_increments`` is given: a number with no
      stated basis cannot be argued with.

    Returns ``{issue_url, project_id, repo, expected_increments}``. A filing
    failure raises with an actionable message — there is no receipt unless the
    issue really exists."""
    try:
        result = await _intake.file_intake(
            registry,
            project_id=project_id,
            what=what,
            done_when=done_when,
            asker=asker,
            channel=channel,
            context=context,
            expected_increments=expected_increments,
            increment_basis=increment_basis,
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
            from ...intake_readiness import default_caller

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
    element named. Re-reads the issue on demand; with webhooks configured (spec 023,
    `DEVCLAW_WEBHOOK_SECRET`) issue opened/edited events trigger this same
    grade automatically. Grading is admission to *grading*, never execution: it does not
    dispatch, does not edit the issue, and does not alter provenance.

    - ``project_id`` — the registered project the issue lives on.
    - ``issue_url`` — the issue's URL (must be open; closed issues reject).

    Grading also validates the filer's expected-increment claim on a SECOND,
    independent axis: the claim is read back from the issue body and never
    rewritten, and ``needs-sizing`` lands when a human must decide the extent
    (no claim, an unestimable claim, an unassessable ask, or a disagreement).
    A sizing dispute never moves the readiness verdict, and the count never
    selects an execution shape — every work item runs as a saga.

    Returns ``{issue_url, project_id, repo, readiness, expected_increments,
    increment_basis, assessed_increments, sizing, sizing_reason}``. The grade
    fails CLOSED: any failure to reach a confident ready verdict lands
    ``needs-refinement``, never ``devclaw-ready``; any failure to reach a
    confident agreement lands ``needs-sizing``."""
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
    plus the cross-cutting ``needs_sizing`` list (issues whose extent needs a
    human decision — they also appear in their readiness bucket), ``cap`` and
    the ``listing_limit`` page bound. A listing failure raises loudly — an
    explicit call never silently degrades to an empty sweep."""
    try:
        result = await _intake.grade_backlog(registry, project_id=project_id)
    except _intake.IntakeError as exc:
        raise ToolError(str(exc)) from exc
    return json.dumps(result, indent=2)


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

    # Adopted repo whose devclaw.json is absent or mechanically behind ⇒ the
    # seed/migrate PR first (spec 016 US3: doctor detects, re-onboard migrates,
    # the human merges). Mechanical fields only; human-set fields preserved.
    if _speckit.manifest_needs_upkeep(resolved.workspace_dir):
        result = await _speckit.migrate_manifest_pr(
            resolved.workspace_dir, project_id=resolved.project_id
        )
        return json.dumps(
            {
                "manifest": "migrate_pr",
                "pr_url": result.get("pr_url"),
                "branch": result.get("branch"),
                "changed": result.get("changed", []),
                "error": result.get("error"),
                "note": (
                    "devclaw.json was absent or behind — opened a reviewable "
                    "seed/migrate PR. Merge it, then re-run onboard for the "
                    "comprehension-doc pass."
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
