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
}


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
