"""The done-gate — the worker's "done" is a proposal, gated on grounded review.

A settled advance session whose settle header reads done opens a read-only
``review_repository`` whose report the
direction evaluator judges (plus a grounded remote-CI cross-check); only an
``achieved`` verdict closes the goal, and a completed goal is best-effort
auto-deployed to a durable URL. Split out of :mod:`devclaw.goal.tick`; imports
tick_context (+ its shared _apply_corrections) and is called by
tick_settle._resolve_polling_done_gate and tick._handle_long_lived_advance. Re-exported
from tick.py.
"""

from __future__ import annotations

import sys

from dataclasses import replace
from pathlib import Path

from .tick_context import (
    AUTODEPLOY_ENABLED,
    NotifyLevel,
    Outcome,
    WorkspacePrep,
    _apply_corrections,
    _engine_kick,
    _notify,
    _run_atomic,
)
from . import evaluator as _evaluator
from . import remote_checks as _remote_checks
from . import slice_guard as _slice_guard
from . import delivery_strategy as _delivery
from .engine import GoalEngine
from .models import Action, Goal, GoalStatus
from .notify import Notifier
from ..llm_call import ClaudeCaller
from .store import GoalStore
from .transitions import Event
from ..delivery import deploy as _deploy
from ..engine.workspace import WorkspaceError
from ..loom import trace as _trace


#: Done-gate treadmill brake: consecutive done-proposal rounds the gate refused
#: to close before the goal parks for the owner (3 matches the checklist
#: circuit-breaker convention). Reset on achieved and on a human vouch
#: (steer_goal / resume_goal) — never on a productive settle: every treadmill
#: round settles productively.
DONEGATE_ROUND_CAP = 3

