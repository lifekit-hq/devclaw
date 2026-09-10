"""The Decisions feed-forward section (spec 031 US4) — the sibling of
:mod:`prior_increments`.

Every current Decision — an owner's typed resolution, a timebox default, an
admission rewrite — reaches the next worker and the done-gate as
devclaw-controlled FACT, never as a worker's prose (#358 trust boundary), so
neither re-derives what the owner settled nor asks again. Superseded
Decisions are history and are not rendered.

Pure, never-raises, bounded by :func:`prompt_budget.cap_decisions`.
"""

from __future__ import annotations

from ..advance_brief import DECISIONS_MARKER
from .models import Decision
from .prompt_budget import cap_decisions

#: option keys → the owner-facing label used when the Decision picked one
_OPTION_LABELS = {
    "correct": "correct the implementation",
    "accept_close": "accept the gap and close",
    "split": "split into a follow-up",
    "supply": "supply the capability",
    "cancel": "cancel",
    "continue": "continue — the dispatch budget is refunded",
}


def _iso_ms(iso: "str | None") -> int:
    """Milliseconds since the epoch for a store ISO timestamp; ``0`` for
    none/unparseable so every Decision counts as made after it."""
    import datetime as _dt
    if not iso:
        return 0
    try:
        return int(_dt.datetime.fromisoformat(iso).timestamp() * 1000)
    except ValueError:
        return 0


#: the steering ``source`` the done-gate's own corrections are written under
#: (tick_context._apply_corrections). One name, read by the two places that
#: decide whether such a row outranks the owner's accept_close.
MACHINE_EVAL_SOURCE = "auto-eval"


def outranks_accept(rows: "list[tuple[int, str, str]]") -> bool:
    """Whether any unread steering row must run BEFORE an owner's standing
    ``accept_close`` closes (spec 045 US3). A human's later line and a
    mechanical correction (``auto-ci``, ``auto-conflict``) do — the last
    word and a fact both outrank the accept, as spec 041 rules. The
    evaluator's own concern rows (:data:`MACHINE_EVAL_SOURCE`) do not: they
    are the gap the accept accepted, and the close records them as
    follow-ups. ``rows`` are ``(id, source, line)`` triples."""
    return any(str(src or "") != MACHINE_EVAL_SOURCE for _id, src, _line in rows)


def pending_since(rows: "list[Decision]", last_plan_at: "str | None") -> "list[Decision]":
    """The current Decisions the loop has not acted on yet — made after the
    goal's last plan/dispatch instant (spec 041 FR-001). A Decision is work:
    the owner (or the timebox) said what to do, and the next tick does it
    instead of waiting for the cadence. Derived, never stored."""
    since = _iso_ms(last_plan_at)
    return [d for d in rows if not d.superseded_by and d.made_at > since]


def continues_since(rows: "list[Decision]", last_progress_at: "str | None") -> "list[Decision]":
    """The ``continue`` Decisions (a dispatch-cap Problem's default or the
    owner's pick) made since the goal last delivered an increment (spec 041
    FR-007). One is the bounded self-heal; a second with nothing delivered in
    between is the signal to wait for an explicit decide."""
    since = _iso_ms(last_progress_at)
    return [
        d for d in rows
        if not d.superseded_by and d.option_key == "continue" and d.made_at > since
    ]


def accepted_close(rows: "list[Decision]") -> "Decision | None":
    """The owner's standing ``accept_close`` — present only when the LATEST
    current Decision is an owner-provenance accept (spec 041 FR-003). A later
    Decision of any kind takes the last word: the accept then no longer
    closes without the gate. A *defaulted* accept never qualifies (a timebox
    is not an owner ruling — spec 031 Q2 → C stands)."""
    current = [d for d in rows if not d.superseded_by]
    if not current:
        return None
    last = max(current, key=lambda d: (d.made_at, d.id))
    if last.provenance == "owner" and last.option_key == "accept_close":
        return last
    return None


def _when(ms: int) -> str:
    import datetime as _dt
    try:
        return _dt.datetime.fromtimestamp(ms / 1000, _dt.timezone.utc).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001 — a bad timestamp never wedges a brief
        return "?"


def _entry(d: Decision) -> str:
    where = f'[clause: "{d.clause}"]' if d.clause else "[contract]"
    if d.text:
        what = f'{d.verb}: "{" ".join(d.text.split())[:240]}"'
    else:
        what = f"{d.verb}: {_OPTION_LABELS.get(d.option_key, d.option_key)}"
    return f"- {where} → {what} ({d.provenance}, {_when(d.made_at)})"


def render(rows: "list[Decision]") -> str:
    """The section, or ``""`` when the goal has no current Decision — absence
    needs no statement (it is a goal's default state)."""
    rows = [r for r in rows if not r.superseded_by]
    if not rows:
        return ""
    lines = [
        DECISIONS_MARKER + f" — settled by the owner, apply them as fact ({len(rows)} current):",
        "These are devclaw's own records of what the owner decided, not a worker's "
        "summary. Do NOT re-derive, re-litigate, or ask about a decided clause; build "
        "on it. A decision names the done_when clause it settles.",
    ]
    entries = cap_decisions("\n".join(_entry(r) for r in rows))
    return "\n".join(lines + [entries])
