"""Single intake doorway — Stage 1: ``file_intake`` (the intent half).

Proposal: ``docs/proposals/single-intake-doorway.md`` (LOCKED 2026-08-13).

Every ask from every source — human or agent — enters devclaw here: the shape
is validated synchronously, provenance is stamped server-side, and the ask is
filed as a labeled GitHub issue on the target *registered project*'s repo. The
returned issue URL is the asker's durable receipt. This half of the doorway can
ONLY create issues; execution admission (stage 2) stays with the dispatch tools
and the authorized dispatcher.

Design mirrors ``goal/self_issue.py``: pure functions over primitives for every
decision (unit-testable with no network), the GitHub calls behind an injectable
``gh`` adapter, and the same ``gh``-subprocess boundary as ``delivery/repo.py``
(a ``GITHUB_TOKEN`` credential — never ``ANTHROPIC_*``; zero LLM anywhere).
Unlike the cycle-edge filers, this is a synchronous user-facing tool: a filing
failure raises loudly with an actionable message — a receipt is real or the
call fails; there is no silent half-filed state.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from typing import Optional, Protocol
from .goal.issue_ref import CONTRACT_HEADING
from .procutil import run as _run

#: the intake marker label every filed ask carries (proposal §5).
INTAKE_LABEL = "devclaw-intake"

#: readiness source-of-truth labels (spec 006, FR-007). Exactly one is present
#: on a graded issue; while the async grade is pending, neither is (the ask is
#: treated as not-ready until the label lands). These labels ARE the durable
#: truth — generated human views (comments) mirror them, never the reverse.
READY_LABEL = "devclaw-ready"
NEEDS_REFINEMENT_LABEL = "needs-refinement"
_READINESS_LABELS = (READY_LABEL, NEEDS_REFINEMENT_LABEL)

#: spec 012 US3 (FR-011): the SECOND, orthogonal axis. Present when a human must
#: decide the work item's extent — the filer stated no count, the filer could not
#: estimate, grading could not assess, or grading disagrees with the claim. It
#: never blocks readiness and readiness never implies it; the two labels answer
#: two different questions.
NEEDS_SIZING_LABEL = "needs-sizing"

#: recorded intake channels. ``other`` is the explicit escape hatch so a new
#: surface never has to lie about its channel to get through validation.
CHANNELS = ("chat", "telegram", "a2a", "other")

#: minimum ``done_when`` length — same bar as goal admission's vague-done_when
#: gate: an unverifiable one-worder is rejected at the doorway, not discovered
#: at dispatch.
MIN_DONE_WHEN_CHARS = 20

#: spec 012 FR-010: a count is only useful if it can be argued with, so a claimed
#: count must arrive with a basis. Same shape as the ``done_when`` bar — enforced
#: at the doorway, not discovered at grading.
MIN_INCREMENT_BASIS_CHARS = 10

#: rendered in the issue body when the filer stated no count. NEVER a number:
#: an unknown extent is surfaced for a human, never defaulted (FR-011).
UNSTATED_INCREMENTS = "unstated"

#: spec 009: one bulk-grade invocation spends at most this many cognition calls
#: (clarify ruling 2026-08-18 — quota spend stays in operator-triggered chunks;
#: the remainder is reported and continued only by an explicit re-invocation).
BULK_GRADE_CAP = 20

#: the ``gh issue list`` page bound — stated in the bulk report, never silent.
LISTING_LIMIT = 200

_TITLE_MAX = 240


class IntakeError(ValueError):
    """A rejected ask — invalid shape, unknown project, or a filing failure.
    Always carries an actionable message; the tool layer maps it to ToolError."""


# ---- pure decisions (no DB, no clock, no network) ---------------------------

def repo_slug(repo_url: Optional[str]) -> Optional[str]:
    """``owner/name`` from a registry row's ``repo_url`` (https or ssh, with or
    without ``.git``). None when the URL is absent or not GitHub-shaped."""
    url = (repo_url or "").strip().rstrip("/")
    if not url:
        return None
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None
    return f"{m.group(1)}/{m.group(2)}"


def validate_shape(
    *,
    what: str,
    done_when: str,
    asker: str,
    channel: str,
    expected_increments: Optional[int] = None,
    increment_basis: Optional[str] = None,
) -> None:
    """Synchronous shape validation (proposal §5). Raises ``IntakeError`` with
    every problem named at once — the asker fixes one round trip, not N.

    The expected-increment claim (spec 012 FR-010) is deliberately OPTIONAL: an
    extent the filer cannot estimate must be surfaced, not rejected, and spec
    009's universal adoption of hand-written issues has no claim by
    construction. What is NOT optional is arguability — a count without a basis
    is a number nobody can dispute, so it is rejected here."""
    problems: list[str] = []
    if not (what or "").strip():
        problems.append("'what' is required: one paragraph describing the ask")
    if len((done_when or "").strip()) < MIN_DONE_WHEN_CHARS:
        problems.append(
            "'done_when' must be verifiable completion criteria "
            f"(≥ {MIN_DONE_WHEN_CHARS} chars)"
        )
    if not (asker or "").strip():
        problems.append("'asker' is required: who is asking (e.g. denys, ledger)")
    if channel not in CHANNELS:
        problems.append(f"'channel' must be one of {'/'.join(CHANNELS)}")
    if expected_increments is not None:
        if isinstance(expected_increments, bool) or not isinstance(
            expected_increments, int
        ):
            problems.append(
                "'expected_increments' must be a whole number of units of work"
            )
        elif expected_increments < 1:
            problems.append(
                "'expected_increments' must be at least 1 — an ask that takes no "
                "unit of work is not an ask"
            )
        if len((increment_basis or "").strip()) < MIN_INCREMENT_BASIS_CHARS:
            problems.append(
                "'increment_basis' is required whenever 'expected_increments' is "
                f"given (≥ {MIN_INCREMENT_BASIS_CHARS} chars): a count with no "
                "stated basis cannot be argued with"
            )
    if problems:
        raise IntakeError("intake rejected: " + "; ".join(problems))


def issue_title(what: str) -> str:
    """First line of the ask, marked as intake."""
    head = (what or "").strip().splitlines()[0].strip()
    return f"[intake] {head}"[:_TITLE_MAX]


def increments_section(
    expected_increments: Optional[int], increment_basis: Optional[str]
) -> str:
    """The filer's expected-increment claim as it is written into the issue body
    — the DURABLE record (spec 012 FR-010). Written once at filing and never
    rewritten by grading (FR-010b); grading reads it back verbatim, which is why
    two grades of an unchanged work item record the same count (SC-005b).

    No count ⇒ ``unstated``, never a defaulted number (FR-011)."""
    basis = (increment_basis or "").strip() or "—"
    claimed = (
        UNSTATED_INCREMENTS if expected_increments is None else str(expected_increments)
    )
    return (
        "## Expected increments\n\n"
        f"- **Claimed by the filer:** {claimed}\n"
        f"- **Basis:** {basis}\n"
    )


def issue_body(
    *,
    what: str,
    done_when: str,
    context: Optional[str],
    asker: str,
    channel: str,
    project_id: str,
    slug: str,
    filed_ms: int,
    expected_increments: Optional[int] = None,
    increment_basis: Optional[str] = None,
) -> str:
    """The rendered intake record — the ONE place the shape becomes an issue
    (no per-repo templates by design, §4-O4). Provenance is stamped here,
    server-side; the asker line is a recorded claim, not authentication."""
    filed_at = datetime.fromtimestamp(filed_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    ctx = (context or "").strip() or "—"
    return (
        "> Filed via devclaw `file_intake` (single-intake-doorway, stage 1 — intent).\n"
        "> Dispatch is a separate human-gated step; this issue is the durable record\n"
        "> of the ask, and its URL is the asker's receipt.\n\n"
        f"## What\n\n{what.strip()}\n\n"
        f"## {CONTRACT_HEADING}\n\n{done_when.strip()}\n\n"
        f"## Context\n\n{ctx}\n\n"
        + increments_section(expected_increments, increment_basis)
        + "\n"
        + "## Provenance\n\n"
        f"- **Asker (recorded, not authenticated):** {asker.strip()}\n"
        f"- **Channel:** {channel}\n"
        f"- **Filed at:** {filed_at}\n"
        f"- **Project:** `{project_id}` → `{slug}`\n"
    )


# ---- the injectable GitHub adapter (tests pass a fake) ----------------------

class GhAdapter(Protocol):
    async def ensure_label(self, repo: str, name: str) -> None: ...
    async def create_issue(
        self, repo: str, *, title: str, body: str, labels: list[str]
    ) -> Optional[str]: ...
    async def add_labels(self, repo: str, issue: str, labels: list[str]) -> None: ...
    async def remove_labels(self, repo: str, issue: str, labels: list[str]) -> None: ...
    async def comment(self, repo: str, issue: str, body: str) -> None: ...
    async def view_issue(self, repo: str, issue: str) -> Optional[dict]: ...
    async def list_intake_awaiting_grade(self, repo: str) -> list[str]: ...
    async def list_open_issues(self, repo: str) -> Optional[list[dict]]: ...



class GhCli:
    """Real adapter: shells ``gh`` service-side. ``create_issue`` returns the
    new issue's URL (what ``gh issue create`` prints) — the receipt itself."""

    async def ensure_label(self, repo: str, name: str) -> None:
        # --force makes it idempotent (created on first use, updated after).
        await _run("gh", "label", "create", name, "--repo", repo, "--force")

    async def create_issue(
        self, repo: str, *, title: str, body: str, labels: list[str]
    ) -> Optional[str]:
        args = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
        for lbl in labels:
            args += ["--label", lbl]
        rc, out = await _run(*args)
        if rc != 0:
            sys.stderr.write(f"file_intake: create failed on {repo}: {out}\n")
            return None
        url = out.strip().splitlines()[-1].strip() if out.strip() else ""
        return url if url.startswith("http") else None

    async def add_labels(self, repo: str, issue: str, labels: list[str]) -> None:
        args = ["gh", "issue", "edit", issue, "--repo", repo]
        for lbl in labels:
            args += ["--add-label", lbl]
        await _run(*args)

    async def remove_labels(self, repo: str, issue: str, labels: list[str]) -> None:
        args = ["gh", "issue", "edit", issue, "--repo", repo]
        for lbl in labels:
            args += ["--remove-label", lbl]
        await _run(*args)

    async def comment(self, repo: str, issue: str, body: str) -> None:
        await _run("gh", "issue", "comment", issue, "--repo", repo, "--body", body)

    async def view_issue(self, repo: str, issue: str) -> Optional[dict]:
        """One read for everything the re-grade needs — ``{title, body, state}``
        (the single-issue verb rejects non-OPEN targets before any cognition)."""
        rc, out = await _run(
            "gh", "issue", "view", issue, "--repo", repo,
            "--json", "title,body,state",
        )
        if rc != 0 or not out.strip():
            return None
        try:
            data = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    async def list_intake_awaiting_grade(self, repo: str) -> list[str]:
        """Open intake issues that carry the intake label but NEITHER readiness
        label — the pending-grade set derived from GitHub itself (the label is
        the source of truth, FR-007). gh has no NOT-label filter, so list the
        intake issues and drop the already-graded ones here. Never raises — a gh
        hiccup degrades to ``[]`` (the sweep skips this repo, not the process)."""
        rc, out = await _run(
            "gh", "issue", "list", "--repo", repo, "--label", INTAKE_LABEL,
            "--state", "open", "--limit", "200", "--json", "url,labels",
        )
        if rc != 0 or not out.strip():
            return []
        try:
            items = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return []
        pending: list[str] = []
        for it in items if isinstance(items, list) else []:
            names = {(l or {}).get("name") for l in (it.get("labels") or [])}
            if names.isdisjoint(_READINESS_LABELS):
                url = it.get("url")
                if url:
                    pending.append(url)
        return pending

    async def list_open_issues(self, repo: str) -> Optional[list[dict]]:
        """All open issues (any label, any format — PRs excluded by ``gh issue
        list`` itself), for the bulk-grade partition (spec 009). Unlike the
        recovery lister above, a ``gh`` failure returns ``None`` so the bulk
        verb can reject LOUDLY — an explicit operator action must never
        silently degrade to an empty sweep."""
        rc, out = await _run(
            "gh", "issue", "list", "--repo", repo, "--state", "open",
            "--limit", str(LISTING_LIMIT), "--json", "url,labels,createdAt",
        )
        if rc != 0:
            return None
        if not out.strip():
            return []
        try:
            items = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return None
        return items if isinstance(items, list) else None


