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

(c) fails CLOSED (constitution V): a caller that raises, or a reply the
protocol cannot read, is :class:`AdmissionLintError` and the goal is NOT
admitted — nothing persists, the author resubmits once cognition answers.
The one deliberate skip is a deployment with no caller configured, said out
loud in ``LintResult.note``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from ..llm_call import PlannerError, extract_json
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

#: (a), continued — a clause whose change lands in ANOTHER repository. The
#: worker holds exactly one checkout, so this is capability-impossible for the
#: whole class however it is worded (#847: "Companion (finance-sentry repo,
#: separate PR)" reached the done-gate and parked the goal). Three shapes:
#: an adjective marking the repo as not-this-one; a companion PR; a repo
#: named outright (``finance-sentry repo``, ``lifekit-hq/finance-sentry``)
#: that is not the goal's own — the last two need ``own_repo`` and are
#: skipped without it, so a repo merely MENTIONED as context by a name that
#: is the goal's own never refuses.
_CROSS_REPO_CAPABILITY = "a change in another repository"
_CROSS_REPO_MARKED = re.compile(
    r"\b(?:separate|another|other|different|companion|sibling|external|second)"
    r" (?:repo|repository|pr|pull request)\b"
    r"|\bcompanion (?:change|commit)\b"
)
_NAMED_REPO = re.compile(r"\b([a-z0-9][\w.-]*) (?:repo|repository)\b")
_REPO_SLUG = re.compile(r"\b([a-z0-9][\w.-]*)/([a-z0-9][\w.-]*)\b")
#: Words that precede "repo" without naming one.
_NOT_A_REPO_NAME = frozenset({
    "this", "the", "a", "an", "our", "same", "that", "its", "own", "each",
    "every", "any", "one", "per", "target", "local", "remote", "git", "current",
    "whole", "entire", "project", "source", "upstream", "main", "default",
    "bare", "package", "npm", "private", "public", "github", "gitlab", "mono",
    "code", "product", "consuming", "consumer", "downstream", "parent", "child",
})


def _cross_repo(clause_low: str, own_repo: Optional[str]) -> bool:
    """Does the clause put its change in a repository other than ``own_repo``
    (an ``owner/name`` slug, or None when unknown)?"""
    if _CROSS_REPO_MARKED.search(clause_low):
        return True
    if not own_repo or "/" not in own_repo:
        return False
    own_owner, own_name = own_repo.lower().split("/", 1)
    for m in _NAMED_REPO.finditer(clause_low):
        name = m.group(1)
        if name not in _NOT_A_REPO_NAME and name != own_name and name != own_owner:
            return True
    for m in _REPO_SLUG.finditer(clause_low):
        if m.group(1) == own_owner and m.group(2) != own_name:
            return True
    return False


#: (b) — absolute repository-wide predicates that need a baseline.
_ABSOLUTE = re.compile(
    r"\b(all|every|100%|zero|no)\s+(?:existing\s+)?(tests?|specs?|checks?|warnings?|lint(?:er)? (?:errors?|warnings?)|failures?)\b"
    r"(?:\s+(?:pass(?:es|ing)?|green|succeed|are green))?",
    re.IGNORECASE,
)
_HAS_BASELINE = re.compile(r"\b(new|relative to|vs\.?|compared to|against|baseline|regression|since)\b", re.IGNORECASE)
_REWRITE = "no new failures relative to the default branch"


class AdmissionLintError(Exception):
    """The undecided-choice judge produced no usable verdict — the caller
    raised, or the reply is not ``{"undecided": [...]}`` with well-formed
    entries. Creation refuses on it (fail closed); nothing is persisted."""


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
    #: the cognition call for (c) was skipped (no caller configured) — said out loud (VI)
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


def lint_mechanical(done_when: str, *, own_repo: Optional[str] = None) -> LintResult:
    """Classes (a) and (b). Pure, deterministic, never raises. ``own_repo`` is
    the goal's own ``owner/name`` slug, which lets (a) tell a repository named
    as context from one named as where the change lands."""
    refusals: list[Refusal] = []
    rewrites: list[Rewrite] = []
    new_clauses: list[str] = []
    for clause in clauses_of(done_when):
        low = clause.lower()
        hit = next((cap for pat, cap in _IMPOSSIBLE if re.search(pat, low)), None)
        if hit is None and _cross_repo(low, own_repo):
            hit = _CROSS_REPO_CAPABILITY
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


async def judge_undecided(done_when: str, claude_caller: Optional[ClaudeCaller]) -> tuple[tuple[Undecided, ...], str]:
    """Class (c) via ONE cognition call. Returns (undecided, note).

    Fails CLOSED: a caller that raises or a reply outside the protocol raises
    :class:`AdmissionLintError` — a lint that cannot judge admits nothing. The
    only skip is an absent caller, reported in ``note``."""
    if claude_caller is None:
        return (), "undecided-choice check skipped: no cognition caller configured"
    try:
        raw = await claude_caller(load_prompt("admission-lint", done_when=done_when.strip()))
    except Exception as exc:  # noqa: BLE001 — one typed refusal, never a wedge
        raise AdmissionLintError(
            f"undecided-choice check failed: {exc.__class__.__name__}: {exc}"
        ) from exc
    try:
        parsed = json.loads(extract_json(raw))
    except (PlannerError, json.JSONDecodeError) as exc:
        raise AdmissionLintError(f"undecided-choice check returned no JSON object: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("undecided"), list):
        raise AdmissionLintError('undecided-choice check reply is not {"undecided": [...]}')
    found: list[Undecided] = []
    for it in parsed["undecided"]:
        if not isinstance(it, dict):
            raise AdmissionLintError("undecided-choice entry is not an object")
        clause = str(it.get("clause", "")).strip()
        choice = str(it.get("choice", "")).strip()
        opts = tuple(str(o).strip() for o in (it.get("options") or []) if str(o).strip())
        if not (clause and choice and len(opts) >= 2):
            raise AdmissionLintError("undecided-choice entry lacks a clause, a choice, or two options")
        found.append(Undecided(clause=clause[:400], choice=choice[:400], options=opts[:4]))
    return tuple(found), ""


async def lint(
    done_when: str, *, claude_caller: Optional[ClaudeCaller] = None,
    own_repo: Optional[str] = None,
) -> LintResult:
    """All three classes. A refusal short-circuits — nothing else is judged
    (the author fixes and resubmits; the cognition call is not spent)."""
    mech = lint_mechanical(done_when, own_repo=own_repo)
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
