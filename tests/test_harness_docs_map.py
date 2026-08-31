"""The harness docs must not point at code that no longer exists.

``CLAUDE.md`` and ``.claude/rules/*.md`` are auto-loaded into EVERY session in
this repo — a human's, and the autonomous worker's. They open by naming the
modules their conventions govern, so a stale path is not a doc nit: it is the
most-repeated wrong input in the system.

The 008 shrink deleted five modules the cognition-prompts rule still named
(``planner.py``, ``goal/decomposer.py``, ``goal/research.py``,
``goal/world_research.py``, ``goal/phases/firming.py`` — the whole ``phases/``
dir). Nothing noticed for weeks because nothing checked. This is the check.
"""
import fnmatch
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RULES_DIR = _ROOT / ".claude" / "rules"


@lru_cache(maxsize=1)
def _tracked() -> "frozenset[str]":
    """Every repo-relative path tracked by version control.

    These docs describe the REPOSITORY, so the repository — not the filesystem
    — is what they must be checked against. The two differ inside the worker
    sandbox: ``engine/sandcastle.py`` mounts an empty tmpfs over the
    workspace's ``.claude/`` (spec 011 / #583) so the repo's contributor hooks
    cannot bind devclaw's own engineer, re-exposing only ``rules/``.

    Filesystem-only checks therefore reached OPPOSITE verdicts by environment:
    in-sandbox the resolve check failed against perfectly true claims, and the
    rational repair — deleting the claim — then failed the symmetric ratchet in
    CI, where the directories exist. Three workers hit this independently on
    2026-08-31 and each invented a different rewrite (inserted space, reworded
    prose, stripped backticks), because each reasoned correctly from false
    evidence. Reading the index makes both checks environment-independent, so
    one revision cannot be green in one place and red in the other (#778).

    Empty when the VCS is unavailable (tarball export, no binary) — callers
    fall back to the filesystem, so behaviour outside a checkout is unchanged.
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(_ROOT), "ls-files", "-z"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001 — unavailable ⇒ fall back, never fail here
        return frozenset()
    if out.returncode != 0:
        return frozenset()
    return frozenset(x for x in out.stdout.split("\0") if x)

#: Repo-relative paths are written inside backticks in these docs. Match only
#: paths rooted at a real top-level dir, so prose like `str.format` or a bare
#: `AGENTS.md` (which names a file in the TARGET repo, not this one) is not
#: mistaken for a claim about this tree.
_TOP_LEVEL = ("devclaw", "runner", "tests", "evals", "docs", "specs", ".specify", ".claude", ".sandcastle")
#: The comma is in the class ON PURPOSE: without it a brace-expanded path like
#: ``devclaw/goal/{evaluator,summary}.py`` fails to match at all and becomes
#: invisible to BOTH checks below — the precise blind spot that let five dead
#: modules sit in this rule for weeks.
_PATH_IN_BACKTICKS = re.compile(
    r"`((?:" + "|".join(re.escape(p) for p in _TOP_LEVEL) + r")/[A-Za-z0-9_*./{},-]*)`"
)


def _docs() -> list[Path]:
    docs = [_ROOT / "CLAUDE.md", _ROOT / "README.md"]
    if _RULES_DIR.is_dir():
        docs += sorted(_RULES_DIR.glob("*.md"))
    return [d for d in docs if d.is_file()]


#: A doc may deliberately name a path that no longer exists — the "what happened
#: to the old pipeline" sections are a record of removal, and rewriting them to
#: point at live files would destroy the history they exist to carry. Marking the
#: line is what separates "documented as gone" from "rotted".
_MARKED_GONE = re.compile(r"\b(deleted|removed|frozen|no longer exists|retired)\b", re.I)


def _claimed_paths(doc: Path) -> set[str]:
    """Repo paths the doc asserts exist. Paths on a line that marks them as
    deleted/removed/retired are records of history, not claims."""
    claims: set[str] = set()
    for line in doc.read_text().splitlines():
        if _MARKED_GONE.search(line):
            continue
        claims.update(m.group(1) for m in _PATH_IN_BACKTICKS.finditer(line))
    return claims


def _resolves(claim: str) -> bool:
    """A claim resolves if it names an existing file, an existing directory, or
    a glob matching at least one file. Directories are written with a trailing
    slash by convention but tolerated without one.

    Checked against the tracked set UNION the filesystem: a path the repository
    tracks resolves even where this environment has it shadowed (see
    :func:`_tracked`), and an untracked-but-real path still resolves as before.
    A claim absent from BOTH is dead and still fails — the guard is not
    weakened, only made environment-independent."""
    if "{" in claim:  # brace expansion is invisible to this guard — see below
        return False
    bare = claim.rstrip("/")
    target = _ROOT / bare
    if target.exists():
        return True
    tracked = _tracked()
    if bare in tracked:
        return True
    if any(t.startswith(bare + "/") for t in tracked):  # a tracked directory
        return True
    if "*" in claim:
        parent = _ROOT / str(Path(claim).parent)
        if parent.is_dir():
            pattern = Path(claim).name
            if any(fnmatch.fnmatch(p.name, pattern) for p in parent.iterdir()):
                return True
        return any(fnmatch.fnmatch(t, bare) for t in tracked)
    return False


def test_every_module_path_in_the_harness_docs_resolves():
    """Named regression: the auto-loaded harness docs claim paths that exist.

    A failure here means a doc is briefing every session — including every
    autonomous worker run — on code that was deleted. Fix the doc, not this
    test: the doc is the thing that is wrong.
    """
    dead: list[str] = []
    for doc in _docs():
        for claim in sorted(_claimed_paths(doc)):
            if not _resolves(claim):
                dead.append(f"{doc.relative_to(_ROOT)} claims {claim!r}")
    assert not dead, "harness docs point at paths that do not exist:\n  " + "\n  ".join(dead)


def test_harness_docs_spell_paths_out_so_the_guard_can_see_them():
    """Brace expansion (``goal/{a,b}.py``) hides paths from the guard above.

    The five modules that rotted were written that way. Requiring plain paths
    is what makes the check exhaustive rather than best-effort.
    """
    braced: list[str] = []
    for doc in _docs():
        for claim in sorted(_claimed_paths(doc)):
            if "{" in claim:
                braced.append(f"{doc.relative_to(_ROOT)} uses brace expansion: {claim!r}")
    assert not braced, (
        "write these as full repo-relative paths, one per module:\n  " + "\n  ".join(braced)
    )


#: Module filenames named inside a fenced layout tree. These are NOT backticked
#: paths, so the checks above cannot see them — and that blind spot let README's
#: layout tree keep listing ``claude_sdk.py`` after #613 deleted it. Matching on
#: BASENAME (rather than reconstructing tree indentation) keeps this cheap and
#: robust: the question worth asking is "does this module still exist at all".
_PY_BASENAME = re.compile(r"\b([a-z][a-z0-9_]*\.py)\b")


def _fenced_blocks(doc: Path) -> list[str]:
    parts = doc.read_text().split("```")
    return parts[1::2]  # odd indices are inside fences


def _module_basenames() -> set[str]:
    roots = [_ROOT / "devclaw", _ROOT / "runner"]
    return {p.name for r in roots if r.is_dir() for p in r.rglob("*.py")}


def test_layout_trees_do_not_list_modules_that_were_deleted():
    """Named regression: a fenced layout tree must not name a dead module.

    README.md's tree and CLAUDE.md's are the first thing a reader — human or
    worker — uses to find their way around. Listing a file that no longer
    exists sends them looking for it.
    """
    have = _module_basenames()
    dead: list[str] = []
    for doc in _docs():
        for block in _fenced_blocks(doc):
            for name in sorted(set(_PY_BASENAME.findall(block))):
                if name not in have:
                    dead.append(f"{doc.relative_to(_ROOT)} layout names {name!r}")
    assert not dead, "layout trees name modules that do not exist:\n  " + "\n  ".join(dead)


@pytest.mark.skipif(
    not _RULES_DIR.is_dir(),
    reason=(
        ".claude/rules/ is absent. In devclaw's own sandbox this is expected and "
        "deliberate: sandcastle.py mounts an EMPTY tmpfs over /workspace/.claude so "
        "the target repo's vendor agent config never binds the worker (#583). The "
        "CLAUDE.md half of this guard still runs there; the rules half is covered on "
        "developer checkouts and in CI, which is where the docs are edited."
    ),
)
def test_the_rules_dir_half_of_the_guard_actually_ran():
    """Guard-the-guard: make the sandbox skip visible rather than silent.

    Without this, a green suite inside the sandbox would look like the rules
    files were checked when they were not.
    """
    assert _claimed_paths(_RULES_DIR / "cognition-prompts.md"), (
        "cognition-prompts.md names no repo paths — either it was gutted or the "
        "path pattern above stopped matching."
    )


# ---- the other direction: a real dir may not go UNMENTIONED -----------------
#: Everything under `.claude/` that is runtime state or editor config rather
#: than authored harness content. These carry nothing a session needs briefing
#: on, so the docs are not expected to name them.
_HARNESS_NOT_CONTENT = {
    "worktrees",        # git worktree scratch, created by tooling
    "__pycache__",
    "settings.json", "settings.local.json",
    "scheduled_tasks.lock", "RESUME.md",
}


def test_every_claude_harness_dir_is_named_by_the_docs():
    """The DUAL of the resolves-check above, and the reason this file exists.

    ``test_every_module_path_in_the_harness_docs_resolves`` catches claiming
    something exists that does not. It cannot catch the opposite — deleting a
    true claim — and that is the direction that actually bit: four goal
    branches independently removed the `.claude/hooks` section asserting
    "hooks were deleted", while both hooks were tracked and working (#762,
    #764). A docs-only deletion changes no behavior, so verify and
    test_integrity pass, and under `trust` no reviewer is consulted. Nothing
    in the system could see it.

    A ratchet that only turns one way is not a ratchet. If a harness directory
    exists, the auto-loaded docs must name it — because those docs are the
    grounding input every session and every autonomous worker reads. Delete
    the directory and this check goes quiet on its own; delete only the
    sentence and it fails, which is the whole point.
    """
    harness = _ROOT / ".claude"
    # Union of the tracked set and what is on disk, for the same reason
    # _resolves uses it: inside the sandbox the tmpfs hides every .claude/
    # entry but rules/, so a filesystem-only read makes this check go quiet
    # exactly where the resolve check is failing — and a worker that deletes
    # the claim to satisfy one then breaks the other in CI (#778).
    present = {e.name for e in (harness.iterdir() if harness.is_dir() else ())} | {
        t.split("/")[1] for t in _tracked()
        if t.startswith(".claude/") and len(t.split("/")) >= 2
    }
    present = {
        name for name in present
        if name not in _HARNESS_NOT_CONTENT and not name.startswith(".")
    }
    if not present:  # pragma: no cover - the harness is checked in
        return
    documented = set()
    for doc in _docs():
        for claim in _claimed_paths(doc):
            parts = claim.split("/")
            if len(parts) >= 2 and parts[0] == ".claude":
                documented.add(parts[1])
    missing = sorted(present - documented)
    assert not missing, (
        "these .claude/ harness entries exist but no auto-loaded doc names them "
        "— a true claim was deleted, or a new one was never written:\n  "
        + "\n  ".join(f".claude/{m}" for m in missing)
    )


# ---- the sandbox shadow must not change either verdict (#778) --------------
# engine/sandcastle.py mounts an empty tmpfs over the workspace's .claude/ so
# the repo's contributor hooks cannot bind devclaw's own engineer, re-exposing
# only rules/. Both guards above therefore have to judge the REPOSITORY, not
# the mounted filesystem — otherwise the same revision is green in CI and red
# in the sandbox, and the only edit that satisfies one breaks the other.


def _shadowed(monkeypatch, tmp_path, tracked):
    """Simulate the sandbox: a checkout whose .claude/ is invisible on disk
    while version control still tracks its contents."""
    monkeypatch.setattr(_docs_module_root(), "_ROOT", tmp_path)
    _tracked.cache_clear()
    monkeypatch.setattr(_docs_module_root(), "_tracked", lambda: frozenset(tracked))


def _docs_module_root():
    import sys

    return sys.modules[__name__]


def test_a_tracked_path_resolves_even_when_the_filesystem_hides_it(
    monkeypatch, tmp_path
):
    """The forward guard. In-sandbox `.claude/hooks/` does not exist on disk,
    but the repository tracks it — so the claim is TRUE and must not be
    reported dead. Reporting it dead is what made three workers delete it."""
    _shadowed(monkeypatch, tmp_path, {".claude/hooks/main-branch-guard.py"})

    assert _resolves(".claude/hooks/") is True
    assert _resolves(".claude/hooks") is True


def test_a_path_in_neither_the_index_nor_the_filesystem_is_still_dead(
    monkeypatch, tmp_path
):
    """The guard is not weakened: a genuinely deleted path fails as before."""
    _shadowed(monkeypatch, tmp_path, {".claude/hooks/main-branch-guard.py"})

    assert _resolves("devclaw/goal/decomposer.py") is False
    assert _resolves(".claude/does-not-exist/") is False


def test_tracked_harness_dirs_are_still_required_to_be_documented(
    monkeypatch, tmp_path
):
    """The reverse ratchet. With .claude/ shadowed on disk, a filesystem-only
    read sees nothing and the check goes quiet — exactly where the forward
    check is failing, which is the trap. Reading the index keeps it armed, so
    deleting the claim can no longer buy a green run anywhere."""
    _shadowed(
        monkeypatch,
        tmp_path,
        {
            ".claude/hooks/main-branch-guard.py",
            ".claude/skills/docs-audit/SKILL.md",
            ".claude/commands/ship.md",
        },
    )

    present = {
        t.split("/")[1] for t in _tracked()
        if t.startswith(".claude/") and len(t.split("/")) >= 2
    }
    present = {n for n in present if n not in _HARNESS_NOT_CONTENT}

    assert present == {"hooks", "skills", "commands"}, (
        "the ratchet must still see harness dirs the repository tracks, even "
        "when the sandbox hides them — otherwise deleting the doc claim is a "
        "way to pass both checks, which is the #778 convergence failure"
    )


def test_no_vcs_falls_back_to_the_filesystem(monkeypatch, tmp_path):
    """Outside a checkout (tarball export, no binary) behaviour is unchanged:
    the tracked set is empty and the filesystem alone decides."""
    monkeypatch.setattr(_docs_module_root(), "_ROOT", tmp_path)
    _tracked.cache_clear()
    monkeypatch.setattr(_docs_module_root(), "_tracked", frozenset)
    (tmp_path / "devclaw").mkdir()
    (tmp_path / "devclaw" / "real.py").write_text("")

    assert _resolves("devclaw/real.py") is True
    assert _resolves("devclaw/ghost.py") is False