# ---- the doorway ------------------------------------------------------------

async def file_intake(
    registry,
    *,
    project_id: str,
    what: str,
    done_when: str,
    asker: str,
    channel: str,
    context: Optional[str] = None,
    expected_increments: Optional[int] = None,
    increment_basis: Optional[str] = None,
    now_ms: int,
    gh: Optional[GhAdapter] = None,
) -> dict:
    """Validate → resolve the registered project → stamp provenance → file the
    labeled issue → return ``{issue_url, project_id, repo, expected_increments}``.
    Raises ``IntakeError`` (actionable, synchronous) on any rejection or filing
    failure — never a fake receipt.

    ``expected_increments``/``increment_basis`` are the filer's claim about the
    work item's extent (spec 012 FR-010). They are recorded verbatim in the body
    and never re-derived; an absent claim is recorded as ``unstated`` and
    surfaced by grading, never defaulted."""
    validate_shape(
        what=what,
        done_when=done_when,
        asker=asker,
        channel=channel,
        expected_increments=expected_increments,
        increment_basis=increment_basis,
    )

    project = registry.get((project_id or "").strip())
    if project is None:
        raise IntakeError(
            f"intake rejected: unknown project '{project_id}' — the target must be "
            "a registered project (see list_projects; register_project to add one)"
        )
    slug = repo_slug(project.repo_url)
    if slug is None:
        raise IntakeError(
            f"intake rejected: project '{project.id}' has no GitHub repo_url in the "
            "registry — set it with update_project so intake has a repo to file on"
        )

    gh = gh or GhCli()
    await gh.ensure_label(slug, INTAKE_LABEL)
    url = await gh.create_issue(
        slug,
        title=issue_title(what),
        body=issue_body(
            what=what, done_when=done_when, context=context, asker=asker,
            channel=channel, project_id=project.id, slug=slug, filed_ms=now_ms,
            expected_increments=expected_increments,
            increment_basis=increment_basis,
        ),
        labels=[INTAKE_LABEL],
    )
    if not url:
        raise IntakeError(
            f"intake filing failed: gh could not create the issue on {slug} "
            "(is gh authenticated on the server, and the repo reachable?) — "
            "no receipt was produced; retry after fixing"
        )
    return {
        "issue_url": url,
        "project_id": project.id,
        "repo": slug,
        "expected_increments": expected_increments,
    }


