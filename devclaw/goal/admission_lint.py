"""The ``done_when`` admission lint (spec 031 US3).

At goal creation the contract is checked against the three classes that
produced 2026-09-02's avoidable owner pings — refused or rewritten NOW, to the
author, instead of surfacing to a worker hours later:

* **(a) capability-impossible** — a clause naming something the sandbox can
  never have (a credential, an external service, a human confirming).
  → REFUSED, the clause and the capability named, nothing persisted (Q3 → A:
  the author stays the author; a guessed rewrite would become a settled
  fact the gate then enforces).
* **(b) baseline-less absolute predicate** — "all tests pass", "no failing
  tests", "zero warnings" with no baseline. → REWRITTEN to "no new failures
  relative to the default branch", recorded as an admission Decision (the
  rewrite is unambiguous).
* **(c) undecided design choice** — a clause whose satisfaction depends on a
  choice the contract does not make. → raised as a Problem to the author
  BEFORE any dispatch (it is a choice, so it gets options).

(a) and (b) are mechanical, deterministic and free. (c) needs reading and is
the ONE cognition call this lint makes — at creation, never on the tick
(constitution III). Grounding: the prompt sees only the contract text; it is
told absent ⇒ unknown and forbidden to infer repository facts (#227 shape).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..prompts import load_prompt

ClaudeCaller = Callable[[str], Awaitable[str]]

#: (a) — words that name a capability the sandbox structurally lacks. Matched
#: per clause, case-insensitive, on word boundaries. Deliberately short: a
#: false refusal is loud and costs one resubmit; a miss is caught later by the
#: worker honest-block path (and recorded as a lint miss, R4).
_IMPOSSIBLE: tuple[tuple[str, str], ...] = (
    (r"\b(credential|credentials|secret|api key|api-key|access token|auth token|password)\b", "a credential"),
    (r"\b(telegram|slack|discord|whatsapp|sms|e-?mail(?:s|ed)?)\b", "an external messaging service"),
    (r"\b(a human|manually|by hand|someone (?:checks|confirms|verifies))\b", "a human confirming"),
    (r"\b(real|live|actual) (?:telegram|slack|e-?mail|message|brief|notification|payment)s? (?:is|are|gets?) (?:sent|delivered|posted)\b", "a real message being sent"),
    (r"\bproduction (?:account|credentials|database|data)\b", "production access"),
)

#: (b) — absolute repository-wide predicates that need a baseline.
_ABSOLUTE = re.compile(
    r"\b(all|every|100%|zero|no)\s+(?:existing\s+)?(tests?|specs?|checks?|warnings?|lint(?:er)? (?:errors?|warnings?)|failures?)\b"
    r"(?:\s+(?:pass(?:es|ing)?|green|succeed|are green))?",
    re.IGNORECASE,
)
_HAS_BASELINE = re.compile(r"\b(new|relative to|vs\.?|compared to|against|baseline|regression|since)\b", re.IGNORECASE)
_REWRITE = "no new failures relative to the default branch"


@dataclass(frozen=True)
class Refusal:
    clause: str
    capability: str


@dataclass(frozen=True)
class Rewrite:
    original: str
    rewritten: str


@dataclass(frozen=True)
class Undecided:
    clause: str
    choice: str
    options: tuple[str, ...]


@dataclass(frozen=True)
class LintResult:
    refusals: tuple[Refusal, ...] = ()
    rewrites: tuple[Rewrite, ...] = ()
    undecided: tuple[Undecided, ...] = ()
    done_when: str = ""
    #: the cognition call for (c) was skipped or failed — said out loud (VI)
    note: str = ""
    _extra: dict = field(default_factory=dict, compare=False)

    @property
    def refused(self) -> bool:
        return bool(self.refusals)


def clauses_of(done_when: str) -> list[str]:
    """Split a contract into clauses: one per line / bullet / sentence."""
    text = (done_when or "").replace("\r", "")
    raw = re.split(r"\n+|(?<=[.;])\s+(?=[A-Z(\-•*\d])", text)
    out = []
    for c in raw:
        c = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s*", "", c).strip()
        if c:
            out.append(c)
    return out


def lint_mechanical(done_when: str) -> LintResult:
    """Classes (a) and (b). Pure, deterministic, never raises."""
    refusals: list[Refusal] = []
    rewrites: list[Rewrite] = []
    new_clauses: list[str] = []
    for clause in clauses_of(done_when):
        low = clause.lower()
        hit = next((cap for pat, cap in _IMPOSSIBLE if re.search(pat, low)), None)
        if hit:
            refusals.append(Refusal(clause=clause, capability=hit))
            new_clauses.append(clause)
            continue
        if _ABSOLUTE.search(clause) and not _HAS_BASELINE.search(clause):
            new = _ABSOLUTE.sub(_REWRITE, clause, count=1)
            rewrites.append(Rewrite(original=clause, rewritten=new))
            new_clauses.append(new)
            continue
        new_clauses.append(clause)
    return LintResult(
        refusals=tuple(refusals), rewrites=tuple(rewrites),
        done_when="\n".join(new_clauses) if rewrites else (done_when or "").strip(),
    )


def _extract_json(text: str) -> object:
    m = re.search(r"\{[\s\S]*\}", text or "")
    return json.loads(m.group(0)) if m else {}


async def judge_undecided(done_when: str, claude_caller: Optional[ClaudeCaller]) -> tuple[tuple[Undecided, ...], str]:
    """Class (c) via ONE cognition call. Returns (undecided, note). Never
    raises: a failed or absent caller yields no findings and a loud note."""
    if claude_caller is None:
        return (), "undecided-choice check skipped: no cognition caller configured"
    try:
        raw = await claude_caller(load_prompt("admission-lint", done_when=done_when.strip()))
        parsed = _extract_json(raw)
    except Exception as exc:  # noqa: BLE001 — creation must not wedge on the judge
        return (), f"undecided-choice check failed ({exc.__class__.__name__}); admitted without it"
    found: list[Undecided] = []
    items = parsed.get("undecided") if isinstance(parsed, dict) else None
    for it in (items or []) if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        clause = str(it.get("clause", "")).strip()
        choice = str(it.get("choice", "")).strip()
        opts = tuple(str(o).strip() for o in (it.get("options") or []) if str(o).strip())
        if clause and choice and len(opts) >= 2:
            found.append(Undecided(clause=clause[:400], choice=choice[:400], options=opts[:4]))
    return tuple(found), ""


async def lint(done_when: str, *, claude_caller: Optional[ClaudeCaller] = None) -> LintResult:
    """All three classes. A refusal short-circuits — nothing else is judged
    (the author fixes and resubmits; the cognition call is not spent)."""
    mech = lint_mechanical(done_when)
    if mech.refused:
        return mech
    undecided, note = await judge_undecided(mech.done_when, claude_caller)
    return LintResult(
        refusals=(), rewrites=mech.rewrites, undecided=undecided,
        done_when=mech.done_when, note=note,
    )


def refusal_message(result: LintResult) -> str:
    lines = ["done_when refused at admission — rewrite these clauses as observable repository behaviour and resubmit:"]
    for r in result.refusals:
        lines.append(f'- "{r.clause}" requires {r.capability}, which the sandbox cannot provide')
    return "\n".join(lines)
