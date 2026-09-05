"""The pinned done-gate rubric (spec 035) — one decomposition per contract
revision.

The done-gate used to re-decompose ``done_when`` inside every evaluator call;
the same contract yielded 4, then 6, then 8 clauses across one goal's rounds
(fs-479, 2026-09-01), so closing was a moving target. The pin is the
task_change doctrine applied to the rubric: the FIRST round's decomposition
for a contract revision is persisted (one record per (goal, revision), the
revision being the content digest ``_live_contract`` already computes), and
every later round judges exactly that list — evidence fresh, rubric fixed.

Ids are assigned HERE, mechanically (``c1..cN`` in pinned order) — never by
the model: the party being constrained must not mint the keys. Identity is
the (id, verbatim text) pair; monotonicity checks are id-set arithmetic, not
text comparison (clarified 2026-09-05).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from typing import Sequence


class PinCorrupt(Exception):
    """A stored pin row that cannot be parsed. Raised by the read seam so the
    gate can recover LOUDLY (re-decompose + re-pin with the reason recorded,
    spec 035 FR-006) — never judged against a half-read rubric."""


@dataclass(frozen=True)
class PinnedClause:
    """One clause of the pinned rubric + its running accounting (US2)."""

    id: str
    text: str
    satisfied: bool = False
    evidence: str = ""
    satisfied_round: "int | None" = None
    #: Decision id when the clause is satisfied by an owner ruling (spec 031);
    #: the clause STAYS in the denominator — a Decision is evidence, never
    #: rubric surgery (clarified 2026-09-05).
    via_decision: str = ""
    #: on a re-pin: the prior revision's clause id this entry inherited from
    #: (byte-identical text carry-forward, FR-003); "" otherwise.
    carried_from: str = ""


@dataclass(frozen=True)
class ContractPin:
    """The decomposition of one contract revision, persisted once."""

    goal_id: str
    revision: str
    clauses: tuple[PinnedClause, ...]
    #: ceremony text step 1a dropped at decomposition — recorded once with the
    #: pin (FR-005), not re-discovered per round.
    ceremony_drops: tuple[str, ...] = ()
    pinned_at_ms: int = 0
    pinned_by_round: int = 0
    #: non-empty iff this pin replaced a corrupt/unreadable one (FR-006).
    recovery: str = ""

    @property
    def ids(self) -> frozenset[str]:
        return frozenset(c.id for c in self.clauses)

    def by_id(self) -> dict[str, PinnedClause]:
        return {c.id: c for c in self.clauses}

    def satisfied_count(self) -> int:
        return sum(1 for c in self.clauses if c.satisfied)


def assign_ids(
    goal_id: str,
    revision: str,
    clause_texts: Sequence[str],
    *,
    ceremony_drops: Sequence[str] = (),
    pinned_at_ms: int = 0,
    pinned_by_round: int = 0,
    recovery: str = "",
) -> ContractPin:
    """Build a fresh pin from a decomposition round's clause texts. Blank and
    byte-duplicate texts are dropped (a duplicate clause is one requirement,
    and duplicate texts would make the byte-identical carry-forward
    ambiguous)."""
    seen: set[str] = set()
    clauses: list[PinnedClause] = []
    for text in clause_texts:
        t = text.strip()
        if not t or t in seen:
            continue
        seen.add(t)
        clauses.append(PinnedClause(id=f"c{len(clauses) + 1}", text=t))
    return ContractPin(
        goal_id=goal_id, revision=revision, clauses=tuple(clauses),
        ceremony_drops=tuple(d.strip() for d in ceremony_drops if d.strip()),
        pinned_at_ms=pinned_at_ms, pinned_by_round=pinned_by_round,
        recovery=recovery,
    )


def carry_forward(old: ContractPin, new: ContractPin) -> ContractPin:
    """FR-003: an amendment never resets accounting it did not touch. A clause
    in ``new`` whose text is byte-identical to one in ``old`` inherits that
    clause's satisfied/evidence/via_decision state (still re-judgeable under
    the FR-011 flip rule); genuinely changed clauses start open."""
    by_text = {c.text: c for c in old.clauses}
    out: list[PinnedClause] = []
    for c in new.clauses:
        prior = by_text.get(c.text)
        if prior is not None and prior.satisfied:
            out.append(replace(
                c, satisfied=True, evidence=prior.evidence,
                satisfied_round=prior.satisfied_round,
                via_decision=prior.via_decision, carried_from=prior.id,
            ))
        else:
            out.append(c)
    return replace(new, clauses=tuple(out))


# ---- (de)serialization — the shape stored in goal_contract_pins.clauses ----

def clauses_to_json(pin: ContractPin) -> str:
    return json.dumps([
        {
            "id": c.id, "text": c.text, "satisfied": c.satisfied,
            "evidence": c.evidence, "satisfied_round": c.satisfied_round,
            "via_decision": c.via_decision, "carried_from": c.carried_from,
        }
        for c in pin.clauses
    ])


def drops_to_json(pin: ContractPin) -> str:
    return json.dumps(list(pin.ceremony_drops))


def pin_from_row(
    goal_id: str, revision: str, clauses_json: str, drops_json: str,
    pinned_at_ms: int, pinned_by_round: int, recovery: str,
) -> ContractPin:
    """Rehydrate a stored pin. Any malformed shape raises :class:`PinCorrupt`
    with the reason — the caller recovers loudly, never judges a partial
    rubric (FR-006)."""
    try:
        raw = json.loads(clauses_json)
        drops = json.loads(drops_json or "[]")
    except (json.JSONDecodeError, TypeError) as exc:
        raise PinCorrupt(f"pin row for revision {revision} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise PinCorrupt(f"pin row for revision {revision} holds no clause list")
    clauses: list[PinnedClause] = []
    ids: set[str] = set()
    for entry in raw:
        if not isinstance(entry, dict):
            raise PinCorrupt(f"pin row for revision {revision}: non-object clause entry")
        cid = str(entry.get("id", "")).strip()
        text = str(entry.get("text", "")).strip()
        if not cid or not text or cid in ids:
            raise PinCorrupt(
                f"pin row for revision {revision}: clause with missing/duplicate id or empty text"
            )
        ids.add(cid)
        sr = entry.get("satisfied_round")
        clauses.append(PinnedClause(
            id=cid, text=text, satisfied=bool(entry.get("satisfied", False)),
            evidence=str(entry.get("evidence", "")),
            satisfied_round=int(sr) if isinstance(sr, int) else None,
            via_decision=str(entry.get("via_decision", "")),
            carried_from=str(entry.get("carried_from", "")),
        ))
    if not isinstance(drops, list):
        drops = []
    return ContractPin(
        goal_id=goal_id, revision=revision, clauses=tuple(clauses),
        ceremony_drops=tuple(str(d) for d in drops),
        pinned_at_ms=pinned_at_ms, pinned_by_round=pinned_by_round,
        recovery=recovery or "",
    )
