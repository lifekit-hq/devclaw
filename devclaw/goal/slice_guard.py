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
            capture_output=True, text=True, errors="replace", timeout=15,
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
# section — one checkbox per story-slice) to the per-feature speckit
# ``specs/*/tasks.md`` files. speckit tasks.md is FINE-GRAINED: one story-slice
# (``[US<n>]``) is MANY task rows (``T001``…), so the unit that must not exceed
# one per increment is the STORY-SLICE, not the raw checkbox — closing five
# ``T00x [US1]`` rows is ONE reviewable slice, not a build-ahead. An increment
# that advances more than one distinct ``(feature, story)`` slice built ahead
# into later stories (the "Ledger" 17k-line-PR class). Same shape as the PLAN.md
# reader — pure git + string parse, ZERO-token, settle-time, fail-OPEN on
# detection — sourced from the speckit contract.

#: A tracked ``specs/<feature>/tasks.md`` path (POSIX, git's own separator).
_TASKS_PATH_RE = re.compile(r"^specs/[^/]+/tasks\.md$")
#: A markdown checkbox line — capture the mark and the trailing text.
_TASK_LINE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<rest>.+?)\s*$")
#: The stable task id (``T001``) — flips key off THIS, never the free-text label,
#: so re-wording a task in the same commit it is checked still counts (a bare
#: label key silently drops that flip).
_TASK_ID = re.compile(r"\bT\d+\b")
#: The story-slice tag (``US1``) — the build-ahead UNIT (a slice ships as one PR).
_STORY_TAG = re.compile(r"\bUS\d+\b")


def _task_rows(text: str) -> "list[tuple[str, str | None, bool]]":
    """Parse a ``tasks.md`` into ``(key, story, checked)`` rows, one per checkbox
    line. ``key`` is the ``T<id>`` when present (stable across a same-commit
    re-word) else the trimmed label; ``story`` is the ``US<n>`` slice tag or
    ``None`` (setup/foundational/polish tasks carry none). Pure and total."""
    rows: "list[tuple[str, str | None, bool]]" = []
    for line in (text or "").splitlines():
        m = _TASK_LINE.match(line)
        if not m:
            continue
        rest = m.group("rest").strip()
        idm = _TASK_ID.search(rest)
        sm = _STORY_TAG.search(rest)
        key = idm.group(0) if idm else rest
        rows.append((key, sm.group(0) if sm else None, m.group("mark") in ("x", "X")))
    return rows


def count_slice_advances(before: str, after: str) -> int:
    """How many distinct STORY-SLICES a single ``tasks.md`` advanced between two
    snapshots — the speckit build-ahead unit (FR-005).

    A slice (``[US<n>]``) advances when at least one of its tasks is checked in
    ``after`` that was NOT already checked in ``before`` (keyed by task id, so a
    same-commit re-word still counts, and a brand-new ``tasks.md`` — absent in
    ``before`` — counts every checked slice as advanced). Completing MANY tasks
    of ONE story is one slice (not build-ahead); advancing two stories is two.
    Setup/foundational/polish tasks (no ``[US<n>]`` tag) never count as a slice
    on their own — they ride whatever story ships — UNLESS the file carries no
    story tags at all, in which case any advance collapses to a single unit (we
    cannot resolve stories, so we never over-trip). Pure; never raises — a
    garbled file fails toward 0 (fail-OPEN, can only under-count)."""
    before_checked = {k for (k, _s, c) in _task_rows(before) if c}
    after_rows = _task_rows(after)
    has_story = any(s for (_k, s, _c) in after_rows)
    advanced: "set[str]" = set()
    for key, story, checked in after_rows:
        if not checked or key in before_checked:
            continue  # not newly checked by this increment
        if has_story:
            if story:
                advanced.add(story)  # untagged tasks ride a story, never count alone
        else:
            advanced.add("")  # no story tags anywhere — one bucket, never over-trip
    return len(advanced)


