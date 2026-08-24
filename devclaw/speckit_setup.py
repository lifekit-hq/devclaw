"""Speckit adopt-or-install — the US2 onboard substrate (spec 008).

Every repo devclaw works must use speckit as its planning spine. Two branches,
decided by the **committed** ``.specify/`` directory (never the gitignored
per-checkout ``feature.json``):

- **committed ``.specify/`` present ⇒ ADOPT** — the repo already uses speckit;
  record it and move on. Onboard writes **no** plan file and opens **no**
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

import shutil
from pathlib import Path

from .delivery import deliver_change  # module global so tests can patch it
from .procutil import run as _run
from .project_manifest import (
    BOILERPLATE_REVISION,
    SCHEMA_VERSION,
    ManifestError,
    load_manifest,
    migrate_manifest,
    seed_manifest,
)

#: The deterministic branch the install PR lands on, so the dispatch gate can
#: find an open install PR by head without any persisted state.
INSTALL_BRANCH = "devclaw/install-speckit"

#: Reusable, repo-agnostic parts of a ``.specify/`` install. Excludes the
#: gitignored per-checkout state (``feature.json``) and devclaw-specific
#: manifests (``init-options.json`` / ``integration.json`` / ``integrations/``).
_SCAFFOLD_DIRS = ("templates", "scripts", "workflows")
_SCAFFOLD_FILES = (".gitignore",)


def _speckit_source() -> Path:
    """devclaw's own vendored speckit scaffold — the source of truth to copy.

    Two layouts must BOTH resolve, and only one of them used to (#588):

    - **installed** (wheel / the deployed container) — the scaffold ships
      *inside* the package as ``devclaw/_specify`` via the pyproject
      ``force-include``. It has to: ``.specify/`` sits at the repo root,
      outside ``packages = ["devclaw"]``, so nothing at that path ever reached
      the wheel and every installed devclaw failed to onboard.
    - **source checkout** (dev / CI) — ``.specify/`` at the repo root, a
      sibling of the package directory.

    The packaged copy wins when present. In a checkout it does not exist, so
    the root copy is used and stays the single editable source of truth.
    """
    return _resolve_speckit_source(Path(__file__).resolve().parent)


def _resolve_speckit_source(package_dir: Path) -> Path:
    """Pure resolver behind :func:`_speckit_source`, split out so both layouts
    are testable without a real install. ``package_dir`` is the directory the
    ``devclaw`` package lives in."""
    packaged = package_dir / "_specify"
    if packaged.is_dir():
        return packaged
    return package_dir.parent / ".specify"



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
    hold and the fail-closed gh-uncertainty branch key off it so a repo with
    no speckit install is never probed over the network. Never raises."""
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
    :func:`devclaw.server.tools.tasks._block_if_speckit_pending`.

    Cheap and inert for ordinary repos: probes ONLY when a LOCAL install branch
    exists (concrete evidence onboard scaffolded here), so a repo with no
    speckit install returns ``None`` after a single ``git branch`` and a stubbed-suite goal tick
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


#: deterministic branch for the manifest seed/migrate PR — same rationale as
#: INSTALL_BRANCH: findable by head with no persisted state.
MIGRATE_BRANCH = "devclaw/migrate-manifest"


def manifest_needs_upkeep(workspace_dir: str) -> bool:
    """True when the repo's devclaw.json is absent or mechanically behind
    (schemaVersion / boilerplateRevision) — i.e. a re-onboard should open the
    seed/migrate PR (spec 016 US3). A malformed manifest returns False: that
    is a human-fix-by-PR condition doctor reports; migration never repairs by
    guessing."""
    try:
        manifest = load_manifest(workspace_dir)
    except ManifestError:
        return False
    if manifest is None:
        return True
    return (manifest.schema_version != SCHEMA_VERSION
            or manifest.boilerplate_revision < BOILERPLATE_REVISION)


async def migrate_manifest_pr(workspace_dir: str, *, project_id: str | None = None) -> dict:
    """Seed-or-migrate the manifest via a **reviewable PR** (spec 016 US3:
    doctor detects, re-onboard migrates, the human merges). Mechanical fields
    only — every human-set field is preserved verbatim (``migrate_manifest``).
    Idempotent on an already-open migrate PR. Same branch dance as
    :func:`install_speckit_pr`."""
    rc_pr, out_pr = await _run(
        "gh", "pr", "list", "--head", MIGRATE_BRANCH, "--state", "open",
        "--json", "url", "--jq", ".[0].url // empty", cwd=workspace_dir,
    )
    if rc_pr == 0 and out_pr.strip():
        return {"delivered": True, "pr_url": out_pr.strip(), "branch": MIGRATE_BRANCH,
                "already_open": True, "changed": []}

    rc0, orig = await _run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=workspace_dir)
    orig_branch = orig.strip() if rc0 == 0 and orig.strip() and orig.strip() != "HEAD" else None
    try:
        rc, out = await _run("git", "checkout", "-B", MIGRATE_BRANCH, cwd=workspace_dir)
        if rc != 0:
            return {"delivered": False, "pr_url": None, "branch": MIGRATE_BRANCH,
                    "error": f"could not create migrate branch: {out[-200:]}", "changed": []}
        changed: list[str] = []
        seeded = seed_manifest(workspace_dir)
        if seeded:
            changed.append(seeded)
        elif migrate_manifest(workspace_dir):
            changed.append("devclaw.json")
        if not changed:
            return {"delivered": False, "pr_url": None, "branch": MIGRATE_BRANCH,
                    "error": None, "changed": [], "note": "manifest already current"}
        result = await deliver_change(
            workspace_dir=workspace_dir,
            task_id=f"migrate-manifest-{project_id or 'repo'}",
            goal="Bring devclaw.json to the current schema/boilerplate revision",
            kind="onboard",
            target_branch=MIGRATE_BRANCH,
        )
        result["changed"] = changed
        return result
    finally:
        if orig_branch:
            await _run("git", "checkout", orig_branch, cwd=workspace_dir)


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
        # Spec 016 US2: seed the per-project manifest on the SAME reviewable
        # PR — the one devclaw-write path devclaw.json has. No-op when the
        # repo already carries one (human-owned; never touched here).
        seeded = seed_manifest(workspace_dir)
        if seeded:
            created.append(seeded)

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
