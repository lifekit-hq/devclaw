"""The thin-advance pull-brief — shared marker + human-display helpers.

The brief (``goal/tick._advance_brief``) is DISPATCH PLUMBING: the worker's
instruction for one long_lived advance session. Two rules follow from that,
and this module is the single place both sides read them from:

* the WORKER receives the full brief verbatim — it is the machine-facing
  channel and must stay byte-identical;
* every HUMAN-FACING rendering (a PR title/body, the goal's ``next`` hint,
  the goal-log head, delivery records and their labels) shows the goal's
  embedded objective instead — the brief there reads as its own dispatch
  instructions, not as what the goal is doing (#547 delivery half, #550
  display half).

Deliberately dependency-free so both ``devclaw.goal`` and ``devclaw.delivery``
can import it without a cycle; the marker constant keeps the brief GENERATOR
and every DETECTOR in lockstep.
"""

from __future__ import annotations

import re

# The brief's opening-line prefix. goal/tick._advance_brief builds its first
# line from this constant; is_advance_brief matches on it — they cannot drift.
ADVANCE_BRIEF_MARKER = "Advance this goal by one substantive"

#: Shared section markers (same never-drift contract as ADVANCE_BRIEF_MARKER,
#: #547/#550): the brief GENERATOR (goal/tick._advance_brief) and the display
#: choke point below both key off these exact strings.
STEERING_MARKER = "Steering from the owner — incorporate it:"
FAILURE_CONTEXT_MARKER = "Previous attempt did NOT ship"

#: The saga feed-forward section (spec 012 US1): what previously-settled
#: increments of THIS goal delivered and how each was judged. Same never-drift
#: contract — ``goal/prior_increments.render`` builds the section's opening line
#: from this constant and ``display_goal`` below detects on it.
PRIOR_INCREMENTS_MARKER = "Prior increments of this goal"

#: The count is rendered into the marker line so the display side can annotate
#: how many increments preceded this one WITHOUT re-reading the store. Generator
#: and detector share this pattern for the same never-drift reason as the
#: markers themselves.
_PRIOR_INCREMENTS_COUNT = re.compile(
    re.escape(PRIOR_INCREMENTS_MARKER) + r"[^\n(]*\((\d+) settled\)"
)

# Rendered when a brief carries no parseable ``Goal:`` line — still never the
# raw brief.
_FALLBACK_LABEL = "advance the goal by one increment"


def is_advance_brief(text: str) -> bool:
    """True when ``text`` is the thin-advance pull-brief (matched on its
    opening line)."""
    return text.strip().startswith(ADVANCE_BRIEF_MARKER)


def objective_from_brief(text: str) -> str:
    """Pull the ``Goal: <objective>`` line out of an advance-brief — the
    human-usable statement of what the goal pursues. Empty string when the
    brief carries none."""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("Goal:"):
            return s[len("Goal:") :].strip()
    return ""


def display_goal(text: str) -> str:
    """The human-facing form of an action's ``goal`` text: an advance brief
    renders as its embedded objective; any other goal text passes through
    unchanged (one-shot instructions describe themselves).

    The rendered label must not LIE about the brief: two dispatches with the
    same objective can differ in steering / failure context, and collapsing
    them to identical log lines made a steered re-dispatch indistinguishable
    from an amnesiac one (2026-08-19 night-run diagnosis). Annotate what the
    brief additionally carries."""
    if not is_advance_brief(text):
        return text
    label = objective_from_brief(text) or _FALLBACK_LABEL
    extras = []
    if STEERING_MARKER in text:
        n = len([l for l in text.split(STEERING_MARKER, 1)[1].splitlines() if l.strip()])
        extras.append(f"+{n} steering line(s)")
    if FAILURE_CONTEXT_MARKER in text:
        extras.append("+failure context")
    # Only annotate when there IS saga history to report. The section itself is
    # present on EVERY advance — a first increment is told so explicitly (spec
    # 012 FR-004) — so annotating its mere presence would decorate every
    # dispatch identically, which is the opposite of what these extras are for:
    # distinguishing dispatches that differ.
    m = _PRIOR_INCREMENTS_COUNT.search(text)
    if m and m.group(1) != "0":
        extras.append(f"+{m.group(1)} prior increment(s)")
    if extras:
        return f"{label}  [{', '.join(extras)}]"
    return label
