"""Pre-PR adversarial diff-review gate — make "green" mean "trustworthy".

The verify gate proves a change *behaves* (tests pass) and the test-integrity
guard proves it didn't go green by gutting the suite — but neither one *reads the
code*. That's the hole a spectator-PO can't cover: a `.Take(0)` dead-code line, a
happy-path-only implementation, logic stuffed in the wrong layer, an untested
frontend change all sail through a green gate. This module closes it: after the
gate passes but BEFORE the PR opens, a separate Claude pass reviews the diff
against the ticket and the production quality bar, and returns a structured
verdict. On `request_changes` the task queue feeds the issues back into the
existing retry loop exactly like a gate failure, escalating to the owner after N.

Same shape as the eval judge / planner: cognition is `claude` (host-side, OAuth,
no API key), tiered via DEVCLAW_REVIEW_MODEL; the prompt-building and response
validation are pure, so this is unit-testable with a stubbed caller.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Awaitable, Callable, Optional

from ..llm_call import PlannerError, claude_with_model, extract_json

#: Adversarial code review is judgment-heavy — Sonnet is the right tier (matches
#: the scope grill; heavier than the Haiku classification judge, lighter than the
#: Opus planner). Empty → account default.
from ..model_tiers import model_for as _model_for
REVIEW_MODEL = _model_for("review")
#: per-call timeout. The review reads a diff up to _MAX_DIFF_CHARS (60 KB) and
#: reasons over the whole thing on Sonnet — it was the one large-input cognition
#: role still on the then-90s global ceiling, so a big diff timed out, failed the
#: gate closed, burned the retry budget, and escalated to the owner (#210). Kept
#: explicit even though the general default (``PLANNER_TIMEOUT_MS``, now 180s and
#: env-tunable via ``DEVCLAW_COGNITION_TIMEOUT_S``) has since caught up — the
#: review's budget is a deliberate role-level decision, not an inherited default.
REVIEW_TIMEOUT_MS = 180_000
#: default cognition caller for the review, bound to the review tier + timeout
review_caller = claude_with_model(REVIEW_MODEL, role="review", timeout_ms=REVIEW_TIMEOUT_MS)

#: cap the diff we send so a huge change can't blow the prompt / quota. Tail-kept
#: would lose the header, so we head-keep (the start of the diff, where the
#: substantive files usually are) and note the truncation.
_MAX_DIFF_CHARS = 60_000

_SEVERITIES = ("blocker", "major", "minor")

def _clip_diff(diff: str) -> str:
    if len(diff) <= _MAX_DIFF_CHARS:
        return diff
    return (
        diff[:_MAX_DIFF_CHARS]
        + f"\n\n[... diff truncated at {_MAX_DIFF_CHARS} chars; review what is shown ...]"
    )


# ---------------------------------------------------------------------------
# Generated / lock / vendored filtering.
#
# On closeloop-bench, a "scaffold" step (`ng new`, `dotnet new`) produces a huge,
# mostly-*generated* diff (lockfiles + boilerplate). Sending that whole thing to
# the review model is pointless (a human never wrote it) and dangerous (an
# oversized diff makes the model return non-JSON → the gate crashes). So BEFORE
# clipping/sending we drop whole-file blocks for WELL-KNOWN generated artifacts,
# leaving the reviewer only the hand-written source. Conservative on purpose:
# when in doubt we KEEP the block (better to over-review than skip real code), so
# hand-edited config — package.json, angular.json, *.csproj, tsconfig.json — is
# never stripped.
# ---------------------------------------------------------------------------

#: Path segments whose contents are machine-produced build output / vendored deps
#: — anything *under* one of these directories is generated, not hand-written.
_GENERATED_DIRS = frozenset(
    {"node_modules", "dist", "build", "bin", "obj", ".next", "vendor"}
)
#: Exact filenames that are always machine-generated lockfiles (the ones whose
#: extension isn't a giveaway; the ``*.lock`` suffix rule covers the rest).
_GENERATED_FILES = frozenset(
    {
        "package-lock.json",
        "yarn.lock",
        "pnpm-lock.yaml",
        "Cargo.lock",
        "poetry.lock",
        "composer.lock",
        "Gemfile.lock",
    }
)
#: Filename suffixes that mark a generated/minified/lock artifact.
_GENERATED_SUFFIXES = (".lock", ".min.js", ".min.css")

_DIFF_GIT_RE = re.compile(r"^diff --git a/(.+?) b/(.+)$")


def _is_generated_path(path: str) -> bool:
    """True iff ``path`` is a WELL-KNOWN generated/lock/vendored artifact a human
    never hand-edits. Conservative — only the patterns above match; everything
    else (incl. package.json, angular.json, *.csproj, tsconfig.json) is treated
    as hand-written and KEPT."""
    path = path.strip()
    if not path or path == "/dev/null":
        return False
    parts = path.split("/")
    if any(seg in _GENERATED_DIRS for seg in parts):
        return True
    name = parts[-1]
    if name in _GENERATED_FILES:
        return True
    return name.endswith(_GENERATED_SUFFIXES)


def _block_paths(block: str) -> list[str]:
    """Every file path a single ``diff --git`` block references — both sides of
    the header plus the ``--- a/`` / ``+++ b/`` lines. We drop a block only when
    *every* path it names is generated, so a mixed or ambiguous block is kept."""
    paths: list[str] = []
    for line in block.splitlines():
        if line.startswith("diff --git "):
            m = _DIFF_GIT_RE.match(line)
            if m:
                paths.append(m.group(1))
                paths.append(m.group(2))
        elif line.startswith("--- a/"):
            paths.append(line[len("--- a/"):])
        elif line.startswith("+++ b/"):
            paths.append(line[len("+++ b/"):])
    return paths


def filter_reviewable_diff(diff: str) -> str:
    """Strip whole-file blocks for well-known generated/lock/vendored files from a
    unified git diff, leaving only hand-written source for the reviewer. Blocks
    are split on ``diff --git`` headers; a block is dropped only when every path
    it names is generated (see ``_is_generated_path``) — when in doubt it's KEPT.
    Any preamble before the first ``diff --git`` is preserved. A diff with no
    ``diff --git`` header (or an already-clean one) is returned unchanged."""
    if "diff --git " not in diff:
        return diff

    blocks: list[list[str]] = []
    preamble: list[str] = []
    current: Optional[list[str]] = None
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append(current)

    kept: list[str] = list(preamble)
    for block in blocks:
        text = "".join(block)
        paths = _block_paths(text)
        # Drop only when we resolved at least one path AND all of them are
        # generated; otherwise keep (unresolved path → keep, real source → keep).
        if paths and all(_is_generated_path(p) for p in paths):
            continue
        kept.append(text)
    return "".join(kept)


def build_review_prompt(
    *,
    goal: str,
    kind: str,
    diff: str,
    repo_context: Optional[str] = None,
) -> str:
    from .prompts import load_prompt

    parts = [load_prompt("review-gate")]
    parts.append(f"TICKET ({kind}):\n{goal}")
    if repo_context and repo_context.strip():
        parts.append(
            "REPOSITORY CONTEXT (facts from the task workspace — the source of "
            "truth for repo identity, branch, and which files/dirs exist):\n"
            + repo_context.strip()
        )
    parts.append(f"DIFF UNDER REVIEW:\n{_clip_diff(filter_reviewable_diff(diff))}")
    return "\n\n".join(parts)


def validate_review(parsed: object) -> dict:
    """Validate + normalize the model's review into a verdict dict. Enforces the
    invariant that request_changes ⇔ there is a blocker/major issue, so the
    verdict can't disagree with its own issue list."""
    if not isinstance(parsed, dict):
        raise PlannerError("Review response must be a JSON object")
    verdict = parsed.get("verdict")
    if verdict not in ("approve", "request_changes"):
        raise PlannerError(
            f"Review verdict must be 'approve' or 'request_changes', got {verdict!r}"
        )
    summary = parsed.get("summary")
    summary = summary.strip() if isinstance(summary, str) else ""

    raw_issues = parsed.get("issues")
    issues: list[dict] = []
    if isinstance(raw_issues, list):
        for it in raw_issues:
            if not isinstance(it, dict):
                continue
            sev = it.get("severity")
            sev = sev if sev in _SEVERITIES else "minor"
            issues.append(
                {
                    "severity": sev,
                    "location": str(it.get("location", "")).strip(),
                    "problem": str(it.get("problem", "")).strip(),
                    "fix": str(it.get("fix", "")).strip(),
                }
            )
    blocking = [i for i in issues if i["severity"] in ("blocker", "major")]
    # Reconcile verdict with the issue list — the issues are the evidence, so they
    # win: a "request_changes" with no blocking issue is downgraded; an "approve"
    # that nonetheless lists a blocker/major is upgraded to request_changes.
    final_verdict = "request_changes" if blocking else "approve"
    return {
        "verdict": final_verdict,
        "summary": summary,
        "issues": issues,
        "blocking": blocking,
    }