def _done_gate_review_brief(goal: "Goal") -> str:
    """The instruction the in-sandbox read-only reviewer gets when a settled
    advance session proposes done. The reviewer's report is then fed to the direction evaluator
    — both sides speak the same vocabulary: ``done_when`` is decomposed into
    atomic clauses, and each clause needs SPECIFIC repo evidence (file path +
    symbol + test name) to count as satisfied. This closes the
    `finance-sentry-mcp-readonly` failure mode (2026-06-25), where the reviewer
    produced a vague prose report, the evaluator stamped it `achieved`, and the
    delivered PR turned out to be 16 stub tools with zero real backend reads.

    Two axes — both load-bearing for the verdict:
      1. PER-CLAUSE EVIDENCE — does the code DO what the goal asked?
      2. STRUCTURAL HEALTH   — is the code Denys would HAND TO A NEW HIRE? (added
                               2026-06-29 after closeloop's App.tsx grew to 1827
                               LOC through 4 PRs that each satisfied the clauses
                               but left the codebase worse.)

    A goal is only ACHIEVED when BOTH axes pass. A green Per-clause section with
    a poor Structural section is OFF_TRACK — the agent must come back and clean
    up before the goal closes."""
    return (
        "Read-only review of this repository to verify whether the goal is "
        "fully satisfied. You produce the GROUNDED evidence the direction "
        "evaluator will judge against — be specific and honest, not generous. "
        "Two axes matter: did the code DO what was asked, AND is the codebase "
        "in a shape a senior engineer would hand to a new hire.\n\n"
        f"Objective: {goal.objective}\n"
        f"Done when: {goal.done_when}\n\n"
        "PROCEDURE — follow in order, do NOT skip:\n\n"
        "1. DECOMPOSE the 'Done when' text into atomic clauses (independent "
        "requirements joined by AND). Number them. Treat 'X with Y, including "
        "Z' as three clauses (X, Y, Z). An OR within a clause is a single "
        "clause with an alternative — pick the alternative the code actually "
        "shows.\n\n"
        "2. For EACH clause, search the repository for SPECIFIC evidence and "
        "report:\n"
        "   - clause: the exact clause text\n"
        "   - satisfied: yes | no | partial\n"
        "   - evidence: file path(s) + function/class/test name(s) that confirm "
        "satisfaction. 'src/Foo.cs handles it' is NOT evidence; "
        "'src/Foo.cs:42 GetAccountById queries _db.Accounts, covered by "
        "FooTests.GetAccountById_ReturnsAccount' IS evidence.\n"
        "   - if not satisfied or partial: name what is missing and where it "
        "should live (expected file path + symbol).\n\n"
        "3. Reject 'satisfied' based on weak signals — none of these count "
        "alone:\n"
        "   - Tool/symbol NAMES that match the clause (a tool called "
        "`get_accounts` that returns `{\"status\":\"not_yet_available\"}` does "
        "NOT satisfy 'expose accounts to the caller').\n"
        "   - Scaffolding without functionality (an empty contract test that "
        "asserts 'the registry has 16 entries' does NOT satisfy 'tools must "
        "read from real backend data').\n"
        "   - Tests that only assert the stub-like shape (these prove the stub, "
        "not the requirement).\n"
        "   - Test files that merely EXIST. For any clause about tests / E2E / "
        "coverage, RUN the suite (or cite the verify gate's actual run output) "
        "and report the result — a verify-script check that only asserts the "
        "spec file exists proves presence, not coverage, and does NOT satisfy "
        "a test clause.\n"
        "   - For any clause about a WEB UI working / rendering / being usable, "
        "cite a passing real-BROWSER run (the browser gate's Playwright report — "
        "executed count > 0, zero failures). A component that unit-tests green, "
        "builds, or renders in a Storybook story is NOT proof it works in the "
        "running app — a UI clause is satisfied only by a browser actually "
        "rendering and exercising it.\n"
        "   - A merged PR or a passing gate alone — those prove 'behaviour "
        "doesn't break', not 'the requirement is met'.\n\n"
        "4. STRUCTURAL HEALTH — grade the codebase as a senior engineer would. "
        "This is not a checklist of rules; it is professional judgement. "
        "Read the files this goal touched and the folders they live in, and "
        "answer honestly: would you sign off on a PR that left the codebase "
        "in this shape? Look for:\n"
        "   - God objects / files that have absorbed too many responsibilities "
        "(if everything lives in one file because the existing pattern is to "
        "pile everything into one file, the pattern is the smell — not an "
        "excuse).\n"
        "   - Coupled concerns that should be split (types mixed with "
        "implementation; UI mixed with networking; tests piling into a "
        "catch-all spec file because there's nowhere else for them).\n"
        "   - Dead code, no-op stubs, copy-paste, names that don't earn their "
        "length.\n"
        "   - Skipped tests where the comment doesn't match reality, or fixme "
        "markers used to hide work that should have been done.\n"
        "   - Untested behaviour the new code added — green gate is not the "
        "same as covered.\n"
        "   For each concern, name the file:line and what a senior engineer "
        "would do about it. A 'partial' is fine — overall health is a "
        "judgement call. If you would happily hand this codebase to a new "
        "hire on Monday, say so plainly.\n\n"
        "5. Output format — your final report MUST have this structure:\n\n"
        "   ## Per-clause evidence\n"
        "   1. <clause 1 text>\n"
        "      satisfied: yes | no | partial\n"
        "      evidence: <specific files/symbols/tests OR 'missing — should "
        "live in <path>:<symbol>'>\n"
        "   2. <clause 2 text>\n"
        "      satisfied: ...\n"
        "      evidence: ...\n"
        "   ...\n\n"
        "   ## Structural health\n"
        "   verdict: clean | concerns | poor\n"
        "   <one paragraph: would a senior engineer hand this codebase to a "
        "new hire? if not, why not, and which file:lines would they fix first?>\n"
        "   <bullet list of named concerns — file:line + the senior-eng move>\n\n"
        "   ## Summary\n"
        "   <2-3 sentences covering BOTH axes: are all clauses satisfied, AND "
        "is the structural health acceptable? if either is no, the goal is "
        "NOT done — name what the agent must come back and fix>\n\n"
        "   ## Risks not in done_when\n"
        "   <anything worth raising that isn't part of done_when>\n\n"
        "Be honest. You are graded on producing a report that matches reality, "
        "not on appearing thorough. Understating either axis means the goal "
        "will close on work that satisfies the literal clauses but leaves "
        "the codebase worse than it was — which is the failure mode we are "
        "explicitly trying to prevent."
    )


