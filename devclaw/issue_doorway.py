"""The issue doorway — the ONE writer of machine-found problems as GitHub
issues (spec 014).

`intake.py` is the human doorway (an ask becomes an issue); this module is its
machine sibling: any devclaw mechanism that discovers a problem — the problems
catalog at cycle close, the post-deploy smoke, the spec-015 validator — files
it here, and only here. The rendered body follows ONE fixed, versioned,
machine-parseable schema (``specs/014-issue-doorway/contracts/issue-schema.md``
is normative), so every machine-filed issue on every repo reads the same and
the intake-grading loop consumes it without human rewriting.

Filing is idempotent by fingerprint: the ``machine_issues`` ledger in the state
store (NOT GitHub search — research.md D3) is the dedup source of truth. A
repeat on an open issue appends an occurrence comment; a recurrence after close
reopens the same issue marked as a regression — one issue per root cause, ever.

Zero LLM (FR-007). Failure is loud, never silent (FR-006): the caller receives
a ``failed`` :class:`FilingOutcome` and a problems-catalog row is recorded; the
finding itself stays owned by the originating surface, which re-fires on its
next edge.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence
from urllib.parse import quote as _quote, unquote as _unquote

from .procutil import run as _run
from .state_store.rows import _now_ms

#: In-band schema version (FR-004) — bump on any change to the body contract.
SCHEMA_VERSION = 1
#: The doorway marker label every machine-filed issue carries (research.md D8).
MACHINE_LABEL = "devclaw:machine-filed"
#: The closed severity vocabulary — invalid input is rejected loudly at
#: construction, never silently coerced.
SEVERITIES = ("critical", "high", "medium", "low")
#: Deterministic evidence cap (research.md D7) — keeps the whole body far under
#: GitHub's 65,536-char limit without any "smart" (= LLM) summarizing.
EVIDENCE_MAX_CHARS = 6000
#: Matches ``intake.py``'s title cap.
_TITLE_MAX = 240
#: The admission ``vague_done_when`` bar — a machine-filed issue must be
#: dispatchable as a goal without a human edit (SC-003).
MIN_DONE_WHEN_CHARS = 20

#: The one extraction regex for the metadata line (contract: no heuristics).
_META_RE = re.compile(
    r"^<!-- devclaw-machine-issue v(?P<version>\d+) "
    r"fingerprint=(?P<fp>\S+) source=(?P<source>\S+) "
    r"severity=(?P<sev>\S+) -->$",
    re.MULTILINE,
)


# ---- the finding (input) -----------------------------------------------------

@dataclass(frozen=True)
class MachineFinding:
    """The doorway's input. Every field is mandatory; a field with no
    meaningful value carries the literal string ``unknown`` (absent-but-stated,
    never omitted — FR-001). Validation raises ONE ``ValueError`` naming every
    problem at once (the ``intake.validate_shape`` shape)."""

    source: str
    fingerprint: str
    title: str
    evidence: str
    expected: str
    actual: str
    severity: str
    proposed_done_when: str
    spec_ref: Optional[str] = None

    def __post_init__(self) -> None:
        problems: list[str] = []
        if not (self.source or "").strip() or any(c.isspace() for c in self.source):
            problems.append("'source' is required and must be whitespace-free")
        if not (self.fingerprint or "").strip():
            # Free-form on purpose: producers own their fingerprint shape (the
            # catalog's is `cat|kind|message` WITH spaces); the doorway
            # percent-encodes it into the metadata line, so transport-safety
            # is the doorway's job, never a producer constraint.
            problems.append("'fingerprint' is required")
        if not (self.title or "").strip():
            problems.append("'title' is required")
        if not (self.evidence or "").strip():
            problems.append("'evidence' is required ('unknown' if none)")
        if not (self.expected or "").strip():
            problems.append("'expected' is required ('unknown' if not meaningful)")
        if not (self.actual or "").strip():
            problems.append("'actual' is required ('unknown' if not meaningful)")
        if self.severity not in SEVERITIES:
            problems.append(f"'severity' must be one of {'/'.join(SEVERITIES)}")
        if len((self.proposed_done_when or "").strip()) < MIN_DONE_WHEN_CHARS:
            problems.append(
                "'proposed_done_when' must be a draft completion contract "
                f"(≥ {MIN_DONE_WHEN_CHARS} chars)"
            )
        if problems:
            raise ValueError("machine finding rejected: " + "; ".join(problems))


# ---- rendering (finding → issue) --------------------------------------------

def _truncated(text: str, cap: int = EVIDENCE_MAX_CHARS) -> str:
    """Deterministic cut with an explicit marker — bounded coverage says so out
    loud (constitution VI); never an LLM summary (FR-007)."""
    text = (text or "").strip()
    if len(text) <= cap:
        return text
    omitted = len(text) - cap
    return text[:cap] + f"… [truncated: {omitted} chars omitted]"


def render_issue_title(finding: MachineFinding) -> str:
    head = finding.title.strip().splitlines()[0].strip()
    return f"[machine] {finding.source}: {head}"[:_TITLE_MAX]


def render_issue_body(finding: MachineFinding) -> str:
    """The schema-v1 body — canonical section order per the contract; the
    metadata comment line is the machine entry point."""
    ref = (finding.spec_ref or "").strip()
    spec_line = f"- **Spec scenario:** {ref}\n" if ref else ""
    return (
        "> Machine-filed by devclaw via the issue doorway. This issue is the durable,\n"
        "> gradeable record of a machine-found problem; dispatch stays human-gated.\n\n"
        f"<!-- devclaw-machine-issue v{SCHEMA_VERSION} "
        f"fingerprint={_quote(finding.fingerprint, safe='')} source={finding.source} "
        f"severity={finding.severity} -->\n\n"
        f"## Source\n\n{finding.source}\n\n"
        f"## Evidence\n\n{_truncated(finding.evidence)}\n\n"
        "## Expected vs actual\n\n"
        f"- **Expected:** {finding.expected.strip()}\n"
        f"- **Actual:** {finding.actual.strip()}\n"
        f"{spec_line}\n"
        f"## Severity\n\n`{finding.severity}`\n\n"
        f"## Proposed done-when\n\n{finding.proposed_done_when.strip()}\n"
    )


def _iso(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def occurrence_comment(occurrence: int, evidence: str, now_ms: int) -> str:
    return f"**Occurrence** {occurrence} — {_iso(now_ms)}\n\n{_truncated(evidence)}\n"


def recurrence_comment(occurrence: int, evidence: str, now_ms: int) -> str:
    return (
        f"**Recurrence** (regression) — previously closed; "
        f"occurrence {occurrence}, {_iso(now_ms)}\n\n{_truncated(evidence)}\n"
    )


# ---- parsing (issue → finding) ----------------------------------------------

def parse_machine_issue(body: str, title: Optional[str] = None) -> tuple[MachineFinding, int]:
    """The contract's extraction rules: ONE regex for the metadata line, then
    ``## ``-headed sections in canonical order. Raises ``ValueError`` on a body
    that is not a machine-filed issue or is missing a section — a consumer must
    dispatch on the returned schema version before assuming section semantics.

    The finding's title lives on the ISSUE title (``[machine] <source>:
    <title>``), not in the body — pass it to recover the original; without it
    the parsed finding's ``title`` falls back to the source."""
    m = _META_RE.search(body or "")
    if m is None:
        raise ValueError("not a machine-filed issue: metadata line missing")
    version = int(m.group("version"))

    def _section(name: str) -> str:
        sm = re.search(
            rf"^## {re.escape(name)}\n\n(?P<content>.*?)(?=^## |\Z)",
            body,
            re.MULTILINE | re.DOTALL,
        )
        if sm is None:
            raise ValueError(f"machine issue missing section: {name}")
        return sm.group("content").strip()

    _section("Source")  # presence-validated; the metadata line is authoritative
    eva = _section("Expected vs actual")

    def _bullet(label: str) -> Optional[str]:
        bm = re.search(rf"^- \*\*{re.escape(label)}:\*\* (?P<v>.*)$", eva, re.MULTILINE)
        return bm.group("v").strip() if bm else None

    expected = _bullet("Expected")
    actual = _bullet("Actual")
    if expected is None or actual is None:
        raise ValueError("machine issue missing Expected/Actual bullets")

    source = m.group("source")
    head = source
    if title:
        tm = re.match(rf"^\[machine\] {re.escape(source)}: (?P<head>.+)$", title.strip())
        if tm:
            head = tm.group("head")

    finding = MachineFinding(
        source=source,
        fingerprint=_unquote(m.group("fp")),
        title=head,
        evidence=_section("Evidence"),
        expected=expected,
        actual=actual,
        severity=m.group("sev"),
        proposed_done_when=_section("Proposed done-when"),
        spec_ref=_bullet("Spec scenario"),
    )
    return finding, version


