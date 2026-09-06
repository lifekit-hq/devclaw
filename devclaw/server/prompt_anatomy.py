"""Byte-anatomy of a cognition prompt — what actually fills the 105 KB.

A cognition prompt (planner / decomposer / evaluator / firming) is assembled as
an instruction template followed by ``## ``-delimited sections, several of which
are *re-fed goal state* — the event log, the delivery record, a captured worker
transcript, a fresh repo scan — rather than instructions. When such a prompt
times out or OOMs, the question is never "how big" but "big with WHAT": is the
bloat the instructions we wrote, or the history we push back in on every
stateless tick? This decoder answers that.

Pure, no I/O, never-raises — the same contract as ``worker_events.decode_event``.
No new capture is needed: the full prompt is already saved verbatim by
``loom.trace.write_transcript``; this reconstructs the section sizes post-hoc
from the ``## `` structure the ``build_prompt`` assemblers emit, and classifies
each section as **instruction** (what we tell the model) vs **data** (goal
state/output re-fed into the prompt).

The one subtlety it must get right: a re-fed section — the captured worker
transcript especially — contains its OWN ``## `` sub-headers (``## Per-clause
evidence`` …), and the instruction templates carry ``## PROCEDURE`` / ``##
Response`` headings of their own. A naive ``## `` split would shred the very
section we care about. So we split ONLY on the known top-level headers the
assemblers actually append (:data:`_KNOWN`); every other ``## `` stays absorbed
into its parent section, where it belongs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The top-level section headers the ``goal/*.py`` ``build_prompt`` assemblers
#: append, matched case-insensitively as a prefix of the ``## `` header text.
#: Each maps to ``(category, data_kind)``: ``data`` sections are re-fed goal
#: state/output (the removable/relocatable bloat); ``instruction`` sections are
#: the ask + the framing we author. Anything NOT listed here is treated as a
#: continuation of the current section (a template sub-heading, or a ``## `` that
#: lives inside a re-fed transcript) — never its own top-level segment.
_KNOWN: tuple[tuple[str, str, str | None], ...] = (
    # the ask + author-written framing → instruction
    ("goal", "instruction", None),
    ("context: this is the done-gate", "instruction", None),
    ("standing-goal contract", "instruction", None),
    ("conventions to follow", "instruction", None),
    ("blockers", "instruction", None),
    ("descoped", "instruction", None),
    # re-fed goal state / output → data (the push we want to shrink)
    ("current state", "data", "state"),
    ("recent history", "data", "log"),          # planner: "Recent history (log)"
    ("recent event log", "data", "log"),        # evaluator
    ("repository context", "data", "repo_context"),
    ("repo digest", "data", "repo_digest"),
    ("checklist", "data", "checklist"),
    ("the action that just finished", "data", "engine_result"),
    ("new steering", "data", "steering"),
    ("agreed spec", "data", "spec"),
    ("what has actually shipped", "data", "deliveries"),
    ("fresh read-only review", "data", "review_report"),
)

_HEADER_RE = re.compile(r"(?m)^## (.+)$")


@dataclass(frozen=True)
class Section:
    """One contiguous span of the prompt, keyed on its top-level ``## `` header
    (or the preamble). ``chars`` is its byte length; ``category`` is
    ``instruction`` | ``data``; ``data_kind`` names the re-fed source for data
    sections (``log``, ``deliveries``, …) and is ``None`` for instructions."""

    header: str
    chars: int
    category: str
    data_kind: str | None


@dataclass(frozen=True)
class Anatomy:
    total_chars: int
    instruction_chars: int
    data_chars: int
    sections: list[Section]  # document order


def _match_known(header: str) -> tuple[str, str | None] | None:
    h = header.strip().lower()
    for prefix, category, kind in _KNOWN:
        if h.startswith(prefix):
            return category, kind
    return None


def _label(header: str) -> str:
    """A short display label: drop the parenthetical gloss the assemblers append
    (``Repository context (facts from …)`` → ``Repository context``)."""
    return re.split(r"\s+\(", header.strip(), maxsplit=1)[0].strip()


def anatomize(prompt: str, role: str = "") -> Anatomy:
    """Segment ``prompt`` into its top-level sections and classify each. ``role``
    is accepted for symmetry and possible future per-role rules, but the header
    map is currently role-agnostic (the vocabularies don't collide). Never
    raises; a prompt with no known headers returns a single instruction span
    (graceful degradation for non-``## ``-shaped prompts like the review gate)."""
    prompt = prompt or ""
    total = len(prompt)
    boundaries = [m for m in _HEADER_RE.finditer(prompt) if _match_known(m.group(1))]

    sections: list[Section] = []
    if not boundaries:
        if total:
            sections.append(Section("(instruction template)", total, "instruction", None))
        return Anatomy(total, total, 0, sections)

    # Everything before the first known header is the instruction template
    # (+ any framing appended before the first section, e.g. the untrusted note).
    head_end = boundaries[0].start()
    if head_end > 0:
        sections.append(Section("(instruction template)", head_end, "instruction", None))

    for i, m in enumerate(boundaries):
        start = m.start()
        end = boundaries[i + 1].start() if i + 1 < len(boundaries) else total
        category, kind = _match_known(m.group(1))  # type: ignore[misc]
        sections.append(Section(_label(m.group(1)), end - start, category, kind))

    instruction = sum(s.chars for s in sections if s.category == "instruction")
    data = sum(s.chars for s in sections if s.category == "data")
    return Anatomy(total, instruction, data, sections)


def to_dict(a: Anatomy) -> dict:
    """JSON shape for the console route (camelCase to match ``api.ts``)."""
    return {
        "totalChars": a.total_chars,
        "instructionChars": a.instruction_chars,
        "dataChars": a.data_chars,
        "sections": [
            {
                "header": s.header,
                "chars": s.chars,
                "category": s.category,
                "dataKind": s.data_kind,
            }
            for s in a.sections
        ],
    }