def _feature_spec_grounding(goal: "Goal", store: GoalStore, goal_id: str) -> str:
    """The contract the done-gate judges against (spec 008 US1, FR-006 / D6).

    Grounds on the executing feature's ``spec.md`` — the speckit spec is the
    contract of record, so the gate judges against its Success Criteria +
    Requirements. The feature dir is resolved as: the one RECORDED at dispatch,
    else — the common brand-new-feature case, where the dir did not exist
    pre-session — the feature the increment actually landed in, derived live from
    the post-session working tree (:func:`slice_guard.current_feature_dir_sync`,
    the most-recently-touched ``specs/NNN``). Only a repo with NO ``specs/`` at
    all falls back to the goal's scope spec (:meth:`GoalStore.read_spec`), which
    itself degrades to the ``done_when`` text — the grounding SOURCE changed, the
    fail-closed gate did not (``done_when`` is always evaluated by ``build_prompt``).

    Best-effort and never raises: a git/fs hiccup degrades to the existing
    fallback rather than failing the gate. Module-global so tests patch it here."""
    fallback = store.read_spec(goal_id)
    if not goal.workspace_dir:
        return fallback
    try:
        rel = store.read_executing_feature(goal_id).strip()
    except Exception:  # noqa: BLE001 — grounding is best-effort
        rel = ""
    if not rel:
        # No dispatch-time record (the feature was authored in-session, so the dir
        # did not exist to record pre-session). Ground on the feature the
        # increment actually landed in.
        try:
            rel = _slice_guard.current_feature_dir_sync(goal.workspace_dir)
        except Exception:  # noqa: BLE001 — derivation is best-effort
            rel = ""
    if not rel:
        return fallback
    try:
        spec_path = Path(goal.workspace_dir) / rel / "spec.md"
        if spec_path.is_file():
            return spec_path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — a workspace hiccup falls back, never fails
        return fallback
    return fallback


def _project_owns_its_deploy(workspace_dir: str) -> bool:
    """The target project owns its deploy when its repo contains a ``Dockerfile``
    at the workspace root. In that case devclaw MUST NOT spin its own throwaway
    ``devclaw-deploy-<goal_id>`` container — the project's own CI is the single
    source of deploy truth, and one goal-branch merge triggers one deploy from
    the project's singleton container. Devclaw's old container-per-goal shape
    is the wrong ownership boundary; the Dockerfile-presence check is the
    migration seam. How each project's Dockerfile + CI workflow gets authored
    is engineering-judgment work an ``implement_feature`` task does per-repo
    (per ``plan.md`` §Production-ready C5); devclaw no longer ships a template
    scaffolder — the earlier ``setup_cicd`` MCP tool encoded product taste in
    the harness (5 hardcoded stack templates, silently wrong for fullstack)
    and was removed."""
    try:
        return (Path(workspace_dir) / "Dockerfile").exists()
    except Exception:  # noqa: BLE001 — workspace missing = fall through to the old behavior
        return False