# ---- the injectable GitHub adapter (tests pass a fake) ----------------------

class GhAdapter(Protocol):
    async def ensure_label(self, repo: str, name: str) -> None: ...
    async def create_issue(
        self, repo: str, *, title: str, body: str, labels: list[str]
    ) -> Optional[int]: ...
    async def comment_issue(self, repo: str, number: int, *, body: str) -> bool: ...
    async def reopen_issue(self, repo: str, number: int, *, comment: str) -> bool: ...


class GhCli:
    """Real adapter: shells ``gh`` host-side with the ``GITHUB_TOKEN``
    credential (never ``ANTHROPIC_*`` — the OAuth-only invariant is untouched).
    Fail-loud-not-fatal: a GitHub hiccup logs and returns a falsey result so
    ``file_finding`` turns it into a ``failed`` outcome — it never wedges the
    calling edge."""

    async def ensure_label(self, repo: str, name: str) -> None:
        await _run("gh", "label", "create", name, "--repo", repo, "--force")

    async def create_issue(
        self, repo: str, *, title: str, body: str, labels: list[str]
    ) -> Optional[int]:
        args = ["gh", "issue", "create", "--repo", repo, "--title", title, "--body", body]
        for lbl in labels:
            args += ["--label", lbl]
        rc, out = await _run(*args)
        if rc != 0:
            sys.stderr.write(f"issue-doorway: create failed on {repo}: {out}\n")
            return None
        tail = out.strip().rstrip("/").rsplit("/", 1)[-1]
        return int(tail) if tail.isdigit() else None

    async def comment_issue(self, repo: str, number: int, *, body: str) -> bool:
        rc, out = await _run(
            "gh", "issue", "comment", str(number), "--repo", repo, "--body", body
        )
        if rc != 0:
            sys.stderr.write(f"issue-doorway: comment #{number} failed on {repo}: {out}\n")
            return False
        return True

    async def reopen_issue(self, repo: str, number: int, *, comment: str) -> bool:
        rc, out = await _run("gh", "issue", "reopen", str(number), "--repo", repo)
        if rc != 0:
            # Already-open is a tolerated no-op: the ledger reconciles from the
            # actual GitHub result (data-model.md drift rule) — but only when
            # the comment still lands.
            if "already open" not in (out or "").lower():
                sys.stderr.write(
                    f"issue-doorway: reopen #{number} failed on {repo}: {out}\n"
                )
                return False
        return await self.comment_issue(repo, number, body=comment)


