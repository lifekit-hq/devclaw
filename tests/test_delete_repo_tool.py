"""The ``delete_repo`` MCP tool — the teardown counterpart of ``create_repo``.

The delivery layer's contract (exact confirm echo, loud on unknown repos,
missing-scope hint surfaced) is pinned in ``test_repo.py``; these pins guard the
tool wrapper's two EXTRA gates, both checked BEFORE any gh call:

1. **Ownership** — devclaw only deletes repos it created itself: ``create_repo``
   records every stood-up repo in the registry's managed-repo ledger, and
   ``delete_repo`` refuses anything not in it. A pre-existing, human-owned repo
   (finance-sentry) is structurally undeletable over MCP, whatever confirm
   string is passed.
2. **Reference** — a repo still referenced by a registered project is refused;
   ``delete_project`` first, then the repo.
"""

from __future__ import annotations

import json

import pytest

from devclaw.project_registry import ProjectRegistry
from devclaw.server import tools as _tools
from fastmcp.exceptions import ToolError


@pytest.fixture
def registry(tmp_path):
    return ProjectRegistry(str(tmp_path / "devclaw.db"))


@pytest.fixture(autouse=True)
def _patch_registry(registry, monkeypatch):
    # tools.py binds `registry` at import; point it at a throwaway one. Pin the
    # owner so full_slug derivation is deterministic in tests.
    monkeypatch.setattr(_tools.delivery, "registry", registry)
    monkeypatch.setenv("DEVCLAW_GITHUB_OWNER", "dsdevq")
    return registry


async def test_delete_repo_tool_refuses_repos_devclaw_did_not_create(
    registry, monkeypatch
):
    """The ownership pin: finance-sentry exists on the account but was never
    created by devclaw — no confirm string may delete it."""

    async def never(*args, **kwargs):
        raise AssertionError("gh must not be reached for an unmanaged repo")

    monkeypatch.setattr(_tools.delivery._repo, "delete_repo", never)

    with pytest.raises(ToolError, match="managed-repo ledger"):
        await _tools.delete_repo("finance-sentry", confirm="dsdevq/finance-sentry")


async def test_create_repo_tool_records_provenance_only_when_it_created(
    registry, monkeypatch
):
    """created=True enters the ledger; existed=True (someone else's repo that
    happened to share the name) must NOT become deletable."""
    results = iter(
        [
            {"created": True, "existed": False, "repo": "dsdevq/Scratch", "clone_url": "u"},
            {"created": False, "existed": True, "repo": "dsdevq/theirs", "clone_url": "u"},
        ]
    )

    async def fake_create(name, **kwargs):
        return next(results)

    monkeypatch.setattr(_tools.delivery._repo, "create_repo", fake_create)

    await _tools.create_repo("scratch")
    await _tools.create_repo("theirs")

    assert registry.is_managed_repo("dsdevq/scratch")  # case-insensitive on purpose
    assert not registry.is_managed_repo("dsdevq/theirs")


async def test_delete_repo_tool_refuses_repo_still_referenced_by_project(
    registry, monkeypatch
):
    # Managed (devclaw created it) but still registered as a project — the
    # reference gate must fire. Case-insensitive: GitHub slugs are.
    registry.record_managed_repo("dsdevq/ledger")
    registry.create(
        id="ledger", name="Ledger", repo_url="https://github.com/dsdevq/Ledger.git"
    )

    async def never(*args, **kwargs):
        raise AssertionError("gh must not be reached while a project references the repo")

    monkeypatch.setattr(_tools.delivery._repo, "delete_repo", never)

    with pytest.raises(ToolError, match="delete_project"):
        await _tools.delete_repo("ledger", confirm="dsdevq/ledger")


async def test_delete_repo_tool_guard_also_matches_confirm_slug_after_rename(
    registry, monkeypatch
):
    # gh follows renames: deleting by an OLD name resolves to the canonical repo,
    # which `confirm` must echo. A project referencing the CANONICAL name must
    # still trip the guard even though the input name no longer matches it.
    registry.record_managed_repo("dsdevq/old-ledger")
    registry.create(
        id="ledger", name="Ledger", repo_url="https://github.com/dsdevq/new-ledger.git"
    )

    async def never(*args, **kwargs):
        raise AssertionError("gh must not be reached while a project references the repo")

    monkeypatch.setattr(_tools.delivery._repo, "delete_repo", never)

    with pytest.raises(ToolError, match="delete_project"):
        await _tools.delete_repo("old-ledger", confirm="dsdevq/new-ledger")


async def test_delete_repo_tool_deletes_managed_repo_and_retires_its_ledger_entry(
    registry, monkeypatch
):
    registry.record_managed_repo("dsdevq/scratch")
    seen = {}

    async def fake_delete(name, *, confirm=""):
        seen["name"], seen["confirm"] = name, confirm
        return {"deleted": True, "repo": "dsdevq/scratch"}

    monkeypatch.setattr(_tools.delivery._repo, "delete_repo", fake_delete)

    out = json.loads(await _tools.delete_repo("scratch", confirm="dsdevq/scratch"))

    assert out == {"deleted": True, "repo": "dsdevq/scratch"}
    assert seen == {"name": "scratch", "confirm": "dsdevq/scratch"}
    # the ledger entry dies with the repo — a future same-name repo by someone
    # else must not inherit deletability
    assert not registry.is_managed_repo("dsdevq/scratch")


async def test_delete_repo_tool_requires_a_name():
    with pytest.raises(ToolError, match="requires a name"):
        await _tools.delete_repo("")
