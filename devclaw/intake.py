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

import asyncio
import re
import sys
from datetime import datetime, timezone
from typing import Optional, Protocol

#: the intake marker label every filed ask carries (proposal §5).
INTAKE_LABEL = "devclaw-intake"

#: readiness source-of-truth labels (spec 006, FR-007). Exactly one is present
#: on a graded issue; while the async grade is pending, neither is (the ask is
#: treated as not-ready until the label lands). These labels ARE the durable
#: truth — generated human views (comments) mirror them, never the reverse.
READY_LABEL = "devclaw-ready"
NEEDS_REFINEMENT_LABEL = "needs-refinement"
_READINESS_LABELS = (READY_LABEL, NEEDS_REFINEMENT_LABEL)

#: recorded intake channels. ``other`` is the explicit escape hatch so a new
#: surface never has to lie about its channel to get through validation.
CHANNELS = ("chat", "telegram", "a2a", "other")

#: minimum ``done_when`` length — same bar as goal admission's vague-done_when
#: gate: an unverifiable one-worder is rejected at the doorway, not discovered
#: at dispatch.
MIN_DONE_WHEN_CHARS = 20

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
    *, what: str, done_when: str, asker: str, channel: str
) -> None:
    """Synchronous shape validation (proposal §5). Raises ``IntakeError`` with
    every problem named at once — the asker fixes one round trip, not N."""
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
    if problems:
        raise IntakeError("intake rejected: " + "; ".join(problems))


def issue_title(what: str) -> str:
    """First line of the ask, marked as intake."""
    head = (what or "").strip().splitlines()[0].strip()
    return f"[intake] {head}"[:_TITLE_MAX]


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
        f"## Done when\n\n{done_when.strip()}\n\n"
        f"## Context\n\n{ctx}\n\n"
        "## Provenance\n\n"
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
    async def read_issue(self, repo: str, issue: str) -> Optional[str]: ...


async def _run(*args: str) -> tuple[int, str]:
    """Run a command, return (exit_code, combined output). Never raises. The
    subprocess boundary of the module — mirrors ``delivery/repo.py``."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, f"{args[0]} not runnable: {exc}"
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


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

    async def read_issue(self, repo: str, issue: str) -> Optional[str]:
        rc, out = await _run(
            "gh", "issue", "view", issue, "--repo", repo, "--json", "body", "-q", ".body"
        )
        return out if rc == 0 else None


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
    now_ms: int,
    gh: Optional[GhAdapter] = None,
) -> dict:
    """Validate → resolve the registered project → stamp provenance → file the
    labeled issue → return ``{issue_url, project_id, repo}``. Raises
    ``IntakeError`` (actionable, synchronous) on any rejection or filing
    failure — never a fake receipt."""
    validate_shape(what=what, done_when=done_when, asker=asker, channel=channel)

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
        ),
        labels=[INTAKE_LABEL],
    )
    if not url:
        raise IntakeError(
            f"intake filing failed: gh could not create the issue on {slug} "
            "(is gh authenticated on the server, and the repo reachable?) — "
            "no receipt was produced; retry after fixing"
        )
    return {"issue_url": url, "project_id": project.id, "repo": slug}


# ---- the readiness gate (spec 006) ------------------------------------------
# The async half of the doorway: filing returns the receipt immediately (above);
# the readiness grade lands moments later as a durable label (FR-007). This
# module owns the FAIL-CLOSED choke point and the label persistence — the
# cognition caller (``intake_readiness``) only returns a parsed verdict.


def _readiness_comment(label: str, verdict) -> str:
    """The human-readable mirror of the durable label — never read back for
    decisions (the label is source of truth)."""
    if label == READY_LABEL:
        return (
            "> DevClaw readiness gate: **devclaw-ready**.\n\n"
            "This ask is scoped enough to attempt firming. It is now eligible for "
            "a human to dispatch (readiness is not auto-dispatch)."
        )
    missing = verdict.missing or ["the ask could not be grounded against the repo"]
    lines = "\n".join(f"- {m}" for m in missing)
    return (
        "> DevClaw readiness gate: **needs-refinement**.\n\n"
        "This ask is not yet groundable enough to attempt firming. Concrete "
        f"missing element(s):\n\n{lines}\n\n"
        "Amend the ask (edit this issue) and re-run the grade "
        "(`regrade_intake`) — no need to re-file."
    )


async def _apply_readiness_label(gh, repo: str, issue: str, label: str, verdict) -> None:
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
        await gh.comment(repo, issue, _readiness_comment(label, verdict))
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
) -> str:
    """Grade one ask for readiness and record the outcome as a durable label.

    The FAIL-CLOSED choke point (FR-005): any condition that prevents a
    confident READY verdict — an empty repo snapshot, an evaluator error, a
    usage-limit pause raised by the caller, unusable/malformed output — lands
    ``needs-refinement``. It NEVER lands ``devclaw-ready`` on a failure path;
    a crash is not an approval. Never raises. Returns the applied label.

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
    await _apply_readiness_label(gh, repo, issue, label, verdict)
    return label


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
    done_when = _section("Done when")
    context = _section("Context")
    if context == "—":  # the rendered "omitted context" placeholder
        context = ""
    return what, done_when, context


async def regrade(
    registry,
    *,
    project_id: str,
    issue: str,
    claude_caller=None,
    gh: Optional[GhAdapter] = None,
) -> dict:
    """Manual re-trigger (FR-010): read the (possibly amended) intake issue,
    re-run the readiness grade, and swap the readiness label. Reads the issue
    ON DEMAND — no automatic watching of edits. Raises ``IntakeError`` (loud,
    synchronous) when the target can't be resolved or the issue can't be read;
    the grade itself still fails CLOSED via :func:`grade_and_label`."""
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
    body = await gh.read_issue(slug, issue)
    if not body:
        raise IntakeError(
            f"regrade failed: could not read issue {issue} on {slug} "
            "(is gh authenticated, the issue reachable?)"
        )
    what, done_when, context = parse_issue_fields(body)
    if not what.strip():
        raise IntakeError(
            f"regrade failed: issue {issue} has no readable '## What' section — "
            "is it a devclaw intake issue?"
        )

    caller = claude_caller or intake_readiness.default_caller()
    label = await grade_and_label(
        repo=slug,
        issue=issue,
        what=what,
        done_when=done_when,
        context=context,
        workspace_dir=project.workspace_dir,
        claude_caller=caller,
        gh=gh,
    )
    return {
        "issue_url": issue,
        "project_id": project.id,
        "repo": slug,
        "readiness": label,
    }