async def _auto_deploy(
    goal_id: str, goal: Goal, store: GoalStore, *, enabled: "bool | None"
) -> str:
    """Deploy the built app to a durable Tailscale URL on goal completion and return
    a short suffix to append to the completion notice (the live URL, or empty). Fully
    best-effort: any failure is logged and swallowed — a verified-complete goal must
    never be reopened because hosting wobbled.

    ``enabled`` is resolved upstream (a project's explicit ``autodeploy``
    override, else the devclaw-wide default — see GoalService._autodeploy).
    Three-way: ``True``/``False`` are pinned decisions honored as-is; ``None``
    (nothing pinned anywhere — the fleet default) is CONDITIONAL — deploy only
    if the workspace has an app surface the preview launcher can actually serve
    (:func:`devclaw.delivery.deploy.workspace_has_app_surface`). A pure library
    gets no preview container (#554); an explicit ``True`` still forces one.

    Skipped when the target project owns its own deploy (see
    :func:`_project_owns_its_deploy`) — devclaw does not run a per-goal container
    for a project that already has a Dockerfile + CI deploy job of its own.
    """
    if enabled is False:
        return ""
    if _project_owns_its_deploy(goal.workspace_dir):
        store.append_log(
            goal_id,
            "auto-deploy skipped: project owns its deploy (Dockerfile present in workspace)",
        )
        return ""
    if enabled is None and not _deploy.workspace_has_app_surface(goal.workspace_dir):
        store.append_log(
            goal_id,
            "auto-deploy skipped: no app surface detected (library repo) — "
            "set the project's autodeploy=on to force a preview deploy",
        )
        return ""
    try:
        out = await _deploy.deploy_project(goal.workspace_dir, goal_id)
    except Exception as exc:  # noqa: BLE001 — handoff is a bonus; completion is not contingent on it
        store.append_log(goal_id, f"auto-deploy skipped: {exc}")
        return ""
    url = out.get("url") or out.get("loopback_url", "")
    store.append_log(goal_id, f"deployed: {url or out.get('container')} (ready={out.get('ready')})")
    if out.get("tailscale_served") and out.get("url"):
        return f"\n🔗 live: {out['url']}"
    # Tailscale not wired from here — hand back the one-time serve command.
    return f"\n🔗 deployed on the VPS — expose once: {out.get('serve_command', '')}"