# ---- the readiness gate (spec 006) ------------------------------------------
# The async half of the doorway: filing returns the receipt immediately (above);
# the readiness grade lands moments later as a durable label (FR-007). This
# module owns the FAIL-CLOSED choke point and the label persistence — the
# cognition caller (``intake_readiness``) only returns a parsed verdict.


#: the fixed-shape statement (spec 012 FR-012). Stated where the dispatcher reads
#: the verdict, because #600's complaint was that the shape was a per-ask
#: judgement call: the expected count SIZES the plan, it never selects a shape.
SAGA_SHAPE_NOTE = (
    "Execution shape is fixed: this work item runs as a saga (`create_goal`) "
    "whatever its expected increment count, and the completion judgement is "
    "never bypassed. The count sizes the plan; it selects nothing."
)


def sizing_outcome(
    *, claimed: Optional[int], stated: bool, sizing
) -> tuple[bool, str]:
    """Decide whether the work item's extent needs a HUMAN (spec 012 FR-011).

    Pure and mechanical — no clock, no network, no model trust. Agreement is
    computed from the numbers (``assessed != claimed`` is a disagreement
    whatever the model says about itself); the model's own ``agrees`` boolean is
    consulted only as an ADDITIONAL dissent signal, so a model cannot talk its
    way into agreement. Returns ``(needs_human, reason)``; ``reason`` is ``""``
    only when the claim and the assessment agree."""
    assessed = getattr(sizing, "assessed", None)
    agrees = getattr(sizing, "agrees", None)
    if not stated:
        return True, (
            "no expected increment count was stated by the filer — the extent of "
            "this work item is unrecorded"
        )
    if claimed is None:
        return True, (
            "the filer could not estimate the extent of this work item"
        )
    if assessed is None:
        return True, (
            "grading could not assess the extent of this work item confidently"
        )
    if assessed != claimed or agrees is False:
        return True, (
            f"grading assessed {assessed} unit(s) of work against the filer's "
            f"claim of {claimed} — the claim stands as the record; a human decides"
        )
    return False, ""


