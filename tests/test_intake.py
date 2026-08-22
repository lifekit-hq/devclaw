"""file_intake — Stage 1 of the single intake doorway (proposal: single-intake-doorway).

Named regression tests for the doorway behaviors: synchronous shape/project
rejection, the labeled-issue receipt, provenance stamping, and loud failure
when filing cannot produce a receipt. Zero network — the gh adapter is a fake,
per the ``goal/self_issue.py`` house pattern.
"""

from __future__ import annotations

import asyncio

import pytest

from devclaw import intake
from devclaw.project_registry import ProjectRegistry


@pytest.fixture()
def registry(tmp_path):
    reg = ProjectRegistry(str(tmp_path / "devclaw.db"))
    reg.create(
        id="finance-sentry",
        name="Finance Sentry",
        repo_url="https://github.com/lifekit-hq/finance-sentry.git",
        workspace_dir=str(tmp_path / "ws"),
    )
    return reg


class FakeGh:
    """Records calls; returns a canned URL (or None to simulate a gh failure)."""

    def __init__(self, url="https://github.com/lifekit-hq/finance-sentry/issues/7"):
        self.url = url
        self.labels_ensured: list[tuple[str, str]] = []
        self.created: list[dict] = []

    async def ensure_label(self, repo, name):
        self.labels_ensured.append((repo, name))

    async def create_issue(self, repo, *, title, body, labels):
        self.created.append(
            {"repo": repo, "title": title, "body": body, "labels": labels}
        )
        return self.url


VALID = dict(
    project_id="finance-sentry",
    what="The MCP server returns stale account balances after a refresh.",
    done_when="A regression test proves refreshed balances are served, and the fix is merged.",
    asker="ledger",
    channel="a2a",
    now_ms=1_755_000_000_000,
)


def _file(registry, gh, **overrides):
    kwargs = {**VALID, **overrides}
    return asyncio.run(intake.file_intake(registry, gh=gh, **kwargs))


# ---- the receipt path -------------------------------------------------------

def test_file_intake_files_labeled_issue_and_returns_url_receipt(registry):
    gh = FakeGh()
    result = _file(registry, gh)
    assert result == {
        "issue_url": "https://github.com/lifekit-hq/finance-sentry/issues/7",
        "project_id": "finance-sentry",
        "repo": "lifekit-hq/finance-sentry",
        # the receipt reports the recorded extent claim back (spec 012 FR-010);
        # this ask supplied none, so it is None — never a defaulted number.
        "expected_increments": None,
    }
    # the intake label is ensured (idempotent) then applied — no per-repo
    # template is involved anywhere (proposal §4-O4).
    assert ("lifekit-hq/finance-sentry", intake.INTAKE_LABEL) in gh.labels_ensured
    (issue,) = gh.created
    assert issue["labels"] == [intake.INTAKE_LABEL]
    assert issue["title"].startswith("[intake] ")


def test_file_intake_stamps_provenance_server_side(registry):
    gh = FakeGh()
    _file(registry, gh)
    body = gh.created[0]["body"]
    assert "ledger" in body
    assert "a2a" in body
    assert "2025-08-12T12:00:00Z" in body  # now_ms rendered as the ISO stamp
    assert "recorded, not authenticated" in body
    assert "`finance-sentry` → `lifekit-hq/finance-sentry`" in body
    # the ask itself is the record
    assert "stale account balances" in body
    assert "regression test proves" in body


def test_file_intake_renders_omitted_context_as_dash_not_blank(registry):
    gh = FakeGh()
    _file(registry, gh, context=None)
    assert "## Context\n\n—" in gh.created[0]["body"]


# ---- synchronous rejection (stage-1 shape enforcement in python) ------------

def test_file_intake_rejects_unknown_project_synchronously(registry):
    gh = FakeGh()
    with pytest.raises(intake.IntakeError, match="unknown project 'nope'"):
        _file(registry, gh, project_id="nope")
    assert gh.created == []  # rejected before any gh call


def test_file_intake_rejects_blank_what_short_done_when_and_bad_channel_at_once(registry):
    gh = FakeGh()
    with pytest.raises(intake.IntakeError) as exc:
        _file(registry, gh, what="  ", done_when="fix it", channel="smoke-signal")
    msg = str(exc.value)
    assert "'what' is required" in msg
    assert "'done_when'" in msg
    assert "'channel'" in msg
    assert gh.created == []


