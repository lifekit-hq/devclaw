"""Spec 014 US3 — the problems catalog files through the doorway: the issue
gains the schema while the catalog's linkage/lifecycle behave exactly as
before."""

from __future__ import annotations

import asyncio

import pytest

from devclaw import issue_doorway as dw
from devclaw.goal import self_issue as si
from devclaw.state_store import StateStore
from devclaw.state_store.problems import problem_lifecycle


@pytest.fixture
def store(tmp_path):
    return StateStore(str(tmp_path / "migration.db"))


class FakeGh:
    """The self_issue adapter surface + the doorway's create subset."""

    def __init__(self):
        self.labels: list[tuple[str, str]] = []
        self.created: list[dict] = []
        self._next = 100

    async def ensure_label(self, repo, name):
        self.labels.append((repo, name))

    async def create_issue(self, repo, *, title, body, labels):
        self._next += 1
        self.created.append(
            {"repo": repo, "title": title, "body": body, "labels": labels,
             "number": self._next}
        )
        return self._next

    async def reopen_issue(self, repo, number, *, comment):
        return True

    async def close_issue(self, repo, number, *, comment):
        return True


def _seed_recurring_problem(store, *, end_ms: int) -> str:
    """A terminal problem active in the window; returns its fingerprint."""
    store.record_problem(
        category="task_fail", kind="daytime failure",
        message="broken during a steered daytime run", recovered=False,
    )
    (p,) = store.list_problems(include_issue=True)
    # pin the window membership so threshold=1 files on this cycle
    store._db.execute(
        "UPDATE problems SET first_seen_ms = ?, last_seen_ms = ?",
        (end_ms - 1000, end_ms - 500),
    )
    store._commit()
    return p["fingerprint"]


def test_catalog_filing_produces_schema_issue_with_legacy_labels_and_linkage(store):
    end = 2_000_000
    fp = _seed_recurring_problem(store, end_ms=end)
    gh = FakeGh()

    res = asyncio.run(si.run_self_issue_filing(
        store, cycle_key="c1", start_ms=end - 10_000, end_ms=end, now_ms=end,
        repo="o/r", gh=gh, threshold=1,
    ))

    (created,) = gh.created
    assert res.filed == [created["number"]]

    # the schema: body parses via the doorway contract, source is the catalog
    parsed, version = dw.parse_machine_issue(created["body"], created["title"])
    assert version == dw.SCHEMA_VERSION
    assert parsed.source == si.DOORWAY_SOURCE
    assert parsed.fingerprint == fp
    assert parsed.proposed_done_when  # a draft contract a fixing goal could adopt

    # the legacy labels ride through unchanged (Stage-2 pickup + console)
    assert si.SELF_FILED_LABEL in created["labels"]
    assert "class:task_fail" in created["labels"]
    assert dw.MACHINE_LABEL in created["labels"]

    # the catalog row links the issue exactly as before
    (p,) = store.list_problems(include_issue=True)
    assert p["issue_number"] == created["number"]
    assert p["issue_state"] == "open"
    assert problem_lifecycle(p) == "filed"

    # and the doorway ledger carries the filing too (the catalog is now just
    # another producer)
    row = store.machine_issue_get("o/r", fp)
    assert row is not None and row["issue_number"] == created["number"]


def test_catalog_filing_failure_stays_loud_and_unlinked(store):
    end = 2_000_000
    _seed_recurring_problem(store, end_ms=end)

    class BrokenGh(FakeGh):
        async def create_issue(self, repo, *, title, body, labels):
            return None

    res = asyncio.run(si.run_self_issue_filing(
        store, cycle_key="c1", start_ms=end - 10_000, end_ms=end, now_ms=end,
        repo="o/r", gh=BrokenGh(), threshold=1,
    ))
    assert res.filed == []
    probs = store.list_problems(include_issue=True)
    # the original problem stays unlinked; the filing failure was itself recorded
    assert any(p["kind"] == "issue_filing_failed" for p in probs)
    assert all(p["issue_number"] is None for p in probs)
