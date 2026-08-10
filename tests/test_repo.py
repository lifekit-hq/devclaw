"""repo.create_repo / repo.delete_repo — provisioning and retiring GitHub repos.

_run is monkeypatched so no real gh/network is touched; we assert on the gh
argv it builds and on the idempotent/existing-repo branches. delete_repo's pins
guard the deliberate-teardown contract: exact confirm echo, loud on unknown
repos, gh's missing-scope hint surfaced verbatim."""
from __future__ import annotations

import pytest

from devclaw.delivery import repo


def test_slug_repo_name_makes_valid_names():
    assert repo.slug_repo_name("Build a Todo App!") == "Build-a-Todo-App"
    assert repo.slug_repo_name("  spaces  and/slashes  ") == "spaces-and-slashes"
    assert repo.slug_repo_name("") == "devclaw-project"
    assert repo.slug_repo_name("---") == "devclaw-project"


def test_extract_clone_url_scrapes_or_synthesizes():
    assert repo._extract_clone_url("created https://github.com/me/app", "me/app") == (
        "https://github.com/me/app.git"
    )
    # no URL in output but owner/name known → synthesize
    assert repo._extract_clone_url("ok", "me/app") == "https://github.com/me/app.git"
    assert repo._extract_clone_url("ok", "bare") is None


@pytest.mark.asyncio
async def test_create_repo_creates_when_absent(monkeypatch):
    """When the repo doesn't exist, it shells `gh repo create ... --add-readme`
    and returns the resolved clone URL."""
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str):
        calls.append(args)
        if args[:3] == ("gh", "repo", "view"):
            # first view (existence check) misses; later view resolves the URL
            if "--json" in args and any(c[:3] == ("gh", "repo", "create") for c in calls):
                return 0, "https://github.com/dsdevq/todo"
            return 1, "not found"
        if args[:3] == ("gh", "repo", "create"):
            return 0, "✓ Created repository dsdevq/todo on GitHub"
        return 1, "unexpected"

    monkeypatch.setattr(repo, "_run", fake_run)
    monkeypatch.setenv("DEVCLAW_GITHUB_OWNER", "dsdevq")

    out = await repo.create_repo("todo", private=True, description="d")

    assert out["created"] is True and out["existed"] is False
    assert out["repo"] == "dsdevq/todo"
    assert out["clone_url"] == "https://github.com/dsdevq/todo.git"
    create = next(c for c in calls if c[:3] == ("gh", "repo", "create"))
    assert "--add-readme" in create and "--private" in create
    assert "--description" in create


@pytest.mark.asyncio
async def test_create_repo_idempotent_when_exists(monkeypatch):
    """An existing repo is returned (existed=True), never re-created."""
    async def fake_run(*args: str):
        if args[:3] == ("gh", "repo", "view"):
            return 0, "https://github.com/dsdevq/todo"
        raise AssertionError(f"should not run {args}")

    monkeypatch.setattr(repo, "_run", fake_run)
    monkeypatch.setenv("DEVCLAW_GITHUB_OWNER", "dsdevq")

    out = await repo.create_repo("todo")

    assert out["existed"] is True and out["created"] is False
    assert out["clone_url"] == "https://github.com/dsdevq/todo.git"


@pytest.mark.asyncio
async def test_create_repo_raises_on_failure(monkeypatch):
    async def fake_run(*args: str):
        if args[:3] == ("gh", "repo", "view"):
            return 1, "not found"
        if args[:3] == ("gh", "repo", "create"):
            return 1, "HTTP 403: name already exists / no permission"
        return 1, "x"

    monkeypatch.setattr(repo, "_run", fake_run)
    monkeypatch.delenv("DEVCLAW_GITHUB_OWNER", raising=False)

    with pytest.raises(repo.RepoError):
        await repo.create_repo("todo")

# ---- delete_repo — the teardown twin ---------------------------------------


@pytest.mark.asyncio
async def test_delete_repo_refuses_without_exact_confirm(monkeypatch):
    """The confirm echo IS the safety: a mismatch raises with the exact canonical
    slug to pass, and `gh repo delete` is never reached."""
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str):
        calls.append(args)
        if args[:3] == ("gh", "repo", "view"):
            return 0, "dsdevq/ledger"
        raise AssertionError(f"should not run {args}")

    monkeypatch.setattr(repo, "_run", fake_run)
    monkeypatch.setenv("DEVCLAW_GITHUB_OWNER", "dsdevq")

    with pytest.raises(repo.RepoError, match="confirm='dsdevq/ledger'"):
        await repo.delete_repo("ledger", confirm="")
    with pytest.raises(repo.RepoError, match="confirm='dsdevq/ledger'"):
        await repo.delete_repo("ledger", confirm="ledger")  # bare name ≠ canonical

    assert all(c[:3] != ("gh", "repo", "delete") for c in calls)


@pytest.mark.asyncio
async def test_delete_repo_deletes_on_exact_confirm(monkeypatch):
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str):
        calls.append(args)
        if args[:3] == ("gh", "repo", "view"):
            return 0, "dsdevq/ledger"
        if args[:3] == ("gh", "repo", "delete"):
            return 0, "✓ Deleted repository dsdevq/ledger"
        return 1, "unexpected"

    monkeypatch.setattr(repo, "_run", fake_run)
    monkeypatch.setenv("DEVCLAW_GITHUB_OWNER", "dsdevq")

    out = await repo.delete_repo("ledger", confirm="dsdevq/ledger")

    assert out == {"deleted": True, "repo": "dsdevq/ledger"}
    delete = next(c for c in calls if c[:3] == ("gh", "repo", "delete"))
    assert delete == ("gh", "repo", "delete", "dsdevq/ledger", "--yes")


@pytest.mark.asyncio
async def test_delete_repo_unknown_repo_raises_never_noops(monkeypatch):
    """Same contract as the registry's delete_project: a typo raises loudly
    instead of silently no-opping."""
    calls: list[tuple[str, ...]] = []

    async def fake_run(*args: str):
        calls.append(args)
        return 1, "GraphQL: Could not resolve to a Repository"

    monkeypatch.setattr(repo, "_run", fake_run)
    monkeypatch.setenv("DEVCLAW_GITHUB_OWNER", "dsdevq")

    with pytest.raises(repo.RepoError, match="nothing deleted"):
        await repo.delete_repo("no-such-repo", confirm="dsdevq/no-such-repo")
    assert all(c[:3] != ("gh", "repo", "delete") for c in calls)


@pytest.mark.asyncio
async def test_delete_repo_surfaces_missing_scope_hint(monkeypatch):
    """A gh token without the delete_repo scope fails loudly WITH gh's own
    actionable hint (the `gh auth refresh` command), never swallowed."""
    async def fake_run(*args: str):
        if args[:3] == ("gh", "repo", "view"):
            return 0, "dsdevq/ledger"
        if args[:3] == ("gh", "repo", "delete"):
            return 1, (
                "HTTP 403: This API operation needs the \"delete_repo\" scope. "
                "To request it, run:  gh auth refresh -h github.com -s delete_repo"
            )
        return 1, "unexpected"

    monkeypatch.setattr(repo, "_run", fake_run)
    monkeypatch.setenv("DEVCLAW_GITHUB_OWNER", "dsdevq")

    with pytest.raises(repo.RepoError, match="gh auth refresh -h github.com -s delete_repo"):
        await repo.delete_repo("ledger", confirm="dsdevq/ledger")