def format_feedback(review: dict) -> str:
    """Render a request_changes verdict as actionable feedback fed back into the
    retry loop (becomes the task's failure context, like a gate failure)."""
    lines = ["code review requested changes before this can ship:"]
    if review.get("summary"):
        lines.append(review["summary"])
    for i in review.get("blocking", []):
        loc = f" [{i['location']}]" if i.get("location") else ""
        fix = f" — fix: {i['fix']}" if i.get("fix") else ""
        lines.append(f"- ({i['severity']}){loc} {i['problem']}{fix}")
    lines.append(
        "Address every blocker/major issue above (do not weaken tests to do it), "
        "then re-verify."
    )
    return "\n".join(lines)


async def review_diff(
    *,
    goal: str,
    kind: str,
    diff: str,
    repo_context: Optional[str] = None,
    claude_caller: Callable[[str], Awaitable[str]] = review_caller,
) -> dict:
    """Review one diff into a validated verdict dict. ``claude_caller`` is injected
    so tests can stub the subprocess. Raises PlannerError if the model returns
    unparseable/invalid JSON (the caller decides whether to fail open)."""
    # Nothing hand-written to review (a pure generated/lock/vendored diff, e.g. a
    # scaffold step's lockfile churn) → approve/skip gracefully rather than send
    # the model an empty diff. Same effect as the empty-diff short-circuit upstream.
    if not filter_reviewable_diff(diff).strip():
        return {
            "verdict": "approve",
            "summary": "no hand-written changes to review "
            "(diff is entirely generated/lock/vendored files)",
            "issues": [],
            "blocking": [],
        }
    prompt = build_review_prompt(
        goal=goal, kind=kind, diff=diff, repo_context=repo_context
    )
    raw = await claude_caller(prompt)
    try:
        parsed = json.loads(extract_json(raw))
    except json.JSONDecodeError as err:
        raise PlannerError(f"Review JSON parse failed: {err}", raw) from err
    return validate_review(parsed)


