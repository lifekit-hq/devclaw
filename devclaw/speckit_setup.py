"""Speckit adopt-or-install — the US2 onboard substrate (spec 008).

Every repo devclaw works must use speckit as its planning spine. Two branches,
decided by the **committed** ``.specify/`` directory (never the gitignored
per-checkout ``feature.json``):

- **committed ``.specify/`` present ⇒ ADOPT** — the repo already uses speckit;
  record it and move on. Onboard writes **no** ``PLAN.md`` and opens **no**
  scaffolding PR.
- **absent ⇒ INSTALL** — generate the ``.specify/`` scaffold and open a
  **reviewable PR** through the existing delivery path (``delivery/``). **Never**
  a direct/silent commit to the default branch (FR-003, Principle VI).

A repo whose install PR is still open is **not** run for feature work — no
half-installed execution (spec Edge Case). That gate is :func:`open_install_pr`,
consulted at dispatch time (never on the idle tick path — Principle III).

All git/gh interaction here is a subprocess at onboard/dispatch time; the
detectors are best-effort and never raise (a git/gh hiccup degrades to
"unknown", it never wedges a tool call). Only :func:`install_speckit_pr` — the
explicit install action — surfaces a real failure.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from .delivery import deliver_change  # module global so tests can patch it

#: The deterministic branch the install PR lands on, so the dispatch gate can
#: find an open install PR by head without any persisted state.
INSTALL_BRANCH = "devclaw/install-speckit"

#: Reusable, repo-agnostic parts of a ``.specify/`` install. Excludes the
#: gitignored per-checkout state (``feature.json``) and devclaw-specific
#: manifests (``init-options.json`` / ``integration.json`` / ``integrations/``).
_SCAFFOLD_DIRS = ("templates", "scripts", "workflows")
_SCAFFOLD_FILES = (".gitignore",)


def _speckit_source() -> Path:
    """devclaw's own vendored ``.specify/`` — the scaffold source of truth."""
    return Path(__file__).resolve().parent.parent / ".specify"