def _sizing_paragraph(
    *, claimed: Optional[int], stated: bool, sizing, reason: str
) -> str:
    """The sizing half of the mirror comment. Reports the recorded claim (never
    a rewrite of it), the grader's assessment, and the reason a human is needed."""
    claimed_text = (
        str(claimed) if claimed is not None else UNSTATED_INCREMENTS
    ) if stated else "not recorded"
    assessed = getattr(sizing, "assessed", None)
    assessed_text = "could not assess" if assessed is None else str(assessed)
    basis = (getattr(sizing, "basis", "") or "").strip()
    lines = [
        "**Expected increments** (the second, independent axis — it does not "
        "affect the readiness verdict above):",
        "",
        f"- Filer's claim (the record, unchanged): {claimed_text}",
        f"- Grading assessed: {assessed_text}" + (f" — {basis}" if basis else ""),
    ]
    if reason:
        lines.append(f"- `{NEEDS_SIZING_LABEL}`: {reason}")
    else:
        lines.append("- Grading agrees with the claim.")
    return "\n".join(lines)


def _readiness_comment(label: str, verdict, sizing_note: str = "") -> str:
    """The human-readable mirror of the durable labels — never read back for
    decisions (the labels are source of truth)."""
    tail = ("\n\n" + sizing_note) if sizing_note else ""
    if label == READY_LABEL:
        return (
            "> DevClaw readiness gate: **devclaw-ready**.\n\n"
            "This ask is scoped enough for autonomous execution. It is now eligible for "
            "a human to dispatch (readiness is not auto-dispatch). "
            + SAGA_SHAPE_NOTE
            + tail
        )
    missing = verdict.missing or ["the ask could not be grounded against the repo"]
    lines = "\n".join(f"- {m}" for m in missing)
    return (
        "> DevClaw readiness gate: **needs-refinement**.\n\n"
        "This ask is not yet groundable enough for autonomous execution. Concrete "
        f"missing element(s):\n\n{lines}\n\n"
        "Amend the ask (edit this issue) and re-run the grade "
        "(`regrade_intake`) — no need to re-file."
        + tail
    )


