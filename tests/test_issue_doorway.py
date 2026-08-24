"""Spec 014 US1+US2 — the issue doorway: schema round-trip, fail-loud filing,
fingerprint idempotency, and the machine_issues ledger."""

from __future__ import annotations

import asyncio

import pytest

from devclaw import issue_doorway as dw
from devclaw.intake import parse_issue_fields
from devclaw.state_store import StateStore


@pytest.fixture
def store(tmp_path):
    return StateStore(str(tmp_path / "doorway.db"))


def _finding(**over) -> dw.MachineFinding:
    base = dict(
        source="deploy_smoke",
        fingerprint="abc123def",
        title="console returns 404 after deploy",
        evidence="curl -sf http://box:8000/console → HTTP 404\nexpected 200",
        expected="GET /console responds 200 with the bundle",
        actual="HTTP 404 from the launcher",
        severity="high",
        proposed_done_when=(
            "GET /console on the deployed instance responds 200 and the "
            "post-deploy smoke passes twice in a row"
        ),
    )
    base.update(over)
    return dw.MachineFinding(**base)


class FakeGh:
    """Recording fake for the doorway's GhAdapter."""

    def __init__(self, *, create_ok=True, comment_ok=True, reopen_ok=True):
        self.labels: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self.comments: list[tuple[str, int, str]] = []
        self.reopened: list[tuple[str, int, str]] = []
        self._create_ok = create_ok
        self._comment_ok = comment_ok
        self._reopen_ok = reopen_ok
        self._next_number = 41

    async def ensure_label(self, repo, name):
        self.labels.append((repo, name))

    async def create_issue(self, repo, *, title, body, labels):
        if not self._create_ok:
            return None
        self._next_number += 1
        self.created.append(
            {"repo": repo, "title": title, "body": body, "labels": labels,
             "number": self._next_number}
        )
        return self._next_number

    async def comment_issue(self, repo, number, *, body):
        if not self._comment_ok:
            return False
        self.comments.append((repo, number, body))
        return True

    async def reopen_issue(self, repo, number, *, comment):
        if not self._reopen_ok:
            return False
        self.reopened.append((repo, number, comment))
        return True


# ---- ledger (foundational) ---------------------------------------------------

def test_machine_issue_ledger_round_trip_and_occurrence_bump(store):
    assert store.machine_issue_get("o/r", "fp1") is None
    store.machine_issue_record(
        "o/r", "fp1", issue_number=7, issue_state="open",
        source="validator", schema_version=1, now_ms=1000,
    )
    row = store.machine_issue_get("o/r", "fp1")
    assert row["issue_number"] == 7
    assert row["issue_state"] == "open"
    assert row["occurrence_count"] == 1
    assert row["first_seen_ms"] == 1000

    store.machine_issue_record(
        "o/r", "fp1", issue_number=7, issue_state="open",
        source="validator", schema_version=1, now_ms=2000,
    )
    row = store.machine_issue_get("o/r", "fp1")
    assert row["occurrence_count"] == 2
    assert row["last_seen_ms"] == 2000
    assert row["first_seen_ms"] == 1000

    store.machine_issue_set_state("o/r", "fp1", "closed")
    assert store.machine_issue_get("o/r", "fp1")["issue_state"] == "closed"
    # state flip never touches the occurrence record
    assert store.machine_issue_get("o/r", "fp1")["occurrence_count"] == 2


# ---- US1: schema -------------------------------------------------------------

def test_render_parse_round_trip_is_field_identical():
    f = _finding(spec_ref="spec 015 US2 scenario 1")
    body = dw.render_issue_body(f)
    title = dw.render_issue_title(f)
    parsed, version = dw.parse_machine_issue(body, title)
    assert version == dw.SCHEMA_VERSION
    assert parsed == f


def test_body_carries_canonical_sections_in_order_and_version_line():
    body = dw.render_issue_body(_finding())
    order = [body.index(h) for h in (
        "## Source", "## Evidence", "## Expected vs actual",
        "## Severity", "## Proposed done-when",
    )]
    assert order == sorted(order)
    m = dw._META_RE.search(body)
    assert m is not None
    assert m.group("version") == "1"
    assert m.group("fp") == "abc123def"
    assert m.group("source") == "deploy_smoke"
    assert m.group("sev") == "high"


def test_unknown_is_stated_never_omitted():
    f = _finding(expected="unknown", actual="unknown")
    body = dw.render_issue_body(f)
    assert "- **Expected:** unknown" in body
    assert "- **Actual:** unknown" in body
    parsed, _ = dw.parse_machine_issue(body)
    assert parsed.expected == "unknown"


def test_evidence_truncation_is_deterministic_with_explicit_marker():
    long = "x" * (dw.EVIDENCE_MAX_CHARS + 500)
    body = dw.render_issue_body(_finding(evidence=long))
    assert "[truncated: 500 chars omitted]" in body


