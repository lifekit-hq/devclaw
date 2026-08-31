"""The intake readiness gate — "is this ask scoped enough for autonomous execution?".

Spec: ``specs/006-intake-readiness-gate/spec.md`` (P1 of the autonomous
issue-driven pipeline). This is a one-shot cognition caller in the shape of
``goal/evaluator.py``: build a prompt, call ``claude`` through the cognition
seam (OAuth-only), parse JSON, return a parsed :class:`ReadinessVerdict`. It
decides ONLY groundability — a locatable surface, a concrete change, a
verifiable intent — and never derives ``done_when`` or a checklist (that stays
the worker's speckit specify/plan job; FR-006 non-overlap).

Spec 012 US3 adds a SECOND, orthogonal axis to the same one-shot call (no new
cognition call — FR-013): the grader reports how many units of work it would
assess the ask to take, and whether that matches the count the *filer* claimed.
The filer's claim is the record (FR-010b); this module never emits a number
that replaces it, and a sizing disagreement never moves the readiness verdict.

Spec 028 US2 adds a THIRD axis to the same call (again no new cognition call —
FR-008, so the zero-token idle guard is untouched): staleness. An ask whose
described condition the repo already satisfies is not ready — dispatching it
burns a session to discover the work is done. Staleness FORCES not-ready
(FR-007); sizing never moves it and it never moves sizing (FR-009).

Layering (``.claude/rules/cognition-prompts.md``): this module returns parsed
output; the intake layer (:mod:`devclaw.intake`) is what persists the verdict
as a GitHub label and is where the FAIL-CLOSED choke point lives. On its own
this caller may raise :class:`ReadinessError` on malformed output — the intake
orchestrator catches every failure and lands ``needs-refinement`` (never
``devclaw-ready``), so a crash is never an approval (FR-005).

The workspace snapshot collector (:func:`repo_context`) follows the #227
convention: an ``asyncio.to_thread`` wrapper over
``task_git._review_repo_context_sync`` (imported as a module global so tests
patch it HERE), best-effort and never-raises — a git hiccup degrades to ``''``.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

# The workspace-snapshot collector (#227), reused to ground the readiness gate.
# Imported as a module global so tests patch it on THIS module — same convention
# as the evaluator's re-export.
from .task_git import _review_repo_context_sync  # noqa: F401

from .model_tiers import model_for as _model_for

ClaudeCaller = Callable[[str], Awaitable[str]]

#: the readiness gate's model tier (see model_tiers._ROLE_TIER).
READINESS_MODEL = _model_for("intake_readiness")


class ReadinessError(Exception):
    """The evaluator produced no usable verdict (non-JSON / wrong shape). The
    intake orchestrator maps this to a fail-closed ``needs-refinement`` — it is
    never surfaced as ``devclaw-ready``."""

    def __init__(self, message: str, raw: str | None = None) -> None:
        super().__init__(message)
        self.raw = raw


@dataclass(frozen=True)
class SizingAssessment:
    """The grader's read of the work item's extent (spec 012 FR-010a).

    ``assessed`` is the number of units of work the grader would expect, or
    ``None`` when it could not judge confidently. ``agrees`` is the grader's own
    comparison against the filer's claim, or ``None`` when there was no claim to
    compare against. This is EPHEMERAL: it feeds a surfacing decision and a
    comment, and never becomes the recorded count — the filer's claim is the
    record (FR-010b)."""

    assessed: Optional[int] = None
    agrees: Optional[bool] = None
    basis: str = ""


@dataclass
class ReadinessVerdict:
    """The parsed outcome — binary ready/not-ready plus, when not-ready, the
    concrete missing element(s) that make the reason actionable (FR-004), plus
    the orthogonal sizing assessment (spec 012 FR-010a). Ephemeral input to the
    label decisions; not a durable store."""

    ready: bool
    missing: list[str] = field(default_factory=list)
    rationale: str = ""
    sizing: SizingAssessment = field(default_factory=SizingAssessment)
    #: the described condition is already resolved in the repo (spec 028
    #: FR-010). Absent/garbled model output ⇒ False: a missing staleness signal
    #: must not block an otherwise-groundable ask, and the readiness axis fails
    #: closed on its own.
    stale: bool = False


async def repo_context(workspace_dir: str) -> str:
    """Best-effort, never-raising snapshot of the target repo for grounding.
    Returns ``''`` when the workspace is absent/unreadable — the caller treats
    an empty snapshot as "repo facts unknown ⇒ not ready" (FR-008)."""
    if not workspace_dir:
        return ""
    try:
        return await asyncio.to_thread(_review_repo_context_sync, workspace_dir)
    except Exception:  # noqa: BLE001 — best-effort by contract
        return ""


def _repo_context_block(repo_context: Optional[str]) -> str:
    """Render the grounding section. Present ⇒ a labeled facts block; absent ⇒
    an explicit "unknown" marker so the model cannot silently ground on nothing.
    The ``## Repository context`` header lives HERE, not in the instruction text
    of the raw template (so omission tests stay non-vacuous)."""
    ctx = (repo_context or "").strip()
    if ctx:
        return (
            "## Repository context (facts from the target repo — the source of "
            "truth for what exists)\n\n" + ctx
        )
    return (
        "## Repository context\n\n(unavailable — repo facts are unknown; the ask "
        "cannot be grounded against known files)"
    )


def _increment_claim_block(
    expected_increments: Optional[int], increment_basis: Optional[str]
) -> str:
    """Render the filer's expected-increment claim as prompt input (spec 012
    FR-010). Absent ⇒ an explicit "no claim" marker, so the grader is told there
    is nothing to compare against rather than left to invent a comparison."""
    basis = (increment_basis or "").strip() or "(none stated)"
    if expected_increments is None:
        return (
            "The filer stated NO expected increment count. Reason given: "
            f"{basis}\nThere is nothing to agree or disagree with: report your "
            "own assessment and set `agrees` to null."
        )
    return (
        f"The filer claims this ask takes {expected_increments} unit(s) of work.\n"
        f"Basis given by the filer: {basis}"
    )


def build_prompt(
    *,
    what: str,
    done_when: str,
    context: Optional[str] = None,
    repo_context: Optional[str] = None,
    expected_increments: Optional[int] = None,
    increment_basis: Optional[str] = None,
) -> str:
    from .prompts import load_prompt

    return load_prompt(
        "intake-readiness",
        what=(what or "").strip(),
        done_when=(done_when or "").strip() or "(none provided)",
        context=(context or "").strip() or "(none provided)",
        repo_context_block=_repo_context_block(repo_context),
        increment_claim_block=_increment_claim_block(
            expected_increments, increment_basis
        ),
    )


def extract_json(text: str) -> str:
    trimmed = (text or "").strip()
    if trimmed.startswith("{"):
        return trimmed
    fence = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", trimmed)
    if fence and fence.group(1):
        return fence.group(1)
    first, last = trimmed.find("{"), trimmed.rfind("}")
    if first >= 0 and last > first:
        return trimmed[first : last + 1]
    raise ReadinessError("no JSON object found in readiness response", text)


#: The concrete, asker-fixable reason a stale ask is not ready (spec 028
#: FR-007). ONE home for the wording — the prompt asks the question, this
#: names the answer, and the intake comment renders it verbatim.
STALE_REASON = (
    "the described condition appears to be already resolved in the repository"
)


def _parse_stale(raw: object) -> bool:
    """Normalize the staleness flag. Anything that is not an explicit
    affirmative is False (spec 028 FR-010): a missing or garbled staleness
    signal must not block an otherwise-groundable ask, and the readiness axis
    already fails closed on its own."""
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"true", "yes", "stale"}
    return False


def _parse_sizing(raw: object) -> SizingAssessment:
    """Normalize the ``increments`` object. Fails toward "a human decides":
    anything missing, wrongly typed, or out of range yields ``assessed=None``
    (which the intake layer surfaces), never a fabricated agreement."""
    if not isinstance(raw, dict):
        return SizingAssessment()
    assessed = raw.get("assessed")
    if isinstance(assessed, bool):  # bool is an int subclass — not a count
        assessed = None
    elif isinstance(assessed, int):
        assessed = assessed if assessed >= 1 else None
    elif isinstance(assessed, str) and assessed.strip().isdigit():
        assessed = int(assessed.strip()) or None
    else:
        assessed = None
    raw_agrees = raw.get("agrees")
    agrees = raw_agrees if isinstance(raw_agrees, bool) else None
    return SizingAssessment(
        assessed=assessed,
        agrees=agrees,
        basis=str(raw.get("basis", "")).strip(),
    )


def validate(parsed: object) -> ReadinessVerdict:
    """Normalize the model response into a :class:`ReadinessVerdict`.

    Fails toward not-ready by construction: ``ready`` is honored as True ONLY
    for an explicit affirmative; any drift (missing field, wrong type, a hedged
    string) yields not-ready. A not-ready verdict always carries at least one
    concrete missing element (FR-004) — synthesized from the rationale if the
    model omitted the array.

    Staleness (spec 028 FR-007) is checked BEFORE the ready short-circuit: an
    ask the repo already satisfies is not ready no matter how well it grounds."""
    if not isinstance(parsed, dict):
        raise ReadinessError("readiness output must be a JSON object")
    raw_ready = parsed.get("ready")
    if isinstance(raw_ready, bool):
        ready = raw_ready
    elif isinstance(raw_ready, str):
        ready = raw_ready.strip().lower() in {"true", "yes", "ready", "devclaw-ready"}
    else:
        ready = False
    raw_missing = parsed.get("missing") or []
    missing = (
        [str(m).strip() for m in raw_missing if str(m).strip()]
        if isinstance(raw_missing, list)
        else []
    )
    rationale = str(parsed.get("rationale", "")).strip()
    sizing = _parse_sizing(parsed.get("increments"))
    stale = _parse_stale(parsed.get("stale"))
    if stale:
        # FR-007: a stale ask is NEVER ready, however well it grounds — the work
        # it describes is already done, so dispatching it burns a session to
        # rediscover that. The reason leads the missing list so the asker sees
        # the actual objection first.
        if STALE_REASON not in missing:
            missing = [STALE_REASON, *missing]
        return ReadinessVerdict(
            ready=False,
            missing=missing,
            rationale=rationale,
            sizing=sizing,
            stale=True,
        )
    if ready:
        return ReadinessVerdict(
            ready=True, missing=[], rationale=rationale, sizing=sizing
        )
    if not missing:
        missing = [
            rationale
            or "the ask could not be confidently grounded against the repository"
        ]
    return ReadinessVerdict(
        ready=False, missing=missing, rationale=rationale, sizing=sizing
    )


async def evaluate(
    *,
    what: str,
    done_when: str,
    context: Optional[str],
    repo_context: Optional[str],
    claude_caller: ClaudeCaller,
    expected_increments: Optional[int] = None,
    increment_basis: Optional[str] = None,
) -> ReadinessVerdict:
    """Run the readiness evaluation. ``claude_caller`` is injected so tests stub
    the LLM. Raises :class:`ReadinessError` on unusable output — the intake
    orchestrator turns that into a fail-closed ``needs-refinement``."""
    prompt = build_prompt(
        what=what,
        done_when=done_when,
        context=context,
        repo_context=repo_context,
        expected_increments=expected_increments,
        increment_basis=increment_basis,
    )
    raw = await claude_caller(prompt)
    try:
        parsed = json.loads(extract_json(raw))
    except json.JSONDecodeError as exc:
        raise ReadinessError(f"readiness emitted invalid JSON: {exc}", raw) from exc
    return validate(parsed)


def default_caller() -> ClaudeCaller:
    """Production cognition caller bound to the readiness tier (lazy import so
    tests that inject a fake never touch the subprocess)."""
    from .llm_call import claude_with_model

    return claude_with_model(READINESS_MODEL, role="intake_readiness")