def _dedup_issues(issues: list[dict]) -> list[dict]:
    """Union issues, deduped by (location, severity) — the first occurrence
    wins. Deterministic order (insertion) so the aggregate is stable."""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for issue in issues:
        key = (issue.get("location", ""), issue.get("severity", ""))
        if key in seen:
            continue
        seen.add(key)
        out.append(issue)
    return out


# ---------------------------------------------------------------------------
# Cognition-TIMEOUT degradation ladder (systemic fix #5).
#
# The review model gets a fixed per-call budget (REVIEW_TIMEOUT_MS). On a large-
# but-legitimate diff it can exhaust that budget, the caller raises a timeout
# PlannerError, and the task fails CLOSED with no agent retry (#186 — re-running
# reproduces the same over-large diff and re-times-out identically). Correct, but
# it gives up on the WHOLE diff without trying anything cheaper first. This ladder
# adds ONE degradation rung *before* that hard fail: when the full-diff review
# times out, split the diff into one sub-diff PER FILE and review each
# independently, then UNION the verdicts with evidence-wins semantics (a single
# sub-review's blocker forces request_changes).
# Each per-file review is smaller, so it fits the budget where the whole diff did
# not — a legitimate large diff can still earn a real verdict.
#
# Fail-closed is preserved end to end:
#   - Trigger is a TIMEOUT or a non-quota UNPARSEABLE-VERDICT crash (#381): on an
#     oversized diff the model can return non-JSON for the same "input too big"
#     reason it times out, so per-file split is worth the same try. If the split
#     ALSO can't parse, the sub-reviews RAISE → the whole diff still fails closed
#     + fast. A quota/rate/auth-shaped crash is EXCLUDED from the trigger — it
#     re-raises unchanged so the queue pauses instead of the ladder spraying
#     per-file calls into a live usage cap.
#   - Each per-file sub-review STILL fails closed: a sub-review that times out or
#     can't be parsed RAISES, which propagates out of the ladder → the whole diff
#     fails closed (never an approval), carrying its raw response so a quota-shaped
#     sub-failure is still classified as quota by the queue and PAUSES.
#   - When the ladder can't help (a single unsplittable file still times out, or
#     the diff has more files than the fan-out cap), it RE-RAISES the original
#     timeout → the same crash-marker, no-agent-retry path (#186). Degradation
#     NEVER manufactures a passing verdict.
#
# Opt-out via DEVCLAW_REVIEW_DEGRADE=0 (then a timeout re-raises immediately,
# byte-identical to the pre-ladder gate). The per-file fan-out is bounded by
# DEVCLAW_REVIEW_DEGRADE_MAX_FILES so a pathologically wide diff can't spray
# hundreds of model calls — over the cap it fails closed and a human splits it.
# ---------------------------------------------------------------------------

