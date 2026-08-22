"""The saga feed-forward section — what previous increments of THIS goal
delivered (spec 012 US1).

A unit of work runs in a fresh sandbox with no memory of previous runs, so
without this section each increment rediscovers (or re-implements) what its
siblings already shipped. The delivery + settlement rows have always recorded
the answer; nothing read them back into the worker's prompt.

Two rules shape what may cross this channel:

* **Only devclaw-CONTROLLED facts.** A delivery body (``engine._task_detail``)
  interleaves devclaw's own lines — ``PR:``, ``Verify gate ...:``, ``Error:`` —
  with the worker's free-text ``Agent summary:``. Only the former are fed
  forward: one worker's unverified self-report must never become the next
  worker's premise (#358, and the same reasoning as the dispatch-time ref
  re-stamp). The terminal status comes from ``goal_settlements``, not from
  guessing at the body's shape.
* **Bounded.** The section is re-sent with EVERY increment (FR-009a), so its
  cost multiplies over a saga's life; entries are one compact line and the whole
  section is tail-kept under ``prompt_budget.PRIOR_INCREMENTS_KEEP`` (FR-009b).

Pure and never-raises: a malformed record renders a stated gap rather than
wedging a dispatch (constitution VI).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..advance_brief import PRIOR_INCREMENTS_MARKER
from .prompt_budget import cap_prior_increments

#: devclaw-generated lines inside a delivery body (``engine._task_detail``).
#: The worker's ``Agent summary:`` block is deliberately absent from this set.
_PR_RE = re.compile(r"^PR:\s*(\S+)", re.MULTILINE)
_GATE_RE = re.compile(r"^Verify gate\s+`[^`]*`:\s*(PASSED|FAILED)", re.MULTILINE)
_ERROR_RE = re.compile(r"^Error:\n(.*)", re.MULTILINE | re.DOTALL)

#: Per-entry error budget — enough to identify the failure, far short of the
#: 1500-char blob the delivery body carries.
_ERROR_CAP = 200

_UNREADABLE = "- (1 increment's record was unreadable — treat its outcome as unknown)"


@dataclass(frozen=True)
class IncrementRecord:
    """One settled increment, reduced to the facts devclaw itself owns."""

    objective: str
    status: "str | None" = None
    gate: "str | None" = None
    pr_url: "str | None" = None
    error: "str | None" = None
    readable: bool = True


def parse_record(
    instruction: str, body: str, status: "str | None",
) -> IncrementRecord:
    """Reduce one delivery row (+ its joined settlement status) to an
    :class:`IncrementRecord`, keeping ONLY devclaw-generated fields.

    Never raises: anything unexpected yields ``readable=False`` so the renderer
    can state the gap instead of dropping the increment silently."""
    try:
        objective = (instruction or "").strip()
        text = body or ""
        if not objective and not text:
            return IncrementRecord(objective="", readable=False)
        pr = _PR_RE.search(text)
        gate = _GATE_RE.search(text)
        err = _ERROR_RE.search(text)
        error = None
        if err:
            error = " ".join(err.group(1).split())[:_ERROR_CAP].strip() or None
        return IncrementRecord(
            objective=objective or "(objective not recorded)",
            status=(status or None),
            gate=(gate.group(1) if gate else None),
            pr_url=(pr.group(1) if pr else None),
            error=error,
        )
    except Exception:  # noqa: BLE001 — a parse hiccup must never wedge a dispatch
        return IncrementRecord(objective="", readable=False)


def _entry(rec: IncrementRecord) -> str:
    if not rec.readable:
        return _UNREADABLE
    parts = [f"- {rec.objective} →"]
    parts.append(f"status={rec.status or 'unrecorded'}")
    if rec.gate:
        parts.append(f"gate={rec.gate}")
    if rec.pr_url:
        parts.append(f"PR={rec.pr_url}")
    if rec.error:
        parts.append(f"error={rec.error}")
    return " ".join(parts)


def render(records: "list[IncrementRecord]") -> str:
    """The prior-increments section of the advance brief.

    ALWAYS returns a non-blank section: with no prior increments it states the
    absence explicitly rather than omitting the section, so the worker is never
    left to infer whether it is the first (FR-004)."""
    n = len(records)
    lines = [PRIOR_INCREMENTS_MARKER + f" — what earlier units of work delivered ({n} settled):"]
    if n == 0:
        lines.append(
            "No prior increment has settled in this goal — this is the first. "
            "Nothing has been delivered yet; do not assume any part of the goal "
            "is already built."
        )
        return "\n".join(lines)
    lines.append(
        f"This is increment {n + 1} of this goal. Build ON the increments below "
        "that are recorded as shipped; do NOT re-implement them. An entry with "
        "status=failed or gate=FAILED did NOT land — its work is not in the "
        "tree, and repeating that attempt unchanged will fail the same way. "
        "These are devclaw's own settlement records, not the workers' summaries."
    )
    # Cap the ENTRY LIST only, never the assembled section: the budget
    # tail-keeps, and the marker line sits at the HEAD — capping the whole
    # section would eat the marker every detector keys off (#547/#550) along
    # with the framing that makes the entries mean anything.
    entries = cap_prior_increments("\n".join(_entry(r) for r in records))
    return "\n".join(lines + [entries])
