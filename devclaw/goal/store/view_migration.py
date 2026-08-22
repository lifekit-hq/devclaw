"""One migration, one cutoff — the generated views stop being read back.

``STATUS.md`` / ``log.md`` / ``inbox.md`` / ``deliveries.md`` are generated
VIEWS. The constitution's single-writer principle says they are written and
never read back for a decision, and until #617 the store violated that on
every read: ``_ingest_log`` / ``_ingest_deliveries`` / ``_ingest_inbox`` /
``_ensure_status_row`` parsed the markdown back into rows from eight read
paths. Framed as lazy one-shot migrations, they had no cutoff, so they were
permanent code — and a second writer to goal state that ``GoalStore
.transition()``'s CAS choke point does not cover. A hand-edited ``inbox.md``
became steering; a corrupt ``deliveries.md`` became delivery history.

This module is where that ingest went. It runs ONCE per database, at
:class:`~devclaw.goal.store.base.GoalStore` construction, before any read
path can observe the tables — then stamps :data:`MIGRATION_META_KEY` and
never runs again. Afterwards every view on disk is write-only: editing one
by hand changes nothing any decision reads.

The parsers below are the pre-#617 lazy-ingest bodies moved here verbatim in
behaviour (demolition is relocation, not deletion). They are deliberately
tolerant — a truncated or garbled view degrades field-by-field to defaults
rather than raising, exactly as the lazy readers did, because the migration
must never be the thing that stops an instance from starting. What is NOT
tolerated is running twice: every step is guarded on "this table already has
rows for this goal", so a crash mid-sweep resumes cleanly on the next start
(the marker is stamped only after the whole sweep completes).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from ..models import GoalStatus, InFlight

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..state import GoalState
    from ...state_store import StateStore

#: Stamped in the ``meta`` table once the sweep completes. Its presence — not
#: any per-goal condition — is what makes the migration one-shot.
MIGRATION_META_KEY = "goal_view_migration_done_at_ms"

_FRONTMATTER = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

#: settle-line arrow scan for the settlement seed — the token immediately
#: before " → " on a "- [ts] ... <token> → <status>" log line. Deliberately
#: loose (matches ANY " x → y" substring) so it over-captures exactly what the
#: pre-PR7 ``log_contains(f" {id} → ")`` guard answered True for: readopt and
#: sweep decisions must be IDENTICAL across the migration.
_SETTLE_ARROW_RE = re.compile(r" (\S+) → (\S+)")

_DELIVERY_HEAD = re.compile(r"^## \[", re.MULTILINE)
_DELIVERY_HEAD_LINE = re.compile(r"^## \[[^\]]*\]\s*(.*)$")


# ---- pure parsers (one per view) ------------------------------------------


def read_frontmatter(text: str) -> dict:
    """The YAML frontmatter of a view as a dict, or ``{}`` for anything that
    isn't parseable as one. Never raises."""
    m = _FRONTMATTER.match(text or "")
    if not m:
        return {}
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def parse_status_md(text: str) -> "tuple[GoalStatus, int]":
    """A ``STATUS.md``'s frontmatter as ``(status, inbox_cursor)``.

    ``inbox_cursor`` comes back separately because it is not a
    :class:`GoalStatus` field any more — it was the ingest boundary for
    ``inbox.md``, and with the ingest gone the only thing that still needs it
    is this migration, to decide which historical steering lines were already
    acted on. Degrades field-by-field to defaults on a truncated/garbled file,
    never raises."""
    fm = read_frontmatter(text)
    inflight = None
    raw_inflight = fm.get("in_flight")
    if isinstance(raw_inflight, dict):
        try:
            inflight = InFlight(
                engine=raw_inflight["engine"], tool=raw_inflight["tool"],
                id=raw_inflight["id"], ref_kind=raw_inflight["ref_kind"],
                goal=raw_inflight.get("goal", ""),
                is_done_check=bool(raw_inflight.get("is_done_check", False)),
            )
        except KeyError:
            inflight = None
    raw_history = fm.get("phase_history") or []
    history: "tuple[dict, ...]" = tuple(
        {"phase": str(e.get("phase")), "at": str(e.get("at"))}
        for e in raw_history
        if isinstance(e, dict) and e.get("phase") and e.get("at")
    )
    status = GoalStatus(
        phase=fm.get("phase", "idle"),
        lifecycle=fm.get("lifecycle") or None,
        in_flight=inflight,
        blocked_on=fm.get("blocked_on") or None,
        blocked_kind=fm.get("blocked_kind", "") or "",
        heal_attempts=_int(fm.get("heal_attempts")),
        next_heal_at=fm.get("next_heal_at") or None,
        next=fm.get("next", "") or "",
        last_plan_at=fm.get("last_plan_at") or None,
        last_tick_at=fm.get("last_tick_at") or None,
        actions_dispatched=_int(fm.get("actions_dispatched")),
        last_eval_verdict=fm.get("last_eval_verdict") or None,
        last_eval_at=fm.get("last_eval_at") or None,
        last_eval_note=fm.get("last_eval_note", "") or "",
        last_progress_at=fm.get("last_progress_at") or None,
        no_progress_notified=bool(fm.get("no_progress_notified", False)),
        open_unmerged_pr=fm.get("open_unmerged_pr") or None,
        phase_history=history,
    )
    return status, _int(fm.get("inbox_cursor"))