async def _apply_sizing_label(gh, repo: str, issue: str, needs_human: bool) -> None:
    """Persist the sizing axis as its own label (spec 012 FR-011). Added when a
    human must decide the extent, REMOVED when a re-grade reaches agreement, so
    the label flips cleanly exactly like the readiness pair. Best-effort and
    never-raising — a gh hiccup on this axis must not lose the readiness label
    that already landed."""
    try:
        if needs_human:
            await gh.ensure_label(repo, NEEDS_SIZING_LABEL)
            await gh.add_labels(repo, issue, [NEEDS_SIZING_LABEL])
        else:
            await gh.remove_labels(repo, issue, [NEEDS_SIZING_LABEL])
    except Exception as exc:  # noqa: BLE001 — best-effort second axis
        sys.stderr.write(f"sizing: label write failed on {repo}#{issue}: {exc}\n")


async def _apply_readiness_label(
    gh, repo: str, issue: str, label: str, verdict, sizing_note: str = ""
) -> None:
    """Persist the readiness verdict as the source-of-truth label, swapping out
    the opposite readiness label (so a re-grade flips cleanly). Best-effort on
    the mirror comment + the opposite-label removal — the label add is the one
    load-bearing write. Never raises: the orchestrator must stay never-raising."""
    other = NEEDS_REFINEMENT_LABEL if label == READY_LABEL else READY_LABEL
    try:
        await gh.ensure_label(repo, label)
        await gh.add_labels(repo, issue, [label])
    except Exception as exc:  # noqa: BLE001
        sys.stderr.write(f"readiness: label add failed on {repo}#{issue}: {exc}\n")
        return
    try:
        await gh.remove_labels(repo, issue, [other])
    except Exception:  # noqa: BLE001 — the opposite label may simply be absent
        pass
    try:
        await gh.comment(repo, issue, _readiness_comment(label, verdict, sizing_note))
    except Exception:  # noqa: BLE001 — the comment is a mirror, not the truth
        pass