#: Default per-file fan-out cap. A diff with more reviewable files than this is
#: NOT degraded (the fan-out would be too large a burst); it fails closed and the
#: owner splits the commit. Env-tunable via DEVCLAW_REVIEW_DEGRADE_MAX_FILES.
_DEGRADE_MAX_FILES_DEFAULT = 40


def _degrade_enabled() -> bool:
    """Whether the timeout degradation ladder runs. Default ON; an operator opts
    out with ``DEVCLAW_REVIEW_DEGRADE=0`` (or false/no/off), which restores the
    pre-ladder behaviour exactly (a review timeout re-raises immediately)."""
    raw = os.environ.get("DEVCLAW_REVIEW_DEGRADE", "").strip().lower()
    if not raw:
        return True
    return raw not in ("0", "false", "no", "off")


def _degrade_max_files() -> int:
    """Per-file fan-out cap from ``DEVCLAW_REVIEW_DEGRADE_MAX_FILES``, clamped to
    >=1. Unparseable / <1 → the default. Above the cap the ladder declines to
    degrade and the diff fails closed."""
    raw = os.environ.get("DEVCLAW_REVIEW_DEGRADE_MAX_FILES", "")
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return _DEGRADE_MAX_FILES_DEFAULT
    return max(1, v)


#: Markers of an UNPARSEABLE-VERDICT review crash (the model returned no usable
#: JSON verdict). From ``extract_json`` ("No JSON object found …") and the review
#: parser's own raises ("Review JSON parse failed …", "must be a JSON object").
_REVIEW_UNPARSEABLE_MARKERS = (
    "no json object",
    "json parse failed",
    "must be a json object",
)