# ---- filing (the doorway verb) ----------------------------------------------

@dataclass
class FilingOutcome:
    #: ``filed`` (new issue) / ``updated`` (occurrence on an open issue) /
    #: ``reopened`` (recurrence after close) / ``failed``.
    action: str
    issue_number: Optional[int] = None
    #: on ``failed``: the actionable cause.
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.action != "failed"


def _fail(
    store, repo: str, finding: MachineFinding, reason: str
) -> FilingOutcome:
    """FR-006: the failure is loud on BOTH surfaces — the caller's outcome and
    the problems catalog. The finding stays owned by the originating mechanism
    (it re-fires on its next edge; the doorway does not queue)."""
    sys.stderr.write(
        f"issue-doorway: filing {finding.fingerprint} on {repo} failed: {reason}\n"
    )
    record = getattr(store, "record_problem", None)
    if record is not None:
        record(
            category="delivery",
            kind="issue_filing_failed",
            message=f"{finding.source} → {repo}: {reason}",
            recovered=False,
        )
    return FilingOutcome(action="failed", reason=reason)


async def file_finding(
    finding: MachineFinding,
    *,
    repo: str,
    store,
    gh: Optional[GhAdapter] = None,
    labels: Sequence[str] = (),
    now_ms: Optional[int] = None,
) -> FilingOutcome:
    """File one machine finding on ``repo`` — THE one verb every machine
    producer calls. Dedup rides the ``machine_issues`` ledger (single writer:
    the state store); ``labels`` are caller pass-through stamped alongside the
    doorway marker. Mechanical end to end — zero LLM."""
    gh = gh or GhCli()
    now = _now_ms() if now_ms is None else now_ms
    try:
        row = store.machine_issue_get(repo, finding.fingerprint)
        if row is None:
            all_labels = [MACHINE_LABEL, *labels]
            for lbl in all_labels:
                await gh.ensure_label(repo, lbl)
            number = await gh.create_issue(
                repo,
                title=render_issue_title(finding),
                body=render_issue_body(finding),
                labels=all_labels,
            )
            if number is None:
                return _fail(store, repo, finding, "issue creation failed (gh)")
            store.machine_issue_record(
                repo, finding.fingerprint,
                issue_number=number, issue_state="open",
                source=finding.source, schema_version=SCHEMA_VERSION, now_ms=now,
            )
            return FilingOutcome(action="filed", issue_number=number)

        number = int(row["issue_number"])
        occurrence = int(row["occurrence_count"]) + 1
        if row["issue_state"] == "open":
            if not await gh.comment_issue(
                repo, number, body=occurrence_comment(occurrence, finding.evidence, now)
            ):
                return _fail(store, repo, finding, f"occurrence comment on #{number} failed")
            action = "updated"
        else:
            if not await gh.reopen_issue(
                repo, number, comment=recurrence_comment(occurrence, finding.evidence, now)
            ):
                return _fail(store, repo, finding, f"reopen of #{number} failed")
            action = "reopened"
        store.machine_issue_record(
            repo, finding.fingerprint,
            issue_number=number, issue_state="open",
            source=finding.source, schema_version=SCHEMA_VERSION, now_ms=now,
        )
        return FilingOutcome(action=action, issue_number=number)
    except Exception as exc:  # noqa: BLE001 — a filing crash must surface, not propagate
        return _fail(store, repo, finding, f"{type(exc).__name__}: {exc}")
