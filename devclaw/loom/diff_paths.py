"""Diff path helpers — the mechanical "which files did this change touch,
and does a path match a glob" pair.

All that survives of the spec 010 declared-scope machinery. The `[P]`
fan-out contract it was built for was deleted by spec 022 US3, and the
gate that consumed it was retired with this module's rename: nothing in
the dispatch path emitted a `[P]` scope claim any more, so the gate
self-skipped on every real increment while its tests kept it green.

What is left is genuinely general and has a live consumer:
``quality.change_advisories`` uses both functions to describe a change.
Total and never-raises, as before — a malformed diff yields fewer paths,
never an exception.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
_DIFF_HEADER = re.compile(r'^diff --git "?a/(?P<a>.*?)"? "?b/(?P<b>.*?)"?$')

def _normalise(path: str) -> str:
    """A path in the one form everything here compares in: POSIX separators, no
    leading ``./`` or ``/``, no surrounding quotes/whitespace."""
    p = (path or "").strip().strip('"').strip("'").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _glob_to_regex(pattern: str) -> "re.Pattern[str] | None":
    """Translate one declared glob into a full-match regex.

    ``*`` does NOT cross ``/``, ``**`` does, ``?`` matches one non-``/``
    character. That is the build-system reading of a declared path set, and it
    is the one that makes ``src/*`` a real narrowing rather than a synonym for
    ``src/**``. ``None`` for an empty pattern (declares nothing)."""
    pat = _normalise(pattern)
    if not pat:
        return None
    out: "list[str]" = []
    i = 0
    while i < len(pat):
        ch = pat[i]
        if ch == "*":
            if pat.startswith("**/", i):
                out.append("(?:.*/)?")  # so `src/**/x` also covers `src/x`
                i += 3
                continue
            if pat.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    try:
        return re.compile("^" + "".join(out) + "$")
    except re.error:  # pragma: no cover — the translation only emits valid regex
        return None


def path_in_scope(path: str, globs: "tuple[str, ...] | list[str]") -> bool:
    """Whether ``path`` lies inside the declared set ``globs``.

    Beyond glob matching, a declaration that names a DIRECTORY covers its
    subtree: an author writing ``src/widget/`` (or a wildcard-free
    ``src/widget``) meant the directory, and reading that narrowly would fail
    honest increments. That is the one deliberately permissive rule here — it
    widens only what the plan itself named."""
    target = _normalise(path)
    if not target:
        return False
    for raw in globs:
        pat = _normalise(raw)
        if not pat:
            continue
        rx = _glob_to_regex(pat)
        if rx is not None and rx.match(target):
            return True
        # directory declaration ⇒ its subtree
        bare = pat.rstrip("/")
        if bare and not any(c in bare for c in "*?") and target.startswith(bare + "/"):
            return True
    return False


# ---- diff parsing ----------------------------------------------------------

@dataclass
class _FileBlock:
    """One file's slice of a unified diff: the paths it names and its body."""

    paths: "list[str]" = field(default_factory=list)
    body: "list[str]" = field(default_factory=list)


def _file_blocks(diff: str) -> "list[_FileBlock]":
    """Split a unified diff into per-file blocks.

    Paths are read from the block HEADER only (``diff --git``, ``---``/``+++``,
    ``rename from``/``rename to``) — never from the body. A repository that
    contains patch files would otherwise have its own diff content mistaken for
    file boundaries, and every such false path reads as an out-of-scope edit."""
    blocks: "list[_FileBlock]" = []
    cur: "_FileBlock | None" = None
    in_body = False
    for line in (diff or "").splitlines():
        if line.startswith("diff --git "):
            cur = _FileBlock()
            blocks.append(cur)
            in_body = False
            m = _DIFF_HEADER.match(line)
            if m:
                cur.paths.extend([m.group("a"), m.group("b")])
            continue
        if cur is None:
            continue
        if line.startswith("@@"):
            in_body = True
            continue
        if in_body:
            cur.body.append(line)
            continue
        if line.startswith("--- a/"):
            cur.paths.append(line[6:])
        elif line.startswith("+++ b/"):
            cur.paths.append(line[6:])
        elif line.startswith("rename from "):
            cur.paths.append(line[12:])
        elif line.startswith("rename to "):
            cur.paths.append(line[10:])
    return blocks

def changed_paths(diff: str) -> "tuple[str, ...]":
    """Every repository path the diff touches, deduplicated and sorted.

    Both sides of a rename count: moving a file OUT of a declared scope is as
    much an out-of-scope write as editing one. ``/dev/null`` (the add/delete
    sentinel) is not a path."""
    seen: "set[str]" = set()
    for block in _file_blocks(diff):
        for raw in block.paths:
            p = _normalise(raw)
            if p and p != "dev/null":
                seen.add(p)
    return tuple(sorted(seen))