async def _resolve_done_gate(
    goal_id: str, goal: Goal, status: GoalStatus, review_report: str,
    *, store: GoalStore, evaluator_caller: ClaudeCaller, notifier: Notifier,
    summarize: "ClaudeCaller | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    consume_steering: "list[int] | None" = None,
) -> Outcome:
    """A done-gate review just finished — judge the repo against done_when. Only
    'achieved' closes the goal; otherwise corrections are steered back in and the
    goal continues (its next tick plans the next step).

    ``consume_steering`` (PR5): row ids the TICK's own post-plan call already
    read this turn — rides whichever of the three verdict transitions below
    fires, so consumption lands atomically with the decision. The two
    polling-resolver call sites (``_resolve_polling_done_gate``, settling a
    done-check dispatched on a PRIOR tick) pass nothing — their steering
    wasn't read this turn, so there is nothing of theirs to consume.

    An ``achieved`` verdict additionally has to survive the grounded
    remote-checks verification (when a checker is bound and the goal works on
    a shared goal branch): the branch's REAL CI state is queried and a
    failing / never-ran / still-running check surface converts the verdict to
    ``off_track`` with a steering correction. The 2026-07-06 benchmark closed
    a goal whose 32 GitHub Actions runs had all failed at startup — the
    sandbox gate was green and nothing ever looked at the repo's actual
    checks. ``unknown`` / ``no_workflows`` do NOT block (fail-open on infra
    uncertainty, fail-closed on evidence of a problem) but are logged."""
    # Ground the evaluator in the goal's ACTUAL workspace (triage F3, the
    # evaluator sibling of #227): on the verify_done=False fallthrough
    # review_report is empty and the prompt otherwise carries ZERO first-hand
    # repo facts. Best-effort and collected OUTSIDE the try — it never raises,
    # so a git hiccup can't read as an eval error. No zero-token concern: this
    # path already runs cognition; the git subprocess adds no LLM call.
    repo_context = await _evaluator._repo_context(goal.workspace_dir)
    # Ground the gate on the executing feature's speckit spec (FR-006 / D6),
    # falling back to the goal's scope spec / done_when when none is recorded.
    spec = _feature_spec_grounding(goal, store, goal_id)
    try:
        ev = await _evaluator.evaluate(
            goal, status, store.recent_log(goal_id), store.recent_deliveries(goal_id),
            claude_caller=evaluator_caller, review_report=review_report, at_done_gate=True,
            spec=spec, repo_context=repo_context,
        )
    except _evaluator.GoalEvalError as exc:
        store.append_log(goal_id, f"done-gate eval error: {exc}")
        store.update_status_fields(goal_id, last_tick_at=store.now_iso())
        await _notify(notifier, NotifyLevel.TASK, f"⚠️ [{goal_id}] done-gate eval failed: {exc}")
        return Outcome.ERROR
    if ev.verdict == "achieved" and remote_checker is not None and goal.repo_url:
        # Only goal-branch goals accumulate work on a shared branch whose
        # check surface is meaningful at close time; per-action PRs
        # were already merged (or reviewed) one by one.
        branch = _delivery.resolve_strategy(store, goal_id).goal_branch(goal_id)
        if branch is not None:
            try:
                rc = await remote_checker(goal.repo_url, branch)
            except Exception as exc:  # noqa: BLE001 — checker trouble must not wedge the gate
                rc = _remote_checks.RemoteChecksResult(
                    "unknown", f"{exc.__class__.__name__}: {exc}",
                )
            store.append_log(
                goal_id, f"done-gate remote checks ({branch}): {rc.state} — {rc.detail[:200]}",
            )
            if rc.blocks_done(_remote_checks.CI_GATE_MODE):
                correction = {
                    "failing": (
                        f"the target repo's REAL CI for {branch} is failing "
                        f"({rc.detail}). The sandbox gate is not CI — fix the "
                        f"workflows/runs until the branch's checks are green, "
                        f"then re-propose done."
                    ),
                    "none": (
                        f"the target repo has workflows but CI produced ZERO "
                        f"runs for {branch}'s head commit ({rc.detail}). Find "
                        f"out why Actions never ran (triggers, permissions, "
                        f"billing) and get a green run before re-proposing done."
                    ),
                    "infra_broken": (
                        f"every CI run for {branch} died at startup "
                        f"({rc.detail}) — Actions never executed a step "
                        f"(permissions/billing). Fix the repo's CI "
                        f"infrastructure, then re-propose done."
                    ),
                    "pending": (
                        f"remote checks for {branch} are still running "
                        f"({rc.detail}). Let them settle green, then re-propose "
                        f"done."
                    ),
                }[rc.state]
                ev = replace(
                    ev, verdict="off_track",
                    rationale=(
                        f"all done_when clauses pass but the branch's real CI "
                        f"contradicts the close: remote checks are {rc.state}."
                    ),
                    corrections=[f"[remote-checks] {correction}"],
                )
            elif rc.state in ("infra_broken", "none"):
                # Flexible ci-gate: broken CI infrastructure must not wedge a
                # verified goal, but the close must never masquerade as
                # CI-green — annotate the verdict the owner will read.
                ev = replace(
                    ev, rationale=(
                        f"{ev.rationale} [ci-gate flexible: remote CI is "
                        f"{rc.state} ({rc.detail[:120]}) — close honored on the "
                        f"internal verify gate only]"
                    ),
                )
    now = store.now_iso()
    base = replace(
        status, last_eval_verdict=ev.verdict, last_eval_at=now,
        last_eval_note=ev.rationale, last_tick_at=now,
    )
    store.append_log(goal_id, f"done-gate: {ev.verdict} — {ev.rationale[:500]}")
    if ev.verdict == "achieved":
        store.transition(
            goal_id, Event.ACHIEVE,
            replace(base, phase="done", next=ev.rationale[:200], donegate_rounds=0),
            expect=status, consume_steering=consume_steering,
        )
        if ev.structural_concerns:
            # Trust-dial close: the structural axis advises-and-ships (ADR
            # 0007) — loud in the goal log + the owner ping, never a silent
            # drop; the human PR review is the backstop.
            store.append_log(
                goal_id,
                "advisory follow-ups shipped with the close (structural axis, "
                "not gating): " + "; ".join(ev.structural_concerns),
            )
        # Close-out artifact: RUN_SUMMARY.md, a projection of the goal's own
        # rows (delivery traces + cognition totals + phase history)
        # rendered once, AFTER the ACHIEVE transition committed — a rolled-back
        # close can't leave a summary behind. Best-effort end to end: a summary
        # hiccup logs and the verified close proceeds untouched (same
        # never-undo-a-close stance as the deploy below). Zero LLM calls.
        summary_line = ""
        try:
            from . import run_summary as _run_summary

            md, summary_line = _run_summary.build_run_summary(
                goal_id,
                base,
                store.read_goal_traces(goal_id, kind="delivery"),
                totals=store.goal_trace_totals(goal_id),
                objective=goal.objective,
            )
            store.write_run_summary_view(goal_id, md)
            store.append_log(goal_id, "run summary written → RUN_SUMMARY.md")
        except Exception as exc:  # noqa: BLE001 — observability, not correctness
            summary_line = ""
            sys.stderr.write(f"goal-tick: run-summary render failed [{goal_id}]: {exc}\n")
        # Handoff: a completed goal should be a thing the owner can OPEN, not just a
        # closed ticket. Best-effort deploy the built app to a durable Tailscale URL.
        # NEVER let a deploy hiccup undo a verified-complete goal — the goal IS done.
        live = await _auto_deploy(goal_id, goal, store, enabled=autodeploy)
        # Honest labeling (F3): "(verified)" is earned by a repo review that
        # actually grounded the decision. On the verify_done=False fallthrough
        # no review ran — same close, annotated honestly (cf. the ci-gate
        # flexible annotation above); which verdicts close is unchanged.
        label = (
            "goal complete (verified)" if review_report.strip()
            else "goal complete (artifact-only close — no repo review ran; "
                 "verify_done is off for this project)"
        )
        summary_suffix = f"\n{summary_line}" if summary_line else ""
        followups = (
            f" — {len(ev.structural_concerns)} advisory follow-up(s) in the goal log"
            if ev.structural_concerns else ""
        )
        await _notify(notifier, NotifyLevel.OWNER, f"✅ [{goal_id}] {label} — {ev.rationale[:200]}{followups}{live}{summary_suffix}", summarize=summarize)
        return Outcome.DONE
    if ev.verdict in ("stalled", "needs_human"):
        q = ev.question or ev.rationale or "done-gate flagged a problem"
        store.transition(
            goal_id, Event.BLOCK,
            replace(base, phase="blocked", blocked_on=q, blocked_kind="needs_answer", next=""),
            expect=status, consume_steering=consume_steering,
        )
        await _notify(notifier, NotifyLevel.OWNER, f"🟡 [{goal_id}] not done — {q}", summarize=summarize)
        return Outcome.BLOCKED
    # on_track / off_track → not done yet. Count the round: a gate that
    # refuses to close the same goal DONEGATE_ROUND_CAP times in a row is a
    # treadmill (fresh nits per round, each round burning a worker + a review
    # + an eval), not convergence — park it for the owner with the FULL last
    # verdict instead of re-advancing forever. Below the cap, steer
    # corrections back in and continue.
    rounds = status.donegate_rounds + 1
    if rounds >= DONEGATE_ROUND_CAP:
        q = (
            f"done-gate churn brake: {rounds} consecutive done proposals did not "
            f"close the goal. Latest verdict: {ev.rationale[:1000]} — review the "
            f"open PR yourself, then resume (re-judges the same contract), steer, "
            f"or cancel."
        )
        store.transition(
            goal_id, Event.BLOCK,
            replace(base, phase="blocked", blocked_on=q, blocked_kind="donegate_churn",
                    donegate_rounds=rounds, next=""),
            expect=status, consume_steering=consume_steering,
        )
        _apply_corrections(store, goal_id, ev)  # visible in inbox for the owner's decision
        await _notify(notifier, NotifyLevel.OWNER, f"🟡 [{goal_id}] {q[:400]}", summarize=summarize)
        return Outcome.BLOCKED
    store.transition(
        goal_id, Event.RESUME_IDLE,
        replace(base, phase="idle", next="done-gate said keep going",
                donegate_rounds=rounds),
        expect=status, consume_steering=consume_steering,
    )
    _apply_corrections(store, goal_id, ev)
    await _notify(notifier, NotifyLevel.TASK, f"↩️ [{goal_id}] done-gate: not complete — {ev.rationale[:200]}")
    return Outcome.SLEPT