def test_invalid_finding_names_every_problem_at_once():
    with pytest.raises(ValueError) as exc:
        _finding(severity="urgent", proposed_done_when="fix it", fingerprint="")
    msg = str(exc.value)
    assert "severity" in msg and "proposed_done_when" in msg and "fingerprint" in msg


def test_fingerprint_with_spaces_round_trips_via_percent_encoding():
    """Producers own their fingerprint shape — the catalog's is
    `cat|kind|message` WITH spaces; the metadata line stays one-regex parseable."""
    fp = "task_fail|daytime failure|broken during a steered run"
    f = _finding(fingerprint=fp)
    body = dw.render_issue_body(f)
    m = dw._META_RE.search(body)
    assert m is not None and " " not in m.group("fp")
    parsed, _ = dw.parse_machine_issue(body)
    assert parsed.fingerprint == fp


def test_parse_rejects_non_machine_body():
    with pytest.raises(ValueError):
        dw.parse_machine_issue("## What\n\njust a human issue\n")


# ---- US1: filing + fail-loud -------------------------------------------------

def test_filing_creates_schema_issue_with_marker_and_passthrough_labels(store):
    gh = FakeGh()
    out = asyncio.run(dw.file_finding(
        _finding(), repo="o/r", store=store, gh=gh,
        labels=["devclaw:self-filed"], now_ms=1000,
    ))
    assert out.action == "filed" and out.ok
    (created,) = gh.created
    assert created["labels"] == [dw.MACHINE_LABEL, "devclaw:self-filed"]
    parsed, _ = dw.parse_machine_issue(created["body"], created["title"])
    assert parsed.fingerprint == "abc123def"
    row = store.machine_issue_get("o/r", "abc123def")
    assert row["issue_number"] == created["number"] and row["issue_state"] == "open"


def test_filing_failure_is_loud_never_silent(store):
    gh = FakeGh(create_ok=False)
    out = asyncio.run(dw.file_finding(_finding(), repo="o/r", store=store, gh=gh, now_ms=1000))
    assert out.action == "failed" and not out.ok
    assert out.reason
    # the failure lands in the problems catalog too (US1 scenario 3)
    probs = store.list_problems()
    assert any(p["kind"] == "issue_filing_failed" for p in probs)
    # and no ledger row pretends an issue exists
    assert store.machine_issue_get("o/r", "abc123def") is None


def test_doorway_body_is_gradeable_by_the_intake_fallback():
    """FR-008: no ## What section → regrade's spec-009 hand-written fallback
    (title + body = ask) applies; nothing in the body wedges the parser."""
    f = _finding()
    body = dw.render_issue_body(f)
    what, done_when, context = parse_issue_fields(body)
    assert what == ""  # not an intake-format issue → the fallback path fires
    ask = (dw.render_issue_title(f) + "\n\n" + body.strip()).strip()
    assert ask  # the composed ask regrade grades is non-empty
    assert "Proposed done-when" in ask  # the draft contract reaches the grader


# ---- US2: idempotency by fingerprint ----------------------------------------

def test_second_filing_of_open_fingerprint_updates_not_duplicates(store):
    gh = FakeGh()
    first = asyncio.run(dw.file_finding(_finding(), repo="o/r", store=store, gh=gh, now_ms=1000))
    second = asyncio.run(dw.file_finding(
        _finding(evidence="fresh evidence run 2"), repo="o/r", store=store, gh=gh, now_ms=2000,
    ))
    assert second.action == "updated"
    assert second.issue_number == first.issue_number
    assert len(gh.created) == 1  # SC-002: exactly one issue, ever
    ((_, number, comment),) = gh.comments
    assert number == first.issue_number
    assert comment.startswith("**Occurrence** 2")
    assert "fresh evidence run 2" in comment
    assert store.machine_issue_get("o/r", "abc123def")["occurrence_count"] == 2


def test_recurrence_after_close_reopens_marked_as_regression(store):
    gh = FakeGh()
    first = asyncio.run(dw.file_finding(_finding(), repo="o/r", store=store, gh=gh, now_ms=1000))
    store.machine_issue_set_state("o/r", "abc123def", "closed")
    again = asyncio.run(dw.file_finding(_finding(), repo="o/r", store=store, gh=gh, now_ms=2000))
    assert again.action == "reopened"
    assert again.issue_number == first.issue_number
    assert len(gh.created) == 1
    ((_, number, comment),) = gh.reopened
    assert number == first.issue_number
    assert comment.startswith("**Recurrence** (regression)")
    row = store.machine_issue_get("o/r", "abc123def")
    assert row["issue_state"] == "open" and row["occurrence_count"] == 2


def test_failed_occurrence_comment_fails_loud_and_keeps_ledger_untouched(store):
    gh = FakeGh()
    asyncio.run(dw.file_finding(_finding(), repo="o/r", store=store, gh=gh, now_ms=1000))
    bad = FakeGh(comment_ok=False)
    out = asyncio.run(dw.file_finding(_finding(), repo="o/r", store=store, gh=bad, now_ms=2000))
    assert out.action == "failed"
    assert store.machine_issue_get("o/r", "abc123def")["occurrence_count"] == 1