def _tracked_tasks_files_sync(workspace_dir: str, ref: str = "HEAD") -> "list[str]":
    """The ``specs/*/tasks.md`` paths tracked at ``ref``, via ``git ls-tree``.
    ``[]`` on any hiccup (not a repo, git missing, timeout, unknown ref) —
    best-effort, never raises. Empty result is the signal to fall back to the
    legacy PLAN.md reader (D4)."""
    try:
        p = subprocess.run(
            ["git", "-C", workspace_dir, "ls-tree", "-r", "--name-only", ref],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if p.returncode != 0:
        return []
    return [ln.strip() for ln in p.stdout.splitlines() if _TASKS_PATH_RE.match(ln.strip())]


def _has_parent_sync(workspace_dir: str) -> bool:
    """Whether ``HEAD`` has a first parent (``HEAD^`` resolves) — i.e. there is a
    real increment (a commit against prior history) to police. A repo's very
    first commit has no parent and is NOT an increment: the guard fails open (0)
    there, exactly as the PLAN.md reader did. Best-effort; any hiccup ⇒ False."""
    try:
        p = subprocess.run(
            ["git", "-C", workspace_dir, "rev-parse", "--verify", "-q", "HEAD^"],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return p.returncode == 0


def _file_at_ref_sync(workspace_dir: str, ref: str, path: str) -> str:
    """File content at ``ref:path`` via ``git show``, or ``""`` on any hiccup
    (no such path at that ref, no parent commit, git missing). Best-effort."""
    try:
        p = subprocess.run(
            ["git", "-C", workspace_dir, "show", f"{ref}:{path}"],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout if p.returncode == 0 else ""


def tasks_flips_sync(workspace_dir: str) -> int:
    """Best-effort count of distinct ``(feature, story)`` build-ahead slices the
    increment at ``HEAD`` (vs its first parent) advanced across ALL
    ``specs/*/tasks.md`` in ``workspace_dir`` — the speckit build-ahead signal
    (FR-005). A well-sliced increment advances exactly ONE slice.

    Sums :func:`count_slice_advances` per feature file (each file's stories are
    distinct slices, and the same ``US1`` label in two different features is two
    different slices — genuinely advancing two features in one increment IS
    building ahead). When NO ``specs/*/tasks.md`` exists at all, falls back to
    the legacy :func:`mega_dump_flips_sync` (PLAN.md) — the D4 dual-read
    transition, removed by US4/shrink. ZERO-token (pure git + string parse),
    settle-time, and fail-OPEN: any git hiccup / absent contract / no parent
    commit reads as 0. Never raises. The VERDICT it feeds (advise under
    ``trust`` / block under ``strict``) is unchanged — the source moved from
    ``PLAN.md`` milestones to ``tasks.md`` story-slices, and the build-ahead
    UNIT with it."""
    tasks_files = _tracked_tasks_files_sync(workspace_dir, "HEAD")
    if not tasks_files:
        return mega_dump_flips_sync(workspace_dir)  # legacy PLAN.md fallback (D4)
    if not _has_parent_sync(workspace_dir):
        return 0  # first commit — no increment to police (fail-open, as PLAN.md did)
    total = 0
    for path in tasks_files:
        after = _file_at_ref_sync(workspace_dir, "HEAD", path)
        before = _file_at_ref_sync(workspace_dir, "HEAD^", path)
        total += count_slice_advances(before, after)
    return total


def current_feature_dir_sync(workspace_dir: str) -> str:
    """The speckit feature directory the goal is currently executing — the
    ``specs/NNN-*/`` whose ``tasks.md`` was **most recently modified** (the file
    the active increment touches) — as a workspace-relative POSIX path (e.g.
    ``specs/012-widget``), or ``""`` when there is none.

    Most-recent-mtime, tie-broken lexical-last (the higher feature number),
    tracks the feature actually being worked: robust when several features are
    incomplete (a stalled earlier ``specs/005`` no longer shadows the active
    ``specs/012``), and it does not mistake a placeholder/empty ``tasks.md`` for
    a finished one. Reads the WORKING TREE (the goal branch is checked out),
    pure fs, best-effort — any hiccup degrades to ``""``. Used to ground the
    done-gate on the right ``spec.md`` (D6 / FR-006). Never raises."""
    try:
        matches = glob.glob(os.path.join(workspace_dir, "specs", "*", "tasks.md"))
    except Exception:  # noqa: BLE001 — detection is best-effort
        return ""
    best: "tuple[float, str] | None" = None
    for tasks_path in matches:
        try:
            mtime = os.path.getmtime(tasks_path)
        except OSError:
            continue
        cand = (mtime, tasks_path)  # ties break on path → lexical-last wins
        if best is None or cand > best:
            best = cand
    if best is None:
        return ""
    rel = os.path.relpath(os.path.dirname(best[1]), workspace_dir)
    return rel.replace(os.sep, "/")
