"""First-class goal→issue references (spec 019 US1) — the one seam every
consumer of a referenced goal shares.

A goal that works an issue references it structurally (``Goal.issue_refs``,
ordered issue numbers against the goal's own repository) instead of pasting
its content into the objective. The reference is resolved to LIVE issue state
at each dispatch boundary through :func:`fetch_issue` — a creation-time copy
of an issue is unrepresentable by construction, which is what kills the
stale-contract class (a goal text froze a correction, main independently
fixed it, the night produced a conflicting PR).

The gh subprocess boundary mirrors :mod:`devclaw.goal.remote_checks`: the
default fetcher shells out to ``gh``; production binds it, tests inject a
fake — the tick itself stays subprocess-free under test, and NOTHING on an
idle tick path calls in here (the fetch sits below the should_plan gate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from . import remote_checks as _remote_checks


class IssueRefError(Exception):
    """A referenced issue could not be resolved to live state — unknown repo
    shape, gh failure, or a response that isn't an issue. The dispatch
    boundary BLOCKS on this (human-gated): devclaw never dispatches from a
    guess or a stale copy."""


@dataclass(frozen=True)
class IssueSnapshot:
    """One issue's live state as of one fetch — threaded into ONE worker
    brief (or one gate round), never persisted: freshness by construction."""

    number: int
    title: str
    body: str
    state: str  # "open" | "closed"
    labels: tuple[str, ...] = ()


#: The injectable fetch seam: ``(repo_url, number) -> IssueSnapshot``.
IssueFetcher = Callable[[str, int], Awaitable[IssueSnapshot]]


def validate_refs(issues: "list[int] | None", *, repo_url: Optional[str]) -> list[int]:
    """Structural validation at the creation doorway (spec 019 FR-008 slice
    landed with US1: shape + dedupe + a repository to resolve against).
    Every refusal names the rule, the offending input, and the fixing verb —
    nothing is persisted on refusal (the caller raises before any write)."""
    if not issues:
        return []
    if not repo_url:
        raise ValueError(
            "issue references need a repository to resolve against — this "
            "goal has no repo_url; pass one, or file the goal without "
            "`issues` (the issue-less lane)"
        )
    refs: list[int] = []
    for x in issues:
        if isinstance(x, bool) or not isinstance(x, int) or x <= 0:
            raise ValueError(
                f"issue reference {x!r} is not a positive issue number — "
                "references are plain numbers on the goal's own repository "
                f"({repo_url}); cross-repo references are not supported"
            )
        if x in refs:
            raise ValueError(
                f"issue #{x} is referenced twice — each issue appears at "
                "most once per goal; drop the duplicate"
            )
        refs.append(x)
    return refs


async def fetch_issue(repo_url: str, number: int) -> IssueSnapshot:
    """The gh-backed default fetcher: one ``gh api`` read of the issue's
    current state. Any failure raises :class:`IssueRefError` — the caller
    blocks loudly; it never degrades to empty content (a completion contract
    and a worker brief are load-bearing inputs, not optional grounding)."""
    import json as _json

    owner_repo = _remote_checks.parse_owner_repo(repo_url)
    if not owner_repo:
        raise IssueRefError(f"cannot derive owner/repo from repo_url {repo_url!r}")
    rc, out = await _remote_checks._gh("api", f"repos/{owner_repo}/issues/{number}")
    if rc != 0:
        raise IssueRefError(
            f"gh could not fetch {owner_repo}#{number} (exit {rc}): {out.strip()[:200]}"
        )
    try:
        data = _json.loads(out)
    except ValueError as exc:
        raise IssueRefError(f"non-JSON gh response for {owner_repo}#{number}") from exc
    if not isinstance(data, dict) or "state" not in data:
        raise IssueRefError(f"unexpected gh response shape for {owner_repo}#{number}")
    return IssueSnapshot(
        number=number,
        title=str(data.get("title") or ""),
        body=str(data.get("body") or ""),
        state=str(data.get("state") or "open"),
        labels=tuple(
            str(lb.get("name"))
            for lb in (data.get("labels") or [])
            if isinstance(lb, dict) and lb.get("name")
        ),
    )


#: Shared marker so brief detectors and tests never drift from the generator
#: (the #547/#550 discipline).
ISSUE_CONTEXT_MARKER = "Referenced issues (live state, fetched at dispatch)"

#: Cap per issue body in the brief — the issue is readable in full in the
#: repo's tracker; the brief carries enough to work from without becoming the
#: essay this feature exists to kill.
_BODY_CAP = 4000


def render_issue_context(
    open_snaps: "list[IssueSnapshot]", dropped: "list[IssueSnapshot]",
) -> str:
    """The brief section for a referenced dispatch: every OPEN referenced
    issue's live title + body, in goal order, plus an explicit line per
    dropped (closed) reference so the worker never re-does closed work."""
    parts = [f"{ISSUE_CONTEXT_MARKER} — work these, in order:"]
    for s in open_snaps:
        body = s.body.strip()
        if len(body) > _BODY_CAP:
            body = body[:_BODY_CAP] + "\n[... issue body truncated — read the full issue in the tracker]"
        parts += ["", f"### Issue #{s.number}: {s.title.strip()}", body or "(no body)"]
    for s in dropped:
        parts += [
            "",
            f"Issue #{s.number} is {s.state} — already resolved out-of-band; "
            "do NOT work on it.",
        ]
    return "\n".join(parts)


# ---- acceptance scenarios as the completion contract (spec 019 US2) --------


class MissingAcceptance(Exception):
    """A referenced issue carries no recognizable acceptance section — the
    scenario-default ``done_when`` cannot be built from it. Carries the
    offending issue numbers; every consumer surfaces them with the fixing
    verbs (groom the issue, or write an explicit ``done_when``)."""

    def __init__(self, numbers: "list[int]"):
        self.numbers = list(numbers)
        super().__init__(
            "no acceptance section in issue(s) "
            + ", ".join(f"#{n}" for n in self.numbers)
        )


def extract_acceptance(body: str) -> Optional[str]:
    """Mechanically slice the acceptance section out of an issue body: from a
    heading whose text starts with 'Acceptance' to the next heading of the
    same or higher level. Zero cognition — the grading pipeline guarantees
    the format; this only cuts along it. None when absent."""
    import re

    if not body:
        return None
    m = re.search(r"^(#{2,4})\s*Acceptance\b.*$", body, flags=re.MULTILINE | re.IGNORECASE)
    if not m:
        return None
    level = len(m.group(1))
    tail = body[m.end():]
    stop = re.search(rf"^#{{1,{level}}}\s", tail, flags=re.MULTILINE)
    section = tail[: stop.start()] if stop else tail
    section = section.strip()
    return section or None


async def scenarios_contract(
    repo_url: str, refs: "list[int]", fetcher: "IssueFetcher",
) -> str:
    """The scenario-default completion contract, read LIVE (clarified
    2026-08-25: evaluation time, never a creation-time copy): every
    referenced issue's acceptance section, in goal order. Raises
    :class:`IssueRefError` on any fetch failure and
    :class:`MissingAcceptance` when a body carries no section — both
    LOAD-BEARING: a completion contract is never silently empty."""
    parts: list[str] = []
    missing: list[int] = []
    for n in refs:
        snap = await fetcher(repo_url, n)
        acc = extract_acceptance(snap.body)
        if acc is None:
            missing.append(n)
            continue
        parts.append(f"Acceptance scenarios of issue #{n} ({snap.title.strip()}):\n{acc}")
    if missing:
        raise MissingAcceptance(missing)
    return "\n\n".join(parts)
