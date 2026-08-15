"""Build-ahead slice guardrail (SDLC execution pipeline).

A well-sliced nightly increment closes ONE story-slice and ships as one
reviewable PR. An increment that flips **more than one** checkbox from ``- [ ]``
to ``- [x]`` built ahead into later work instead of slicing — the "Ledger"
17k-line-PR class this guardrail exists to catch.

Since spec 008 (US1, FR-005) the build-ahead signal is sourced from the speckit
execution contract — the per-feature ``specs/*/tasks.md`` files
(:func:`tasks_flips_sync`) — with the legacy single-``PLAN.md`` ``## Milestones``
reader (:func:`mega_dump_flips_sync`) kept only as the D4 fallback for repos not
yet migrated to speckit (removed by US4/shrink).

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

import glob
import os
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
    trips on a real, observed ``>1`` mega-dump. Never raises.

    LEGACY fallback (spec 008 / D4): once a repo uses speckit its execution
    contract is ``specs/*/tasks.md``, not ``PLAN.md`` — the settle path calls
    :func:`tasks_flips_sync`, which only reaches this reader when NO ``tasks.md``
    exists anywhere (an un-migrated ``PLAN.md``-spine repo). Removed by the US4
    migration / shrink slice."""
    after = _plan_at_ref_sync(workspace_dir, "HEAD")
    before = _plan_at_ref_sync(workspace_dir, "HEAD^")
    return count_milestone_flips(before, after)


# ---- speckit tasks.md substitution (spec 008 US1, FR-005) ------------------
#
# The build-ahead signal moved from a single repo ``PLAN.md`` (``## Milestones``
# section) to the per-feature speckit ``specs/*/tasks.md`` files. A well-sliced
# increment checks off ONE story-slice; an increment that flips MORE than one
# ``- [ ]`` → ``- [x]`` across the tasks.md files built ahead into later stories.
# Same shape as the PLAN.md reader — pure git + string parse, ZERO-token,
# settle-time, fail-OPEN on detection — sourced from the speckit contract.

#: A tracked ``specs/<feature>/tasks.md`` path (POSIX, git's own separator).
_TASKS_PATH_RE = re.compile(r"^specs/[^/]+/tasks\.md$")


def _checkbox_states(text: str) -> "dict[str, bool]":
    """Map each markdown checkbox line (keyed by its trimmed label) to whether it
    is checked. Unlike :func:`_milestone_states`, tasks.md is ALL task items —
    every checkbox in the file counts, no section scoping. Pure and total."""
    states: "dict[str, bool]" = {}
    for line in (text or "").splitlines():
        m = _CHECKED.match(line)
        if m:
            states[m.group(1).strip()] = True
            continue
        m = _UNCHECKED.match(line)
        if m:
            states.setdefault(m.group(1).strip(), False)
    return states


def count_checkbox_flips(before: str, after: str) -> int:
    """How many checkbox items went ``[ ]``→``[x]`` between two ``tasks.md``
    snapshots. Matched by trimmed label; a newly-added-already-checked item
    (absent from ``before``) is NOT a flip. Pure; never raises — a garbled file
    fails toward 0 flips (fail-OPEN, can only under-count)."""
    before_states = _checkbox_states(before)
    after_states = _checkbox_states(after)
    flips = 0
    for label, checked_after in after_states.items():
        if checked_after and before_states.get(label) is False:
            flips += 1
    return flips


def _tracked_tasks_files_sync(workspace_dir: str, ref: str = "HEAD") -> "list[str]":
    """The ``specs/*/tasks.md`` paths tracked at ``ref``, via ``git ls-tree``.
    ``[]`` on any hiccup (not a repo, git missing, timeout, unknown ref) —
    best-effort, never raises. Empty result is the signal to fall back to the
    legacy PLAN.md reader (D4)."""
    try:
        p = subprocess.run(
            ["git", "-C", workspace_dir, "ls-tree", "-r", "--name-only", ref],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    return [ln.strip() for ln in p.stdout.splitlines() if _TASKS_PATH_RE.match(ln.strip())]


def _file_at_ref_sync(workspace_dir: str, ref: str, path: str) -> str:
    """File content at ``ref:path`` via ``git show``, or ``""`` on any hiccup
    (no such path at that ref, no parent commit, git missing). Best-effort."""
    try:
        p = subprocess.run(
            ["git", "-C", workspace_dir, "show", f"{ref}:{path}"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout if p.returncode == 0 else ""


def tasks_flips_sync(workspace_dir: str) -> int:
    """Best-effort count of ``[ ]``→``[x]`` flips the increment at ``HEAD`` (vs
    its first parent) introduced across ALL ``specs/*/tasks.md`` in
    ``workspace_dir`` — the speckit build-ahead signal (FR-005).

    Reads each tracked ``tasks.md`` at ``HEAD`` and ``HEAD^`` and sums
    :func:`count_checkbox_flips`. When NO ``specs/*/tasks.md`` exists at all,
    falls back to the legacy :func:`mega_dump_flips_sync` (PLAN.md) — the D4
    dual-read transition, removed by US4/shrink. ZERO-token (pure git + string
    parse), settle-time, and fail-OPEN: any git hiccup / absent contract / no
    parent commit reads as 0 flips. Never raises. The VERDICT it feeds
    (advise under ``trust`` / block under ``strict``) is unchanged — only the
    source file moved from ``PLAN.md`` to ``tasks.md``."""
    tasks_files = _tracked_tasks_files_sync(workspace_dir, "HEAD")
    if not tasks_files:
        return mega_dump_flips_sync(workspace_dir)  # legacy PLAN.md fallback (D4)
    total = 0
    for path in tasks_files:
        after = _file_at_ref_sync(workspace_dir, "HEAD", path)
        before = _file_at_ref_sync(workspace_dir, "HEAD^", path)
        total += count_checkbox_flips(before, after)
    return total


def current_feature_dir_sync(workspace_dir: str) -> str:
    """The smallest not-yet-complete speckit feature directory — the first
    ``specs/NNN-*/`` (lexical order) whose ``tasks.md`` still has an unchecked
    item — as a workspace-relative POSIX path (e.g. ``specs/012-widget``), or
    ``""`` when there is none.

    Reads the WORKING TREE (the goal branch is checked out at dispatch), pure fs
    + string parse, best-effort — any hiccup degrades to ``""``. Recorded on the
    goal at dispatch so the done-gate can ground on the right ``spec.md`` (D6).
    Never raises."""
    try:
        matches = sorted(
            glob.glob(os.path.join(workspace_dir, "specs", "*", "tasks.md"))
        )
    except Exception:  # noqa: BLE001 — detection is best-effort
        return ""
    for tasks_path in matches:
        try:
            text = open(tasks_path, "r", encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        states = _checkbox_states(text)
        if any(not checked for checked in states.values()):
            rel = os.path.relpath(os.path.dirname(tasks_path), workspace_dir)
            return rel.replace(os.sep, "/")
    return ""