#: A ``claude --print`` process KILLED BY A SIGNAL surfaces as a negative exit
#: code (``exited -9`` = SIGKILL, ``-15`` = SIGTERM) in the PlannerError message
#: (``llm_call.py``: ``f"claude --print exited {proc.returncode}. …"``). A signal
#: death is the process being torn down mid-call — OOM / watchdog kill on a diff
#: too big to hold — i.e. the SAME oversized-input family as a timeout, NOT a
#: clean nonzero exit. A quota/rate/auth crash always comes back as a *clean*
#: nonzero exit carrying the usage wording, never a signal, so this never
#: swallows a pausing failure.
_KILLED_BY_SIGNAL_RE = re.compile(r"claude --print exited -\d+")


def _is_degradable(err: Exception) -> bool:
    """True iff ``err`` is a review failure worth retrying by per-file split (#381).

    Three triggers now:
    - a **TIMEOUT** (the original symptom),
    - a **SIGNAL DEATH** (``exited -9``/``-15`` — the process killed mid-call, the
      same oversized-input family as a timeout), or
    - an **unparseable-verdict crash** (no-JSON / parse-failed) that is NOT
      quota/rate/auth-shaped.

    The insight #381 adds: on an **oversized** diff the review model can return
    non-JSON for the *same* "input too big" reason it times out — the response
    runs long / gets truncated / never emits the verdict object. Splitting into
    per-file sub-diffs fits the budget and can earn a real verdict, exactly as it
    does for a timeout. Fail-closed is untouched: if the sub-reviews also can't
    parse, they RAISE → the whole diff still fails closed (never an approval).

    A quota/rate/auth-shaped non-JSON is deliberately EXCLUDED — it must re-raise
    unchanged so the queue's pause-and-resume classifier sees it, instead of the
    ladder spraying per-file calls into a live usage cap. (A timeout and a signal
    death are never quota-shaped — quota is a clean nonzero exit with wording — so
    this guard only matters for the unparseable trigger.)"""
    if not isinstance(err, PlannerError):
        return False
    if "timed out" in str(err).lower():
        return True
    if _KILLED_BY_SIGNAL_RE.search(str(err)):
        return True
    if not any(m in str(err).lower() for m in _REVIEW_UNPARSEABLE_MARKERS):
        return False
    # Classify the RAW model output (where the quota/usage wording lives), not the
    # generic "No JSON object found" message. Pausing kinds re-raise, never split.
    from ..loom.limits import PAUSING_KINDS, classify_failure

    return classify_failure(err.raw or str(err)).kind not in PAUSING_KINDS


def _split_diff_by_file(diff: str) -> list[str]:
    """Split a unified diff into one sub-diff per ``diff --git`` block, so an
    over-large diff that timed out as a whole can be reviewed file-by-file. Any
    preamble before the first header is prepended to the first block so nothing is
    dropped. A diff with no header (or blank) yields at most one element — the
    caller then can't degrade and fails closed. Expects an already
    reviewable-filtered diff (generated/lock/vendored blocks removed)."""
    if not diff.strip():
        return []
    if "diff --git " not in diff:
        return [diff]
    preamble: list[str] = []
    blocks: list[list[str]] = []
    current: Optional[list[str]] = None
    for line in diff.splitlines(keepends=True):
        if line.startswith("diff --git "):
            if current is not None:
                blocks.append(current)
            current = [line]
        elif current is None:
            preamble.append(line)
        else:
            current.append(line)
    if current is not None:
        blocks.append(current)
    texts = ["".join(b) for b in blocks]
    pre = "".join(preamble)
    if pre.strip() and texts:
        texts[0] = pre + texts[0]
    return texts