def _int(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def parse_log_md(text: str) -> "list[str]":
    """Every log line of a ``log.md`` body, verbatim and in file order — the
    same ``- [`` filter the pre-PR6 file-tail read used."""
    return [ln for ln in text.splitlines() if ln.startswith("- [")]


def split_delivery_sections(text: str) -> "list[tuple[str, str]]":
    """A ``deliveries.md`` body as ``(instruction, block)`` pairs, split on
    lines starting ``## [`` — the exact boundary ``append_delivery`` writes.
    Text before the first match (the file header) is dropped on purpose; each
    ``block`` runs from its ``## [`` line up to the next one, so
    ``"".join(blocks)`` reconstructs the original byte-for-byte."""
    starts = [m.start() for m in _DELIVERY_HEAD.finditer(text)]
    sections: "list[tuple[str, str]]" = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        block = text[start:end]
        head_line = block.splitlines()[0] if block else ""
        m = _DELIVERY_HEAD_LINE.match(head_line)
        sections.append((m.group(1) if m else "", block))
    return sections


def parse_inbox_md(text: str) -> "list[str]":
    """Every steering line of an ``inbox.md`` body — non-blank, non-header,
    stripped, in file order."""
    out = []
    for ln in text.splitlines():
        s = ln.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _read(path: Path) -> "str | None":
    """A view's text, or ``None`` when it is absent or unreadable. An
    unreadable view is skipped, not fatal: the migration must never be the
    reason an instance fails to start."""
    try:
        return path.read_text()
    except (OSError, UnicodeDecodeError):
        return None


# ---- the sweep -------------------------------------------------------------


def migrate_views_once(
    state: "StateStore", goal_state: "GoalState", root: Path, now_ms: int,
) -> int:
    """Ingest every on-disk view into its owning table ONCE, then stamp the
    database migrated. Returns the number of goals whose views were read
    (0 when the marker is already set, which is every call after the first).

    Ordering within a goal is load-bearing: status first (its frontmatter
    carries the inbox cursor the steering step needs), then the log, then the
    settlements seeded from those log rows, then deliveries, then the inbox.

    Every subdirectory is swept, not only those with a ``goal.yaml``. This is
    the last chance to read these files, so the sweep is deliberately wider
    than :meth:`GoalStore.list_goal_ids`: a goal dir whose ``goal.yaml`` was
    lost to a crash still has its state ingested rather than silently dropped
    at the cutoff. Dirs with no views at all cost one ``exists`` check each.
    """
    if state.get_meta(MIGRATION_META_KEY):
        return 0
    migrated = 0
    if root.exists():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            _migrate_one(goal_state, d, d.name, now_ms)
            migrated += 1
    # Stamped only after the whole sweep: a crash part-way through leaves the
    # marker unset, and the per-goal row guards make the retry a resume.
    state.set_meta(MIGRATION_META_KEY, str(int(now_ms)))
    return migrated


def _migrate_one(goal_state: "GoalState", d: Path, goal_id: str, now_ms: int) -> None:
    cursor = _migrate_status(goal_state, d, goal_id)
    _migrate_log(goal_state, d, goal_id, now_ms)
    _seed_settlements(goal_state, goal_id, now_ms)
    _migrate_deliveries(goal_state, d, goal_id, now_ms)
    _migrate_inbox(goal_state, d, goal_id, cursor, now_ms)


def _migrate_status(goal_state: "GoalState", d: Path, goal_id: str) -> int:
    """Seed ``goal_status`` + ``goal_phase_history`` from ``STATUS.md``.
    Returns the inbox cursor to use for this goal's steering step — read from
    the existing row when there is one, else from the file's frontmatter."""
    if goal_state.has_status(goal_id):
        return goal_state.read_inbox_ingest_cursor(goal_id)
    text = _read(d / "STATUS.md")
    if text is None:
        return 0
    status, cursor = parse_status_md(text)
    goal_state.write_status(goal_id, status)
    goal_state.seed_phase_history(goal_id, status.phase_history)
    return cursor


def _migrate_log(goal_state: "GoalState", d: Path, goal_id: str, now_ms: int) -> None:
    if goal_state.has_log_rows(goal_id):
        return
    text = _read(d / "log.md")
    if text is None:
        return
    lines = parse_log_md(text)
    if lines:
        goal_state.append_log_rows(goal_id, lines, now_ms)


def _seed_settlements(goal_state: "GoalState", goal_id: str, now_ms: int) -> None:
    """Seed ``goal_settlements`` from historical ``goal_log`` rows — the goals
    that settled work before ``goal_settlements`` existed. Over-captures on
    purpose (see :data:`_SETTLE_ARROW_RE`)."""
    if goal_state.has_any_settlements(goal_id):
        return
    for line in goal_state.all_log_rows(goal_id):
        m = _SETTLE_ARROW_RE.search(line)
        if m:
            goal_state.record_settlement(goal_id, m.group(1), None, m.group(2), now_ms)


def _migrate_deliveries(goal_state: "GoalState", d: Path, goal_id: str, now_ms: int) -> None:
    if goal_state.has_delivery_rows(goal_id):
        return
    text = _read(d / "deliveries.md")
    if text is None:
        return
    for instruction, block in split_delivery_sections(text):
        # ref_id NULL: these sections predate the idempotency key and have
        # nothing to dedupe against. #616 backfills them and makes the column
        # NOT NULL; until then they are the reason it is nullable.
        goal_state.append_delivery_row(goal_id, None, block, now_ms, instruction=instruction)


def _migrate_inbox(
    goal_state: "GoalState", d: Path, goal_id: str, cursor: int, now_ms: int,
) -> None:
    """Seed ``goal_steering`` from ``inbox.md``. Lines at or below the stored
    ingest cursor were already acted on before ``goal_steering`` existed —
    they are preserved as CONSUMED so the record survives without ever being
    replayed to a planner. Everything past the cursor becomes unread steering,
    which is the last time a hand-typed line can enter the store: after this
    migration, steering arrives only through ``steer_goal``."""
    if goal_state.has_steering_rows(goal_id):
        return
    text = _read(d / "inbox.md")
    if text is None:
        return
    lines = parse_inbox_md(text)
    if not lines:
        return
    history, fresh = lines[:cursor], lines[cursor:]
    if history:
        goal_state.append_steering_rows(
            goal_id, history, source="manual", created_at_ms=now_ms, consumed=True,
        )
    if fresh:
        goal_state.append_steering_rows(goal_id, fresh, source="manual")
