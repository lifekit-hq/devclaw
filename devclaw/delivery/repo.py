"""Provision a GitHub repo — so devclaw can take on a *from-scratch* goal.

The goal layer's :func:`workspace.prepare_workspace` deliberately refuses to
``git init`` on its own and requires a ``repo_url`` to clone; :mod:`delivery`
needs an ``origin`` remote to push + open a PR. A build-from-scratch project has
neither until someone creates the repo. This module is that someone: it creates a
GitHub repo via ``gh`` (already installed + authed in the devclaw-mcp image, with
git's credential helper wired to ``gh auth git-credential``) and returns a clone
URL the goal can use.

``--add-readme`` is the load-bearing flag: it gives the new repo an initial commit
and a default branch (``main``), so the very next ``git clone`` + ``fetch`` +
``checkout`` in prepare_workspace succeeds and delivery can branch a PR against a
real base. Auth here is *repo write access* (the ``gh`` token), separate from the
Claude OAuth pillar (cognition billing) — same split as :mod:`delivery`.

:func:`delete_repo` is the teardown twin — retiring the scratch/bench repos this
module creates, behind an explicit confirm echo (deletion needs the extra
``delete_repo`` OAuth scope on the gh token besides).
"""

from __future__ import annotations

import re
from .. import config as _config
from ..procutil import run as _run

#: GitHub repo names: letters, digits, '.', '_', '-'. We slug the goal/idea into one.
_NAME_OK = re.compile(r"[^A-Za-z0-9._-]+")


class RepoError(RuntimeError):
    pass


def slug_repo_name(text: str, n: int = 60) -> str:
    """Turn an idea/goal-id into a valid GitHub repo name (no spaces/slashes)."""
    s = _NAME_OK.sub("-", text.strip()).strip("-._")
    return (s[:n].strip("-._") or "devclaw-project")


def _default_owner() -> str | None:
    """Owner for new repos. None → gh uses the authenticated user's account."""
    return _config.github_owner()


def full_slug(name: str, owner: str | None = None) -> str:
    """The owner-qualified slug create_repo/delete_repo operate on — one shared
    derivation so the provenance ledger and the gh calls can't disagree."""
    safe = slug_repo_name(name)
    owner = owner or _default_owner()
    return f"{owner}/{safe}" if owner else safe



async def _clone_url(slug: str) -> str | None:
    """Resolve a repo's HTTPS clone URL via gh (None if it can't be read)."""
    rc, out = await _run("gh", "repo", "view", slug, "--json", "url", "-q", ".url")
    if rc == 0 and out.strip().startswith("https://"):
        return out.strip() + ".git"
    return None


async def create_repo(
    name: str,
    *,
    private: bool = False,
    description: str = "",
    owner: str | None = None,
) -> dict:
    """Create a GitHub repo and return ``{created, existed, repo, clone_url}``.

    Public by default: on a billing-locked account, Actions on private repos
    never start (every run is ``startup_failure``), so a devclaw-managed repo
    is only CI-verifiable when public. Pass ``private=True`` explicitly for
    anything sensitive and accept that its CI may be dead.

    Idempotent: if the repo already exists it is returned (``existed=True``)
    rather than erroring, so re-running a goal setup is safe. Raises
    :class:`RepoError` only when creation genuinely fails (auth/network/quota).
    """
    slug = full_slug(name, owner)

    # Already there? Hand back its URL instead of failing the whole goal setup.
    if (existing := await _clone_url(slug)) is not None:
        return {"created": False, "existed": True, "repo": slug, "clone_url": existing}

    args = ["gh", "repo", "create", slug, "--add-readme",
            "--private" if private else "--public"]
    if description:
        args += ["--description", description]
    rc, out = await _run(*args)
    if rc != 0:
        raise RepoError(f"gh repo create failed: {out[-400:]}")

    clone_url = await _clone_url(slug) or _extract_clone_url(out, slug)
    if not clone_url:
        raise RepoError(f"repo created but could not resolve its clone URL: {out[-200:]}")
    return {"created": True, "existed": False, "repo": slug, "clone_url": clone_url}


def _extract_clone_url(text: str, slug: str) -> str | None:
    """Fallback: scrape the repo URL gh prints, else synthesize from the slug."""
    m = re.search(r"https://github\.com/[\w.\-/]+", text)
    if m:
        return m.group(0).rstrip("/") + (".git" if not m.group(0).endswith(".git") else "")
    if "/" in slug:  # owner/name known → safe to synthesize
        return f"https://github.com/{slug}.git"
    return None


async def delete_repo(name: str, *, confirm: str = "", owner: str | None = None) -> dict:
    """Delete a GitHub repo and return ``{deleted, repo}`` — create_repo's teardown twin.

    Irreversible, so it is deliberately hard to trip by accident:

    - ``confirm`` must echo the repo's canonical ``owner/name`` exactly as GitHub
      reports it. A mismatch (or omission) raises with the exact string to pass,
      so the caller always names precisely what dies before it does.
    - An unknown repo raises — a typo must never silently no-op (same contract
      as the registry's ``delete_project``).
    - ``gh repo delete`` needs the ``delete_repo`` OAuth scope; gh's own error
      (which carries the ``gh auth refresh -s delete_repo`` hint) is surfaced
      verbatim rather than swallowed.
    """
    slug = full_slug(name, owner)

    rc, out = await _run(
        "gh", "repo", "view", slug, "--json", "nameWithOwner", "-q", ".nameWithOwner"
    )
    canonical = out.strip()
    if rc != 0 or "/" not in canonical:
        raise RepoError(f"repo '{slug}' not found — nothing deleted: {out[-200:]}")
    if confirm != canonical:
        raise RepoError(
            f"refusing to delete '{canonical}': pass confirm='{canonical}' "
            "to prove you mean this exact repo"
        )
    rc, out = await _run("gh", "repo", "delete", canonical, "--yes")
    if rc != 0:
        raise RepoError(f"gh repo delete failed: {out[-400:]}")
    return {"deleted": True, "repo": canonical}