def _aggregate_file_reviews(results: list[dict], *, n_files: int) -> dict:
    """Union per-file sub-review verdicts into one, with evidence-wins semantics:
    issues are unioned + deduped by (location, severity), the blocking subset is
    the blocker/major issues, and the verdict is request_changes iff any blocking
    issue exists. So a single file's blocker still forces request_changes.

    IMPORTANT — this is NOT strictly >= as strict as a whole-diff review: reviewing
    each file in ISOLATION loses cross-file context, so a regression that only
    shows up across files (a symbol renamed in one file but still referenced in
    another) can pass per-file where a whole-diff review would have blocked it.
    This is an accepted thoroughness
    trade-off, engaged ONLY on a path that otherwise hard-fails the diff outright:
    a degraded real verdict on most of the diff beats no verdict at all, and every
    fail-closed guarantee (a sub-review that can't produce a verdict still raises →
    the whole diff fails closed) is preserved."""
    merged_issues = _dedup_issues([i for r in results for i in r.get("issues", [])])
    blocking = [i for i in merged_issues if i["severity"] in ("blocker", "major")]
    verdict = "request_changes" if blocking else "approve"
    summary = (
        f"degraded per-file review — the full diff exceeded the review budget, so "
        f"it was reviewed as {n_files} per-file sub-diffs and their verdicts "
        f"unioned: {verdict} ({len(blocking)} blocking issue(s))."
    )
    return {
        "verdict": verdict,
        "summary": summary,
        "issues": merged_issues,
        "blocking": blocking,
    }


async def review_gate(
    *,
    goal: str,
    kind: str,
    diff: str,
    repo_context: Optional[str] = None,
    claude_caller: Callable[[str], Awaitable[str]] = review_caller,
) -> dict:
    """The wired review entry — the single adversarial reviewer
    (:func:`review_diff`) wrapped in the cognition-timeout degradation ladder.

    The happy path is byte-identical to ``review_diff``: this just returns it.
    Only when that call raises a TIMEOUT (or a non-quota unparseable-verdict
    crash) does the ladder engage — it re-reviews the diff one file at a time and
    unions the verdicts (see the ladder note above). Every fail-closed invariant
    is preserved; the ladder can only turn a whole-diff timeout into a real
    per-file verdict OR fall through to the same fail-closed raise, never into an
    approval."""
    try:
        return await review_diff(
            goal=goal, kind=kind, diff=diff, repo_context=repo_context,
            claude_caller=claude_caller,
        )
    except PlannerError as err:
        # A TIMEOUT or a non-quota UNPARSEABLE-VERDICT crash triggers the ladder
        # (#381), and only when enabled. A quota/rate/auth-shaped crash re-raises
        # UNCHANGED so the queue's fail-closed / quota-classify paths see it as
        # before — never spray per-file calls into a live usage cap.
        if not _degrade_enabled() or not _is_degradable(err):
            raise
        sub_diffs = _split_diff_by_file(filter_reviewable_diff(diff))
        # Can't split further (0 or 1 reviewable file) → nothing cheaper to try →
        # re-raise the ORIGINAL timeout so the diff fails closed on the same
        # crash-marker, no-agent-retry path (#186).
        if len(sub_diffs) <= 1:
            raise
        # Too many files to fan out safely → decline to degrade (a burst of that
        # many model calls is its own hazard) and fail closed; a human splits it.
        if len(sub_diffs) > _degrade_max_files():
            raise
        # Review each file's sub-diff independently through the SAME reviewer.
        # A sub-review that RAISES (still times out on one huge file, or
        # unparseable) must propagate straight out of the ladder → the whole diff
        # fails closed (never approved), carrying its raw response for the queue's
        # quota classifier. On that first failure we CANCEL the still-running
        # siblings: the ladder has already decided to fail closed, so leaving the
        # other per-file `claude` calls running only burns OAuth quota. (Plain
        # gather raises but ORPHANS the siblings — hence explicit tasks + cancel.)
        tasks = [
            asyncio.ensure_future(
                review_diff(
                    goal=goal, kind=kind, diff=sub, repo_context=repo_context,
                    claude_caller=claude_caller,
                )
            )
            for sub in sub_diffs
        ]
        try:
            results = await asyncio.gather(*tasks)
        except BaseException:
            for t in tasks:
                t.cancel()
            # Let the cancellations settle (swallow their CancelledError/results)
            # before re-raising the original sub-review failure.
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return _aggregate_file_reviews(results, n_files=len(sub_diffs))
