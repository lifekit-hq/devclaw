"""Milestone-keyed mega-dump guardrail (SDLC execution pipeline, P2).

A well-sliced nightly increment closes ONE milestone and ships as one reviewable
PR. An increment that flips **more than one** ``## Milestones`` checkbox from
``- [ ]`` to ``- [x]`` in the goal's ``PLAN.md`` built ahead into later
milestones instead of slicing — the "Ledger" 17k-line-PR class this guardrail
exists to catch.

Two pieces, both ZERO-token (pure git + string parse, never an LLM call):

* :func:`count_milestone_flips` — the pure counter. String in, count out; it
  parses ONLY the ``## Milestones`` section (a milestone lives there, a *task*
  checkbox lives under ``## Tasks — …`` and is deliberately NOT counted) and
  reports how many milestones went unchecked→checked between two PLAN.md
  snapshots. It never raises: an absent or garbled section on either side simply
  contributes nothing, so the count fails toward 0 (never trips).
* :func:`mega_dump_flips_sync` — the best-effort I/O wrapper: reads PLAN.md at
  ``HEAD`` and at its first parent in a workspace and delegates to the counter.
  Any git hiccup / absent PLAN.md ⇒ 0 (fail-OPEN on DETECTION).

Detection fails OPEN; the VERDICT it feeds does not — the settle path dials the
count through the EXISTING gate policy (``quality.gate_policy.gate_consequence``):
under ``trust`` a trip ADVISES (loud log, ship anyway), under ``strict`` it
BLOCKS. This module owns only detection; it never reaches the goal store, never
decides the verdict, and never talks to an LLM.
"""

from __future__ import annotations

import re
import subprocess

#: The ``## Milestones`` section header (only this heading opens the section).
_MILESTONES_HEADER = re.compile(r"^\s*##\s+milestones\b", re.IGNORECASE)
#: Any ``## `` heading — closes the Milestones section (the skill's shape: the
#: section runs until the next ``##``).
_ANY_H2 = re.compile(r"^\s*##\s+\S")
#: A checked / unchecked markdown checkbox line, keyed by its trimmed label.
_CHECKED = re.compile(r"^\s*[-*]\s+\[[xX]\]\s+(.+?)\s*$")
_UNCHECKED = re.compile(r"^\s*[-*]\s+\[\s\]\s+(.+?)\s*$")


def _milestone_states(text: str) -> "dict[str, bool]":
    """Map each milestone checkbox line (keyed by its trimmed label) to whether
    it is checked, parsing ONLY the ``## Milestones`` section — from that header
    until the next ``##`` heading. A checkbox OUTSIDE that section (a task under
    ``## Tasks — …``) is ignored, so tasks never count as milestones. Pure and
    total: no Milestones section ⇒ ``{}``."""
    states: "dict[str, bool]" = {}
    in_section = False
    for line in (text or "").splitlines():
        if _MILESTONES_HEADER.match(line):
            in_section = True
            continue
        if not in_section:
            continue
        if _ANY_H2.match(line):
            break  # next section — Milestones is done
        m = _CHECKED.match(line)
        if m:
            states[m.group(1).strip()] = True
            continue
        m = _UNCHECKED.match(line)
        if m:
            # setdefault: a checked line already recorded True wins over a later
            # duplicate-label unchecked line (defensive; labels are unique in
            # practice).
            states.setdefault(m.group(1).strip(), False)
    return states


def count_milestone_flips(before: str, after: str) -> int:
    """How many ``## Milestones`` entries went ``[ ]``→``[x]`` between two
    PLAN.md snapshots.

    A milestone counts only when it was present-and-UNCHECKED in ``before`` and
    present-and-CHECKED in ``after`` (matched by its trimmed label). A milestone
    that is newly-added-and-already-checked in ``after`` (absent from ``before``)
    is NOT a flip — the plan grew, it wasn't ticked off. Tasks (checkboxes
    outside the Milestones section) never count. Pure; never raises — a garbled
    section fails toward 0 flips, so detection can only under-count, never
    over-trip (fail-OPEN)."""
    before_states = _milestone_states(before)
    after_states = _milestone_states(after)
    flips = 0
    for label, checked_after in after_states.items():
        if checked_after and before_states.get(label) is False:
            flips += 1
    return flips


def _plan_at_ref_sync(workspace_dir: str, ref: str) -> str:
    """PLAN.md content at ``ref`` in ``workspace_dir`` via ``git show``, or ``""``
    on any hiccup (not a repo, no PLAN.md at that ref, git missing, timeout).
    Best-effort — the detection wrapper fails open on an empty read."""
    try:
        p = subprocess.run(
            ["git", "-C", workspace_dir, "show", f"{ref}:PLAN.md"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout if p.returncode == 0 else ""


def mega_dump_flips_sync(workspace_dir: str) -> int:
    """Best-effort count of ``## Milestones`` flips introduced by the increment
    at ``HEAD`` (vs its first parent) in ``workspace_dir``.

    Reads PLAN.md at ``HEAD`` and ``HEAD^`` and delegates to
    :func:`count_milestone_flips`. ZERO-token (pure git + string parse) and
    fail-OPEN: any git hiccup, an absent PLAN.md, or a repo with no parent commit
    reads as 0 flips — the guardrail never trips on a detection error, it only
    trips on a real, observed ``>1`` mega-dump. Never raises."""
    after = _plan_at_ref_sync(workspace_dir, "HEAD")
    before = _plan_at_ref_sync(workspace_dir, "HEAD^")
    return count_milestone_flips(before, after)