async def grade_and_label(
    *,
    repo: str,
    issue: str,
    what: str,
    done_when: str,
    context: Optional[str],
    workspace_dir: str,
    claude_caller,
    gh: Optional[GhAdapter] = None,
    repo_context: Optional[str] = None,
    expected_increments: Optional[int] = None,
    increment_basis: Optional[str] = None,
    increments_stated: bool = False,
) -> dict:
    """Grade one ask on both axes and record the outcome as durable labels.

    The FAIL-CLOSED choke point (FR-005): any condition that prevents a
    confident READY verdict — an empty repo snapshot, an evaluator error, a
    usage-limit pause raised by the caller, unusable/malformed output — lands
    ``needs-refinement``. It NEVER lands ``devclaw-ready`` on a failure path;
    a crash is not an approval. Never raises.

    The SIZING axis (spec 012 US3) is orthogonal and fails the same way: any
    condition that prevents a confident agreement — no claim, an unestimable
    claim, an unassessable ask, a disagreement, a crashed evaluator — lands
    ``needs-sizing`` for a human. The filer's claim is passed through and
    returned unchanged; grading never overwrites it (FR-010b).

    Returns ``{readiness, expected_increments, increment_basis,
    assessed_increments, sizing, sizing_reason}``.

    Runs AFTER the intake receipt (FR-011) — filing is never blocked on this.
    """
    from . import intake_readiness

    gh = gh or GhCli()

    # Repo context is gathered OUTSIDE the fail-closed try (snapshot-collector
    # convention): it is best-effort and already never-raises.
    if repo_context is None:
        repo_context = await intake_readiness.repo_context(workspace_dir)

    if not (repo_context or "").strip():
        # No repo facts ⇒ ungroundable by construction (FR-008). Distinct reason
        # so the asker can tell "couldn't read the repo" from "ask is vague".
        verdict = intake_readiness.ReadinessVerdict(
            ready=False,
            missing=[
                "could not read the target repository — repo facts are unknown, "
                "so the ask cannot be grounded (retry once the repo is reachable)"
            ],
            rationale="repository context unavailable",
        )
    else:
        try:
            verdict = await intake_readiness.evaluate(
                what=what,
                done_when=done_when,
                context=context,
                repo_context=repo_context,
                claude_caller=claude_caller,
                expected_increments=expected_increments,
                increment_basis=increment_basis,
            )
        except Exception as exc:  # noqa: BLE001 — fail CLOSED, never ready-by-crash
            verdict = intake_readiness.ReadinessVerdict(
                ready=False,
                missing=[
                    "readiness evaluation could not complete (evaluator error or "
                    "cognition unavailable) — re-grade once cognition is available"
                ],
                rationale=f"evaluation failed: {type(exc).__name__}",
            )

    label = READY_LABEL if verdict.ready else NEEDS_REFINEMENT_LABEL
    sizing = getattr(verdict, "sizing", None) or intake_readiness.SizingAssessment()
    needs_human, reason = sizing_outcome(
        claimed=expected_increments, stated=increments_stated, sizing=sizing
    )
    note = _sizing_paragraph(
        claimed=expected_increments,
        stated=increments_stated,
        sizing=sizing,
        reason=reason,
    )
    await _apply_readiness_label(gh, repo, issue, label, verdict, note)
    await _apply_sizing_label(gh, repo, issue, needs_human)
    return {
        "readiness": label,
        "expected_increments": expected_increments if increments_stated else None,
        "increment_basis": (increment_basis or "") if increments_stated else "",
        "assessed_increments": sizing.assessed,
        "sizing": "needs_human" if needs_human else "agreed",
        "sizing_reason": reason,
    }


# ---- manual re-grade (FR-010) -----------------------------------------------

def parse_issue_fields(body: str) -> tuple[str, str, str]:
    """Pull ``what`` / ``done_when`` / ``context`` back out of an intake issue
    body (the shape :func:`issue_body` writes). Used by the manual re-grade to
    read the AMENDED ask on demand — devclaw does not watch for edits (FR-010)."""

    def _section(name: str) -> str:
        m = re.search(
            rf"^##\s+{re.escape(name)}\s*\n(.*?)(?=^##\s|\Z)",
            body or "",
            re.DOTALL | re.MULTILINE,
        )
        return m.group(1).strip() if m else ""

    what = _section("What")
    done_when = _section(CONTRACT_HEADING)
    context = _section("Context")
    if context == "—":  # the rendered "omitted context" placeholder
        context = ""
    return what, done_when, context


def parse_expected_increments(body: str) -> tuple[Optional[int], str, bool]:
    """Read the filer's expected-increment claim back out of an issue body
    (spec 012 FR-010) as ``(count, basis, stated)``.

    This — not a model — is where the recorded count comes from, which is why
    re-grading an unchanged work item yields an identical count (SC-005b). A
    body with no ``Expected increments`` section (a hand-written issue adopted
    under spec 009, or an issue filed before this section existed) yields
    ``(None, "", False)``: extent unrecorded, which grading surfaces for a
    human rather than defaulting (FR-011)."""
    m = re.search(
        r"^##\s+Expected increments\s*\n(.*?)(?=^##\s|\Z)",
        body or "",
        re.DOTALL | re.MULTILINE,
    )
    if not m:
        return None, "", False
    section = m.group(1)
    claim_m = re.search(
        r"\*\*Claimed by the filer:\*\*\s*(.+?)\s*$", section, re.MULTILINE
    )
    basis_m = re.search(r"\*\*Basis:\*\*\s*(.+?)\s*$", section, re.MULTILINE)
    basis = (basis_m.group(1).strip() if basis_m else "")
    if basis == "—":
        basis = ""
    raw = (claim_m.group(1).strip() if claim_m else "")
    if raw.isdigit() and int(raw) >= 1:
        return int(raw), basis, True
    # the section exists but names no usable number — the filer said "unstated"
    # (or wrote something unparseable). Either way the extent is unknown, and
    # the section's presence means the filer was asked and answered.
    return None, basis, True


