"""The wayfinder plan-map — the durable, worker-owned plan-state, as plain data.

`docs/proposals/cognition-demolition.md`. The demolition moves a goal's plan out
of per-tick control-plane cognition (the planner LLM) and into a worker-owned MAP
on the target repo's GitHub issue tracker: a ``wayfinder:map`` parent issue (the
index — Destination, Notes, Decisions-so-far, Out-of-scope) and child DECISION
tickets (``wayfinder:<kind>`` — research/prototype/grilling/task) wired by
blocking relationships. The worker writes the map; the control plane READS it and
drives dispatch MECHANICALLY off the frontier — it never re-plans.

This module is the **pure core** (no I/O), and it is P2a of a sliced build:
- P2a (here): the map data model, a parser from normalized issue dicts, and the
  mechanical selectors the tick walks — :func:`next_frontier_ticket` ("what's
  next", the planner-LLM's job reduced to plain topology) and :func:`is_complete`
  ("propose done").
- P2b: the ``gh`` fetch/write adapter (issues ⇄ dicts) + the worker pull-brief.
- P2c: wiring these into the tick so the frontier walk replaces the planner call.

Keeping the "what's next" decision pure makes it fully unit-testable without
docker or ``claude`` — the whole point of moving planning from an unparseable LLM
verdict to a deterministic walk of persisted state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, Optional

#: the label marking the single parent index issue.
MAP_LABEL = "wayfinder:map"
#: prefix for decision-ticket kind labels (``wayfinder:<kind>``).
_KIND_PREFIX = "wayfinder:"
#: decision-ticket kinds. Mirrors the mattpocock wayfinder skill's ticket types
#: — the SHAPE borrowed and re-expressed, never the skill file installed (the
#: model-agnostic worker invariant; the worker drives ``gh``, not a vendor skill).
TicketKind = Literal["research", "prototype", "grilling", "task"]

#: ``Blocked by #12`` (case-insensitive) in a ticket body declares a dependency.
_BLOCKED_BY_RE = re.compile(r"blocked by #(\d+)", re.IGNORECASE)
#: a closed ticket records its answer after a ``Resolution:`` marker.
_RESOLUTION_RE = re.compile(r"resolution:\s*(.+)", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class WayfinderTicket:
    """One decision ticket — a child issue of the map. A ticket resolves a
    QUESTION whose answer is a decision, not a slice of build-work to execute."""

    number: int
    title: str
    kind: str  # one of TicketKind; an unknown kind is tolerated (kept as-is)
    state: str  # "open" | "closed"
    blocked_by: tuple[int, ...] = ()
    resolution: str = ""  # the answer, present once closed

    @property
    def is_open(self) -> bool:
        return self.state == "open"


@dataclass(frozen=True)
class WayfinderMap:
    """The parsed plan-map — the durable plan-state the control plane READS. An
    index, not a store: each decision lives in its own ticket; the map gists and
    links, it never restates."""

    map_number: int
    destination: str
    notes: str = ""
    out_of_scope: tuple[str, ...] = ()
    tickets: tuple[WayfinderTicket, ...] = ()

    @property
    def open_tickets(self) -> tuple[WayfinderTicket, ...]:
        return tuple(t for t in self.tickets if t.is_open)

    @property
    def closed_tickets(self) -> tuple[WayfinderTicket, ...]:
        return tuple(t for t in self.tickets if not t.is_open)

    @property
    def decisions_so_far(self) -> tuple[str, ...]:
        """One-line gists of resolved decisions (closed tickets)."""
        return tuple(
            f"#{t.number} {t.title}"
            + (f": {t.resolution}" if t.resolution else "")
            for t in self.closed_tickets
        )


# ---- the mechanical selectors (what the tick walks — no cognition) ----------


def next_frontier_ticket(m: WayfinderMap) -> Optional[WayfinderTicket]:
    """The mechanical "what's next": the first OPEN ticket whose every blocker is
    CLOSED (an unblocked frontier ticket). This is the planner-LLM's per-tick job
    reduced to walking the persisted map — no tokens, just topology.

    Returns ``None`` when no open ticket is unblocked — either the map is complete
    (see :func:`is_complete`) or every open ticket still waits on an open
    dependency (:func:`is_stalled_on_deps`); the goal waits, it does not re-plan.
    Deterministic: ties break on ascending issue number, so the same map always
    dispatches the same next ticket (a re-tick is idempotent)."""
    closed = {t.number for t in m.closed_tickets}
    unblocked = [
        t for t in m.open_tickets if all(dep in closed for dep in t.blocked_by)
    ]
    return min(unblocked, key=lambda t: t.number) if unblocked else None


def is_complete(m: WayfinderMap) -> bool:
    """True when the map HAS tickets and every one is closed → the goal MAY
    propose done. The close itself stays gated on the grounded done-gate; this is
    only the mechanical proposal trigger. A map with zero tickets is NOT complete
    — an empty frontier means "not yet charted", never "done"."""
    return bool(m.tickets) and all(not t.is_open for t in m.tickets)


def is_stalled_on_deps(m: WayfinderMap) -> bool:
    """True when there ARE open tickets but none is unblocked — every remaining
    ticket waits on an open dependency (an unresolved blocker or a dependency
    cycle). The tick surfaces this as blocked-on-deps rather than churning."""
    return bool(m.open_tickets) and next_frontier_ticket(m) is None


# ---- the parser (normalized issue dicts → the model; gh adapter is P2b) ------


def _parse_labels(raw: object) -> list[str]:
    """Labels come as either bare strings or ``{"name": ...}`` dicts depending on
    the gh call; tolerate both."""
    out: list[str] = []
    if isinstance(raw, list):
        for label in raw:
            if isinstance(label, str):
                out.append(label)
            elif isinstance(label, dict) and label.get("name"):
                out.append(str(label["name"]))
    return out


def _kind_of(labels: list[str]) -> Optional[str]:
    """The wayfinder role of an issue: ``"map"`` for the index, the ``<kind>`` for
    a decision ticket, or ``None`` for an unrelated issue (ignored)."""
    for label in labels:
        if label == MAP_LABEL:
            return "map"
        if label.startswith(_KIND_PREFIX):
            return label[len(_KIND_PREFIX):]
    return None


def _blocked_by(body: str) -> tuple[int, ...]:
    seen: dict[int, None] = {}  # dedup, preserve first-seen order
    for n in _BLOCKED_BY_RE.findall(body or ""):
        seen.setdefault(int(n), None)
    return tuple(seen)


def _resolution(body: str) -> str:
    m = _RESOLUTION_RE.search(body or "")
    return m.group(1).strip() if m else ""


def _section(body: str, header: str) -> str:
    """Text under a ``## <header>`` markdown section (up to the next heading or
    end). Empty string when the section is absent."""
    pat = re.compile(
        rf"^#+\s*{re.escape(header)}\s*$(.*?)(?=^#+\s|\Z)",
        re.IGNORECASE | re.MULTILINE | re.DOTALL,
    )
    m = pat.search(body or "")
    return m.group(1).strip() if m else ""


def _section_items(body: str, header: str) -> list[str]:
    """The ``- `` bullet items under a section, in order; ``[]`` if absent."""
    return [
        line.strip()[2:].strip()
        for line in _section(body, header).splitlines()
        if line.strip().startswith("- ")
    ]


def parse_map(issues: list[dict]) -> Optional[WayfinderMap]:
    """Build a :class:`WayfinderMap` from normalized issue dicts
    ``{number, title, body, state, labels}`` (the ``gh``→dict adapter is P2b).
    The single ``wayfinder:map``-labelled issue is the index; ``wayfinder:<kind>``
    issues are its decision tickets.

    Returns ``None`` when no map issue is present — the goal has no plan-map yet;
    the caller treats that as "not charted" and may block legibly rather than
    invent one. Best-effort and total: a malformed ticket degrades (unknown kind
    kept as-is; missing body → no blockers/resolution) and never raises. A missing
    map issue is the caller's block-legibly case (#185/#188), signalled by the
    ``None`` return, not an exception here."""
    map_issue: Optional[dict] = None
    tickets: list[WayfinderTicket] = []
    for iss in issues or []:
        labels = _parse_labels(iss.get("labels", []))
        kind = _kind_of(labels)
        if kind is None:
            continue
        if kind == "map":
            map_issue = iss  # last wins; a well-formed repo has exactly one
            continue
        state = str(iss.get("state") or "open").lower()
        body = str(iss.get("body") or "")
        tickets.append(
            WayfinderTicket(
                number=int(iss.get("number", 0)),
                title=str(iss.get("title") or "").strip(),
                kind=kind,
                state=state,
                blocked_by=_blocked_by(body),
                resolution=_resolution(body) if state == "closed" else "",
            )
        )
    if map_issue is None:
        return None
    mbody = str(map_issue.get("body") or "")
    return WayfinderMap(
        map_number=int(map_issue.get("number", 0)),
        destination=_section(mbody, "Destination"),
        notes=_section(mbody, "Notes"),
        out_of_scope=tuple(_section_items(mbody, "Out of scope")),
        tickets=tuple(sorted(tickets, key=lambda t: t.number)),
    )