async def _run(*args: str, cwd: str) -> tuple[int, str]:
    """Run a command, return (exit_code, combined output). Never raises."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=cwd,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
    except OSError as exc:
        return 127, f"{args[0]} not runnable: {exc}"
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", "replace").strip()


async def has_committed_speckit(workspace_dir: str) -> bool:
    """True iff the repo has a **committed** ``.specify/`` directory at HEAD.

    Uses ``git ls-tree`` (the committed tree), NOT the working tree — a scaffold
    that's only staged/uncommitted on a side branch must not read as adopted.
    Best-effort: any git failure ⇒ False (treated as absent, the safe default —
    it triggers install detection, not a silent skip)."""
    rc, out = await _run(
        "git", "ls-tree", "HEAD", "--name-only", ".specify", cwd=workspace_dir
    )
    return rc == 0 and out.strip() == ".specify"


async def open_install_pr(workspace_dir: str) -> str | None:
    """The URL of an OPEN speckit-install PR for this repo, or None.

    A ``gh pr list --head <INSTALL_BRANCH> --state open`` query — a subprocess at
    dispatch time (never on the idle tick path). Best-effort: any gh failure ⇒
    None. A None here does NOT by itself green-light feature work: the gate pairs
    it with :func:`local_install_branch_exists` + :func:`gh_unavailable` to fail
    CLOSED when gh couldn't actually confirm 'no open PR' (a gh hiccup must not
    silently admit half-installed execution)."""
    rc, out = await _run(
        "gh", "pr", "list", "--head", INSTALL_BRANCH, "--state", "open",
        "--json", "url", "--jq", ".[0].url // empty",
        cwd=workspace_dir,
    )
    if rc != 0:
        return None
    return out.strip() or None


async def local_install_branch_exists(workspace_dir: str) -> bool:
    """True iff the deterministic install branch exists LOCALLY — concrete
    evidence :func:`install_speckit_pr` scaffolded a speckit install in THIS
    checkout. Cheap (a local ``git branch --list``, no network); the goal-path
    hold and the fail-closed gh-uncertainty branch key off it so an ordinary
    legacy/plain repo is never probed over the network. Never raises."""
    rc, out = await _run("git", "branch", "--list", INSTALL_BRANCH, cwd=workspace_dir)
    return rc == 0 and out.strip() != ""


async def gh_unavailable(workspace_dir: str) -> bool:
    """True iff ``gh`` cannot answer PR queries here (missing binary, auth
    expiry, network). Used to fail CLOSED on install-state uncertainty rather
    than silently proceed: a non-zero ``gh auth status`` ⇒ True. Best-effort."""
    rc, _out = await _run("gh", "auth", "status", cwd=workspace_dir)
    return rc != 0


async def feature_block_reason(workspace_dir: str) -> "str | None":
    """An actionable reason feature work must NOT run in this repo yet (a speckit
    install is pending), else ``None`` — the GOAL-path (heartbeat) gate for US2's
    'no half-installed execution', the durable-goal counterpart of
    :func:`devclaw.server.tools._block_if_speckit_pending`.

    Cheap and inert for ordinary repos: probes ONLY when a LOCAL install branch
    exists (concrete evidence onboard scaffolded here), so a plain/legacy repo
    returns ``None`` after a single ``git branch`` and a stubbed-suite goal tick
    pays nothing. Fail-CLOSED on gh-uncertainty (unlike the detection guards) —
    running feature work half-installed is exactly the failure this prevents."""
    if not await local_install_branch_exists(workspace_dir):
        return None
    if await has_committed_speckit(workspace_dir):
        return None  # the install merged and landed here — good to go
    url = await open_install_pr(workspace_dir)
    if url:
        return (f"the speckit install PR ({url}) is still open — merge it first "
                f"(no half-installed execution)")
    if await gh_unavailable(workspace_dir):
        return ("cannot verify the speckit install PR state (gh unavailable) and a "
                "local install branch exists — resolve the install before feature work")
    return None


def scaffold_specify(workspace_dir: str) -> list[str]:
    """Write the ``.specify/`` scaffold into ``workspace_dir`` and return the
    list of created top-level paths (relative). Copies devclaw's vendored
    templates/scripts/workflows + ``.gitignore``, and seeds
    ``memory/constitution.md`` from the constitution TEMPLATE (a placeholder for
    the repo to fill via ``/speckit-constitution`` — never devclaw's own filled
    constitution). Raises if the vendored source is missing (loud, not silent)."""
    src = _speckit_source()
    if not src.is_dir():
        raise FileNotFoundError(
            f"cannot install speckit: vendored source {src} is missing"
        )
    dest = Path(workspace_dir) / ".specify"
    created: list[str] = []

    for name in _SCAFFOLD_DIRS:
        s = src / name
        if s.is_dir():
            shutil.copytree(s, dest / name, dirs_exist_ok=True)
            created.append(f".specify/{name}/")
    for name in _SCAFFOLD_FILES:
        s = src / name
        if s.is_file():
            (dest).mkdir(parents=True, exist_ok=True)
            shutil.copy2(s, dest / name)
            created.append(f".specify/{name}")

    # Seed a placeholder constitution from the template so the new repo has a
    # constitution to fill, without importing devclaw's own filled one.
    tmpl = src / "templates" / "constitution-template.md"
    if tmpl.is_file():
        (dest / "memory").mkdir(parents=True, exist_ok=True)
        shutil.copy2(tmpl, dest / "memory" / "constitution.md")
        created.append(".specify/memory/constitution.md")

    return created


async def install_speckit_pr(workspace_dir: str, *, project_id: str | None = None) -> dict:
    """Install speckit into a bare repo as a **reviewable PR** (never a silent
    commit to the default branch). Idempotent: if an install PR is already open,
    returns it without re-scaffolding.

    Steps: check out the deterministic install branch, scaffold ``.specify/``
    (leaving it uncommitted), and hand off to ``delivery.deliver_change`` — the
    single commit→branch→push→PR path, which commits the scaffold on the branch
    and opens the PR. Returns the delivery verdict dict (``pr_url`` / ``branch``
    / ``error`` / ``created`` scaffold paths)."""
    existing = await open_install_pr(workspace_dir)
    if existing:
        return {"delivered": True, "pr_url": existing, "branch": INSTALL_BRANCH,
                "already_open": True, "created": []}

    # Capture the branch to restore FIRST: checking out the install branch and
    # never coming back would strand the shared workspace on it, and a later
    # dispatch reading HEAD's committed .specify/ would mistake the repo for
    # 'adopted' and run half-installed. Restored in ``finally`` — on success and
    # on any scaffold/deliver failure alike.
    rc0, orig = await _run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=workspace_dir)
    orig_branch = orig.strip() if rc0 == 0 and orig.strip() and orig.strip() != "HEAD" else None
    try:
        # Land the scaffold on the deterministic install branch (reset if a stale
        # one exists) so delivery reuses it and the gate can find the PR by head.
        rc, out = await _run("git", "checkout", "-B", INSTALL_BRANCH, cwd=workspace_dir)
        if rc != 0:
            return {"delivered": False, "pr_url": None, "branch": INSTALL_BRANCH,
                    "error": f"could not create install branch: {out[-200:]}", "created": []}

        created = scaffold_specify(workspace_dir)

        result = await deliver_change(
            workspace_dir=workspace_dir,
            task_id=f"install-speckit-{project_id or 'repo'}",
            goal="Install speckit as the execution substrate (.specify/ scaffold)",
            kind="onboard",
            target_branch=INSTALL_BRANCH,
        )
        result["created"] = created
        return result
    finally:
        if orig_branch:
            await _run("git", "checkout", orig_branch, cwd=workspace_dir)