async def regrade(
    registry,
    *,
    project_id: str,
    issue: str,
    claude_caller=None,
    gh: Optional[GhAdapter] = None,
) -> dict:
    """Grade any OPEN issue on the registered project's repo — the manual
    re-trigger (006 FR-010) and the universal adoption verb (spec 009) in one.
    Intake-format issues keep their structured sections; any other format is
    read as-is — title + body become the ask (009 FR-001), and the grade judges
    verifiable intent from the ask itself. Reads the issue ON DEMAND — no
    automatic watching of edits. Raises ``IntakeError`` (loud, synchronous) when
    the target can't be resolved, the issue can't be read, or the issue is not
    open; the grade itself still fails CLOSED via :func:`grade_and_label`."""
    from . import intake_readiness

    project = registry.get((project_id or "").strip())
    if project is None:
        raise IntakeError(
            f"regrade rejected: unknown project '{project_id}' — the target must be "
            "a registered project (see list_projects)"
        )
    slug = repo_slug(project.repo_url)
    if slug is None:
        raise IntakeError(
            f"regrade rejected: project '{project.id}' has no GitHub repo_url in the "
            "registry — set it with update_project"
        )

    gh = gh or GhCli()
    view = await gh.view_issue(slug, issue)
    if view is None:
        raise IntakeError(
            f"regrade failed: could not read issue {issue} on {slug} "
            "(is gh authenticated, the issue reachable?)"
        )
    state = str(view.get("state") or "").upper()
    if state and state != "OPEN":
        raise IntakeError(
            f"regrade rejected: issue {issue} on {slug} is {state.lower()}, "
            "not open — grading targets open work only"
        )
    body = view.get("body") or ""
    what, done_when, context = parse_issue_fields(body)
    claimed, claim_basis, claim_stated = parse_expected_increments(body)
    if not what.strip():
        # No intake sections — a hand-written issue. The issue as it stands IS
        # the ask (009 FR-001): title + body, no done_when (the grade judges
        # verifiable intent from the ask itself; absent intent fails closed).
        title = (view.get("title") or "").strip()
        what = (title + "\n\n" + body.strip()).strip()
        done_when, context = "", ""
        if not what:
            raise IntakeError(
                f"regrade failed: issue {issue} on {slug} has no readable "
                "title or body — nothing to grade"
            )

    caller = claude_caller or intake_readiness.default_caller()
    graded = await grade_and_label(
        repo=slug,
        issue=issue,
        what=what,
        done_when=done_when,
        context=context,
        workspace_dir=project.workspace_dir,
        claude_caller=caller,
        gh=gh,
        expected_increments=claimed,
        increment_basis=claim_basis,
        increments_stated=claim_stated,
    )
    return {
        "issue_url": issue,
        "project_id": project.id,
        "repo": slug,
        **graded,
    }


# ---- durable recovery (spec 006 P2 hardening) -------------------------------

async def recover_pending_grades(
    registry, *, gh: Optional[GhAdapter] = None, claude_caller=None,
) -> int:
    """Re-grade every intake issue left WITHOUT a readiness label — the pending
    set derived from GitHub itself (the label is the source of truth). Closes the
    P1 restart gap: the async grade is in-process, so a process death between the
    receipt and the grade landing would otherwise leave an ask in permanent
    unlabeled limbo. Meant to run ONCE at serve-start (never on the heartbeat
    tick — the zero-token idle guard stays intact); it is idempotent and
    self-healing, since each boot re-derives the pending set from GitHub, so a
    crash mid-recovery is simply retried next boot (SC-001).

    Zero cognition until an actual ungraded issue is found (the list is a plain
    gh query). Best-effort per project and per issue — one repo's gh hiccup, or
    one unreadable issue, never blocks the rest; never raises. Returns the count
    of issues successfully (re-)graded.
    """
    gh = gh or GhCli()
    graded = 0
    for project in registry.list():
        slug = repo_slug(getattr(project, "repo_url", "") or "")
        if slug is None:
            continue
        try:
            pending = await gh.list_intake_awaiting_grade(slug)
        except Exception as exc:  # noqa: BLE001 — best-effort per repo
            sys.stderr.write(f"readiness recovery: list failed on {slug}: {exc}\n")
            continue
        for issue in pending:
            try:
                await regrade(
                    registry, project_id=project.id, issue=issue,
                    claude_caller=claude_caller, gh=gh,
                )
                graded += 1
            except Exception as exc:  # noqa: BLE001 — one issue never blocks the sweep
                sys.stderr.write(
                    f"readiness recovery: grade failed on {slug} {issue}: {exc}\n"
                )
    return graded


