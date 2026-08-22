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
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RULES_DIR = _ROOT / ".claude" / "rules"

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
    docs = [_ROOT / "CLAUDE.md"]
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
    slash by convention but tolerated without one."""
    if "{" in claim:  # brace expansion is invisible to this guard — see below
        return False
    target = _ROOT / claim.rstrip("/")
    if target.exists():
        return True
    if "*" in claim:
        parent = _ROOT / str(Path(claim).parent)
        if parent.is_dir():
            pattern = Path(claim).name
            return any(fnmatch.fnmatch(p.name, pattern) for p in parent.iterdir())
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