def test_file_intake_rejects_project_without_github_repo_url(registry):
    registry.create(id="local-only", name="Local", workspace_dir="/tmp/x")
    gh = FakeGh()
    with pytest.raises(intake.IntakeError, match="no GitHub repo_url"):
        _file(registry, gh, project_id="local-only")
    assert gh.created == []


# ---- loud failure: no fake receipts -----------------------------------------

def test_file_intake_fails_loud_when_gh_produces_no_url(registry):
    gh = FakeGh(url=None)
    with pytest.raises(intake.IntakeError, match="no receipt was produced"):
        _file(registry, gh)


# ---- pure helpers -----------------------------------------------------------

def test_repo_slug_handles_https_ssh_and_bare_forms():
    assert intake.repo_slug("https://github.com/o/n.git") == "o/n"
    assert intake.repo_slug("https://github.com/o/n") == "o/n"
    assert intake.repo_slug("git@github.com:o/n.git") == "o/n"
    assert intake.repo_slug("https://gitlab.com/o/n") is None
    assert intake.repo_slug(None) is None
    assert intake.repo_slug("") is None


def test_issue_title_is_first_line_only_and_bounded():
    long_first = "x" * 500
    t = intake.issue_title(f"{long_first}\nsecond line")
    assert t.startswith("[intake] x")
    assert len(t) <= 240
    assert "second line" not in t


# ---- spec 012 US3: the filer's expected-increment claim ---------------------
# The count is the FILER's claim, recorded verbatim in the issue body — the
# durable record grading reads back (never re-derives). An absent claim is
# recorded as "unstated" and surfaced later; it is never defaulted to a number.

def test_file_intake_records_the_filers_expected_increment_claim_and_basis(registry):
    gh = FakeGh()
    _file(
        registry,
        gh,
        expected_increments=3,
        increment_basis="three independent surfaces: the API handler, the store, the UI",
    )
    body = gh.created[0]["body"]
    assert "## Expected increments" in body
    assert "**Claimed by the filer:** 3" in body
    assert "**Basis:** three independent surfaces" in body
    # the claim reads back out of the body byte-identically — this, not a model,
    # is where a re-grade's recorded count comes from (SC-005b).
    count, basis, stated = intake.parse_expected_increments(body)
    assert (count, stated) == (3, True)
    assert basis.startswith("three independent surfaces")


def test_file_intake_records_unstated_extent_rather_than_defaulting_to_one(registry):
    gh = FakeGh()
    _file(
        registry,
        gh,
        increment_basis="cannot tell without reading the migration history",
    )
    body = gh.created[0]["body"]
    assert f"**Claimed by the filer:** {intake.UNSTATED_INCREMENTS}" in body
    assert "**Claimed by the filer:** 1" not in body
    count, basis, stated = intake.parse_expected_increments(body)
    assert count is None and stated is True
    assert basis.startswith("cannot tell")


def test_file_intake_rejects_an_increment_count_with_no_basis(registry):
    gh = FakeGh()
    with pytest.raises(intake.IntakeError, match="increment_basis"):
        _file(registry, gh, expected_increments=4)
    assert gh.created == []


def test_file_intake_rejects_a_non_positive_increment_count(registry):
    gh = FakeGh()
    with pytest.raises(intake.IntakeError, match="at least 1"):
        _file(
            registry,
            gh,
            expected_increments=0,
            increment_basis="a basis long enough to pass the bar",
        )
    assert gh.created == []


def test_parse_expected_increments_reads_back_the_claim_and_absence():
    # a hand-written issue (spec 009 universal adoption) has no section at all:
    # the extent is UNRECORDED, which is a different state from "unstated".
    assert intake.parse_expected_increments(
        "## What\n\nsomething\n\n## Done when\n\nit works\n"
    ) == (None, "", False)
    # a section naming an unparseable claim still counts as answered
    count, basis, stated = intake.parse_expected_increments(
        "## Expected increments\n\n"
        "- **Claimed by the filer:** a handful\n"
        "- **Basis:** hard to say\n\n"
        "## Provenance\n\n- x\n"
    )
    assert (count, basis, stated) == (None, "hard to say", True)
