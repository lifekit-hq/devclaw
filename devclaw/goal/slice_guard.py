"""Build-ahead slice guardrail (SDLC execution pipeline).

A well-sliced nightly increment closes ONE story-slice and ships as one
reviewable PR. An increment that flips **more than one** checkbox from ``- [ ]``
to ``- [x]`` built ahead into later work instead of slicing — the "Ledger"
17k-line-PR class this guardrail exists to catch.

Since spec 008 (US1, FR-005) the build-ahead signal is sourced from the speckit
execution contract — the per-feature ``specs/*/tasks.md`` files
(:func:`tasks_flips_sync`).

Both pieces are ZERO-token (pure git + string parse, never an LLM call):

* :func:`count_slice_advances` — the pure counter. String in, count out; it
  reads speckit task rows and counts how many distinct ``(feature, story)``
  slices went unchecked→checked between two ``tasks.md`` snapshots. It never
  raises: an absent or garbled contract on either side contributes nothing, so
  the count fails toward 0 (never trips).
* :func:`tasks_flips_sync` — the best-effort I/O wrapper: reads every tracked
  ``specs/*/tasks.md`` at ``HEAD`` and at its first parent and sums the counter
  across them. Any git hiccup / no contract / no parent commit ⇒ 0 (fail-OPEN
  on DETECTION).

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


# ---- speckit tasks.md substitution (spec 008 US1, FR-005) ------------------
#
# The build-ahead signal is sourced from the per-feature speckit
# ``specs/*/tasks.md`` files. speckit tasks.md is FINE-GRAINED: one story-slice
# (``[US<n>]``) is MANY task rows (``T001``…), so the unit that must not exceed
# one per increment is the STORY-SLICE, not the raw checkbox — closing five
# ``T00x [US1]`` rows is ONE reviewable slice, not a build-ahead. An increment
# that advances more than one distinct ``(feature, story)`` slice built ahead
# into later stories (the "Ledger" 17k-line-PR class). Pure git + string parse,
# ZERO-token, settle-time, fail-OPEN on detection.

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
    best-effort, never raises. An empty result means the repo carries no speckit
    contract, so there is no build-ahead unit to police."""
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
    there. Best-effort; any hiccup ⇒ False."""
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
    building ahead). A repo with NO ``specs/*/tasks.md`` has no contract to
    police and reads 0. ZERO-token (pure git + string parse),
    settle-time, and fail-OPEN: any git hiccup / absent contract / no parent
    commit reads as 0. Never raises. The VERDICT it feeds (advise under
    ``trust`` / block under ``strict``) is unchanged."""
    tasks_files = _tracked_tasks_files_sync(workspace_dir, "HEAD")
    if not tasks_files:
        return 0  # no speckit contract — nothing to police (fail-open)
    if not _has_parent_sync(workspace_dir):
        return 0  # first commit — no increment to police (fail-open)
    total = 0
    for path in tasks_files:
        after = _file_at_ref_sync(workspace_dir, "HEAD", path)
        before = _file_at_ref_sync(workspace_dir, "HEAD^", path)
        total += count_slice_advances(before, after)
    return total


def speckit_feature_state_sync(workspace_dir: str) -> "tuple[int, int, int]":
    """Working-tree speckit feature state: ``(total_dirs, graded, active)``.

    * ``total_dirs`` — count of ``specs/*/`` directories that carry a
      ``spec.md`` or a ``tasks.md`` (i.e. they look like speckit feature dirs,
      not incidental subdirs like ``specs/tiny/``).
    * ``graded`` — subset that have a ``tasks.md`` (the speckit plan step has
      run; the spec is ready for implementation).
    * ``active`` — subset of graded dirs whose ``tasks.md`` contains at least
      one unchecked task (work is still pending in this feature).

    Zero-token (pure working-tree fs read), best-effort / never-raises.
    Returns ``(0, 0, 0)`` on any failure or when no feature dirs exist.
    Used as the dispatch-boundary enforcement gate (issue #679):
    a dispatch is gated on ``total_dirs == 0`` (first dispatch, allow) OR
    ``active == 1`` (exactly one active feature, allow); everything else
    is a speckit contract violation."""
    try:
        base = os.path.join(workspace_dir, "specs")
        if not os.path.isdir(base):
            return 0, 0, 0
        entries = [d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d))]
    except Exception:  # noqa: BLE001 — best-effort, never raises
        return 0, 0, 0

    total = 0
    graded = 0
    active = 0
    for entry in entries:
        dir_path = os.path.join(base, entry)
        has_spec = os.path.isfile(os.path.join(dir_path, "spec.md"))
        has_tasks = os.path.isfile(os.path.join(dir_path, "tasks.md"))
        if not has_spec and not has_tasks:
            continue  # not a speckit feature dir (e.g. specs/tiny/)
        total += 1
        if not has_tasks:
            continue  # spec.md only — not yet graded (no tasks.md)
        graded += 1
        try:
            content = open(os.path.join(dir_path, "tasks.md"),  # noqa: WPS515
                           encoding="utf-8", errors="replace").read()
            if any(not checked for (_k, _s, checked) in _task_rows(content)):
                active += 1
        except Exception:  # noqa: BLE001 — best-effort
            pass
    return total, graded, active


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