async def _open_done_gate(
    goal_id: str, goal: Goal, base: GoalStatus,
    *, store: GoalStore, engine: GoalEngine, evaluator_caller: ClaudeCaller,
    notifier: Notifier, notify_url: str, prepare_ws: WorkspacePrep, verify_done: bool,
    note: str, summarize: "ClaudeCaller | None" = None,
    remote_checker: "_remote_checks.RemoteChecker | None" = None,
    autodeploy: "bool | None" = AUTODEPLOY_ENABLED,
    consume_steering: "list[int] | None" = None,
) -> Outcome:
    """A settled advance session proposed done. Don't trust it: either dispatch a read-only
    review of the repo against done_when (the grounded path) and let the next
    tick judge it, or — if done-verification is disabled — run an artifact-only
    done evaluation now.

    ``consume_steering`` (PR5): the row ids the tick's post-plan call read
    this turn — rides EVERY transition below (the two retry-RESUME_IDLEs and
    the VERIFYING open), and is forwarded into the ``verify_done=False``
    fallthrough to :func:`_resolve_done_gate` too, so consumption is atomic
    with whichever decision actually lands, no matter which branch fires."""
    if verify_done:
        # In checklist mode the done-gate reviewer needs to see the goal's
        # accumulated work — read the goal branch, not the default branch
        # (otherwise it judges done_when against an empty diff).
        done_gate_branch = _delivery.resolve_strategy(store, goal_id).goal_branch(goal_id)
        try:
            await prepare_ws(goal.workspace_dir, goal.repo_url, done_gate_branch)
        except WorkspaceError as exc:
            store.append_log(goal_id, f"done-gate workspace prep failed: {exc}")
            store.transition(
                goal_id, Event.RESUME_IDLE,
                replace(base, phase="idle", next="retry done-gate"),
                expect=base, consume_steering=consume_steering,
            )
            await _notify(notifier, NotifyLevel.TASK, f"⚠️ [{goal_id}] done-gate workspace prep failed: {exc}")
            return Outcome.ERROR
        review = Action(
            engine="devclaw", tool="review_repository",
            goal=_done_gate_review_brief(goal),
            open_pr=False,
        )
        # Atomic dispatch (PR7) — same shape as _dispatch_action's; see that
        # function's comment for the dispatch_exc/txn-nesting rationale.
        dispatch_exc: "Exception | None" = None
        try:
            with store.transaction():
                try:
                    ref = _run_atomic(engine.dispatch(review, goal, notify_url))
                except Exception as exc:  # noqa: BLE001
                    dispatch_exc = exc
                    raise
                ref = replace(ref, is_done_check=True)
                store.transition(
                    goal_id, Event.OPEN_DONE_GATE,
                    replace(base, phase="verifying", in_flight=ref, next="verifying done"),
                    expect=base, consume_steering=consume_steering,
                )
                store.append_log(goal_id, f"done proposed ({note}) → verifying via review {ref.id}", mirror=False)
        except Exception:
            store.discard_pending_mirrors(goal_id)
            if dispatch_exc is None:
                raise
            store.append_log(goal_id, f"done-gate dispatch failed: {dispatch_exc}")
            store.transition(
                goal_id, Event.RESUME_IDLE,
                replace(base, phase="idle", next="retry done-gate"),
                expect=base, consume_steering=consume_steering,
            )
            await _notify(notifier, NotifyLevel.TASK, f"⚠️ [{goal_id}] done-gate dispatch failed: {dispatch_exc}")
            return Outcome.ERROR
        store.render_mirrors(goal_id)
        _engine_kick(engine)
        _trace.record_dispatch(goal_id=goal_id, tool=review.tool, ref_id=ref.id, engine=getattr(engine, "kind", ""), is_done_check=True)
        await _notify(notifier, NotifyLevel.TASK, f"🔎 [{goal_id}] looks complete — verifying against done_when")
        return Outcome.VERIFYING
    # verify disabled → artifact-only done evaluation now.
    return await _resolve_done_gate(
        goal_id, goal, base, review_report="",  # no review run; artifact-only
        store=store, evaluator_caller=evaluator_caller, notifier=notifier,
        summarize=summarize, remote_checker=remote_checker, autodeploy=autodeploy,
        consume_steering=consume_steering,
    )