# ---- bulk backlog onboarding (spec 009, US2) ---------------------------------

def _priority_band(label_names: set) -> int:
    """Backlog triage order: ``P0`` < ``P1`` < … < ``P5`` < unlabeled — the same
    priority-band-then-oldest convention the repo backlogs (and spec 007's claim
    order) use."""
    for n in range(6):
        if f"P{n}" in label_names:
            return n
    return 99


async def grade_backlog(
    registry,
    *,
    project_id: str,
    gh: Optional[GhAdapter] = None,
    claude_caller=None,
) -> dict:
    """Grade up to :data:`BULK_GRADE_CAP` open, not-yet-graded issues on one
    registered project through the identical single-issue :func:`regrade` path
    (spec 009 US2). Already-graded issues are skipped with zero cognition; the
    pending set is derived from the readiness labels themselves, so the verb is
    idempotent and resumable by construction — no progress store, no automatic
    continuation. One issue's failure never stops the batch (recovery-sweep
    convention); a LISTING failure rejects loudly — an explicit operator action
    never silently degrades to an empty sweep.

    Returns a report accounting for every listed open issue by URL in exactly
    one bucket: ``graded_ready`` / ``graded_needs_refinement`` / ``failed``
    (with reasons) / ``skipped_already_graded`` / ``not_yet_graded`` (beyond
    the cap — run again to continue), plus the ``cap`` and the stated
    ``listing_limit`` page bound. ``needs_sizing`` is a CROSS-CUTTING list, not
    a bucket: it names the graded issues whose extent needs a human decision
    (spec 012 FR-011), and those URLs also appear in their readiness bucket."""
    project = registry.get((project_id or "").strip())
    if project is None:
        raise IntakeError(
            f"grade_backlog rejected: unknown project '{project_id}' — the target "
            "must be a registered project (see list_projects)"
        )
    slug = repo_slug(project.repo_url)
    if slug is None:
        raise IntakeError(
            f"grade_backlog rejected: project '{project.id}' has no GitHub repo_url "
            "in the registry — set it with update_project"
        )
    gh = gh or GhCli()
    items = await gh.list_open_issues(slug)
    if items is None:
        raise IntakeError(
            f"grade_backlog failed: could not list open issues on {slug} "
            "(is gh authenticated, the repo reachable?)"
        )

    skipped: list[str] = []
    pending: list[tuple[int, str, str]] = []
    for it in items:
        url = (it or {}).get("url")
        if not url:
            continue
        names = {(l or {}).get("name") for l in (it.get("labels") or [])}
        if names & set(_READINESS_LABELS):
            skipped.append(url)
        else:
            pending.append((_priority_band(names), it.get("createdAt") or "", url))
    pending.sort()
    batch = [url for _, _, url in pending[:BULK_GRADE_CAP]]
    remainder = [url for _, _, url in pending[BULK_GRADE_CAP:]]

    report: dict = {
        "project_id": project.id,
        "repo": slug,
        "graded_ready": [],
        "graded_needs_refinement": [],
        "failed": [],
        "skipped_already_graded": skipped,
        "not_yet_graded": remainder,
        "needs_sizing": [],
        "cap": BULK_GRADE_CAP,
        "listing_limit": LISTING_LIMIT,
    }
    for url in batch:
        try:
            result = await regrade(
                registry, project_id=project.id, issue=url,
                claude_caller=claude_caller, gh=gh,
            )
        except Exception as exc:  # noqa: BLE001 — one issue never stops the batch
            report["failed"].append({"url": url, "reason": str(exc)})
            continue
        bucket = (
            "graded_ready"
            if result.get("readiness") == READY_LABEL
            else "graded_needs_refinement"
        )
        report[bucket].append(url)
        if result.get("sizing") == "needs_human":
            report["needs_sizing"].append(url)
    return report
