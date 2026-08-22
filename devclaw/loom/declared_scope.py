"""Declared file scopes — the hermetic-I/O contract a `[P]` task signs.

Spec 010 US3 (FR-101/FR-103). Two increments may run at the same time on one
project ONLY when the plan marked them topologically independent (`[P]`) **and**
each declared the file paths it will touch. This module is the mechanism that
makes the declaration mean something: given the increment's own unified diff, it
answers "did this change stay inside what its task declared?".

Mechanism, not prompt. A declared scope that nothing checks is a soft constraint,
and workers route around soft constraints (#358) — so the contract is enforced
where the change is judged, not where it is requested.

The declaration rides the task graph, because parallelism is *data in the plan*
(the spec's framing) and a second artifact would be a second thing to keep in
step::

    - [ ] T012 [P] [US1] Add the widget renderer (scope: src/widget/**, tests/test_widget.py)

Everything here is PURE: string in, verdict out. No git subprocess, no workspace
read, no store read, and — by FR-103 — no LLM. The caller already computed the
unified diff for the other read-the-change gates, so the whole check costs one
scan of a string that is in memory anyway.

Totality is deliberate. Every parser here is total: a garbled diff yields NO
claims, which the caller reads as "this increment declared nothing", i.e. *not
consulted* — never "allowed". The two are different in exactly the way
constitution V cares about: a gate that is by policy not consulted produces no
silence to ship on, while a gate that runs and cannot decide must fail closed.
Deciding which of those applies is the caller's job (see the scope gate in
:mod:`devclaw.task_queue`); this module only refuses to guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: A tracked ``specs/<feature>/tasks.md`` path — the task graph (POSIX, git's own
#: separator). Mirrors ``goal.slice_guard._TASKS_PATH_RE``; the two read the same
#: artifact for different questions (build-ahead vs declared I/O).
_TASKS_PATH_RE = re.compile(r"^specs/[^/]+/tasks\.md$")

#: A markdown checkbox row: the mark, then the row text.
_TASK_LINE = re.compile(r"^\s*[-*]\s+\[(?P<mark>[ xX])\]\s+(?P<rest>.+?)\s*$")
#: The stable task id (``T001``). Claims key off THIS, never the free-text label,
#: so a row re-worded in the same increment it is checked still resolves (the
#: reason ``slice_guard`` keys the same way).
_TASK_ID = re.compile(r"\bT\d+\b")
#: The topological-independence marker the plan puts on a fan-out task.
_PARALLEL = re.compile(r"\[P\]")
#: The declaration itself: ``(scope: a/**, b.py)`` — or a trailing ``scope: …``
#: with no closing paren. Comma-separated; everything after the colon is globs.
_SCOPE = re.compile(r"\(\s*scope\s*:\s*(?P<globs>[^)]*)\)|scope\s*:\s*(?P<tail>.+)$", re.IGNORECASE)

#: ``diff --git a/<x> b/<y>`` — non-greedy on the left half, anchored on the
#: ``b/`` half, so a path containing spaces still splits correctly.
_DIFF_HEADER = re.compile(r'^diff --git "?a/(?P<a>.*?)"? "?b/(?P<b>.*?)"?$')


@dataclass(frozen=True)
class ScopeCheck:
    """The verdict of one increment against its declared scope.

    ``consulted`` is False when the increment claimed no scoped `[P]` task —
    there is no contract, so there is nothing to enforce and the caller must not
    invent one.
    """

    #: task id -> the globs that task declared, for every claim in this increment
    claims: "dict[str, tuple[str, ...]]" = field(default_factory=dict)
    #: paths this increment touched that no declared glob covers
    violations: "tuple[str, ...]" = ()
    #: every path this increment touched (kept for the failure message)
    touched: "tuple[str, ...]" = ()

    @property
    def consulted(self) -> bool:
        """Whether a declared scope applies to this increment at all."""
        return bool(self.claims)

    @property
    def allowed(self) -> "tuple[str, ...]":
        """The union of every declared glob, sorted for a stable message."""
        out: "set[str]" = set()
        for globs in self.claims.values():
            out.update(globs)
        return tuple(sorted(out))


# ---- path/glob matching ----------------------------------------------------


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


# ---- reading a task graph directly -----------------------------------------


@dataclass(frozen=True)
class PlanRow:
    """One checkbox row of a ``tasks.md``, as the fan-out planner reads it."""

    task_id: str
    label: str
    checked: bool
    parallel: bool
    scopes: "tuple[str, ...]"


def parse_plan_rows(text: str) -> "list[PlanRow]":
    """Every identified task row of a ``tasks.md``, in file order.

    The same row grammar the claim detector uses, exposed for the planner that
    reads the graph from the WORKING TREE rather than from a diff. Rows without
    a ``T<id>`` are skipped: they are prose or a checklist of something else, and
    a fan-out lane must be nameable. Pure; never raises."""
    rows: "list[PlanRow]" = []
    for line in (text or "").splitlines():
        m = _TASK_LINE.match(line)
        if not m:
            continue
        rest = m.group("rest")
        idm = _TASK_ID.search(rest)
        if not idm:
            continue
        rows.append(
            PlanRow(
                task_id=idm.group(0),
                label=rest.strip(),
                checked=m.group("mark") in ("x", "X"),
                parallel=bool(_PARALLEL.search(rest)),
                scopes=parse_scopes(rest),
            )
        )
    return rows


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


def parse_scopes(row_text: str) -> "tuple[str, ...]":
    """The globs a task row declares, or ``()`` when it declares none."""
    m = _SCOPE.search(row_text or "")
    if not m:
        return ()
    raw = m.group("globs")
    if raw is None:
        raw = m.group("tail") or ""
    return tuple(g for g in (part.strip() for part in raw.split(",")) if g)


def claimed_scopes(diff: str) -> "dict[str, tuple[str, ...]]":
    """The declared scopes of every scoped `[P]` task this increment CLAIMED.

    A task is claimed when the increment checks its row off: the diff adds a
    ``[x]`` row that carries both ``[P]`` and a ``scope:`` declaration, for a
    task id that was not already checked on the removed side of the same file.
    (Keying on the id, not the label, is what keeps a row re-worded in the same
    increment from silently dropping out.)

    Only ``specs/*/tasks.md`` blocks are read — the task graph is the only place
    a plan speaks. Total: anything unparseable simply yields no claim."""
    claims: "dict[str, tuple[str, ...]]" = {}
    for block in _file_blocks(diff):
        if not any(_TASKS_PATH_RE.match(_normalise(p)) for p in block.paths):
            continue
        removed_checked: "set[str]" = set()
        added: "list[tuple[str, str]]" = []  # (task id, row text)
        for line in block.body:
            if not line or line[0] not in "+-":
                continue
            m = _TASK_LINE.match(line[1:])
            if not m:
                continue
            rest = m.group("rest")
            idm = _TASK_ID.search(rest)
            if not idm:
                continue
            checked = m.group("mark") in ("x", "X")
            if line[0] == "-":
                if checked:
                    removed_checked.add(idm.group(0))
            elif checked:
                added.append((idm.group(0), rest))
        for task_id, rest in added:
            if task_id in removed_checked:
                continue  # already checked before this increment
            if not _PARALLEL.search(rest):
                continue  # not marked topologically independent — no fan-out contract
            globs = parse_scopes(rest)
            if globs:
                claims[task_id] = globs
    return claims


# ---- the check ------------------------------------------------------------

#: Paths always inside a claimed increment's scope. A worker must be able to
#: check off its own row, so the task graph it claimed from is in scope by
#: construction; nothing else is implicitly allowed.
def _implicitly_allowed(diff: str) -> "set[str]":
    allowed: "set[str]" = set()
    for block in _file_blocks(diff):
        for raw in block.paths:
            p = _normalise(raw)
            if _TASKS_PATH_RE.match(p):
                allowed.add(p)
    return allowed


def scope_check(
    diff: str,
    declared: "tuple[str, ...] | list[str] | None" = None,
) -> ScopeCheck:
    """Judge one increment's diff against its declared scope.

    ``declared`` is the scope the DISPATCHER pinned on this increment (the
    fan-out lane case): the host chose the task, so it knows the contract
    first-hand and does not have to infer it. Without it the contract is read
    from the increment's own claim on the task graph — which is what a
    single-lane increment that checks off a scoped `[P]` row is doing.

    ``diff`` is the MATERIALIZED span (spec 013): everything the agent changed,
    including what it chose not to record. This function briefly took an
    ``extra_paths`` argument so the caller could fold the workspace's unrecorded
    paths in itself — the #358 route-around ("escape the declared scope by not
    committing the file") arriving through the back door. That hole is now closed
    upstream, for scoped and unscoped increments alike, so the argument is gone:
    a gate that recomputed the change would be a second component owning its
    definition, which is the defect this all exists to remove.

    Never raises."""
    try:
        claims = claimed_scopes(diff)
        if declared:
            globs = tuple(g for g in declared if str(g).strip())
            if globs:
                # The pinned contract wins, and it applies whether or not the
                # worker got round to checking its row off — a lane that skips
                # the bookkeeping must not thereby escape its declared I/O.
                claims = {"(dispatched)": globs, **claims}
        touched = changed_paths(diff)
        if not claims:
            return ScopeCheck(claims={}, violations=(), touched=touched)
        allowed = _implicitly_allowed(diff)
        every_glob: "list[str]" = []
        for globs in claims.values():
            every_glob.extend(globs)
        violations = tuple(
            p for p in touched if p not in allowed and not path_in_scope(p, every_glob)
        )
        return ScopeCheck(claims=claims, violations=violations, touched=touched)
    except Exception:  # noqa: BLE001 — totality is the contract; see the module docstring
        return ScopeCheck()


def violation_summary(check: ScopeCheck) -> str:
    """The operator- and worker-facing failure text: every out-of-scope path and
    the contract it was measured against. Loud and complete — a partial list
    would send the worker back for a second guess."""
    paths = ", ".join(check.violations)
    scopes = ", ".join(check.allowed) or "(none)"
    tasks = ", ".join(sorted(check.claims)) or "(none)"
    return (
        f"declared-scope violation: this increment touched {len(check.violations)} "
        f"path(s) outside the file scope its plan declared — {paths}. "
        f"Claimed task(s): {tasks}; declared scope: {scopes}. "
        f"A [P] task's declared scope is the contract that makes concurrent "
        f"execution safe; keep the change inside it, or widen the declaration in "
        f"the task graph first (planning time), never at runtime."
    )


# ---- disjointness ----------------------------------------------------------


def literal_prefix(pattern: str) -> str:
    """The wildcard-free head of a declared glob — everything before the first
    ``*`` or ``?``. ``src/widget/**`` ⇒ ``src/widget/``; ``**/x`` ⇒ ``""``."""
    pat = _normalise(pattern)
    cut = len(pat)
    for i, ch in enumerate(pat):
        if ch in "*?":
            cut = i
            break
    return pat[:cut]


def _prefix_covers(a: str, b: str) -> bool:
    """Whether path-prefix ``a`` could contain anything under prefix ``b``."""
    if a == "":
        return True  # a wildcard-leading glob can reach anywhere
    a_dir = a if a.endswith("/") else a + "/"
    return b == a or b.startswith(a_dir) or b.startswith(a)


def scopes_disjoint(
    a: "tuple[str, ...] | list[str]", b: "tuple[str, ...] | list[str]"
) -> bool:
    """Whether two declared scopes can be run at the same time (spec 010 FR-101).

    Decided SYNTACTICALLY, on the literal head of each glob, and deliberately
    conservatively: two scopes are called disjoint only when no glob of one has a
    literal head that could reach into the other's. The test can therefore
    decline a fan-out that would in fact have been safe, but it can never permit
    one that is not — and declining costs a night of sequential execution while
    permitting costs a corrupted integration.

    Empty on either side is NOT disjoint: a scope that declares nothing has
    declared no boundary, and an undeclared boundary is exactly what FR-101
    refuses to run concurrently."""
    left = [literal_prefix(g) for g in a if str(g).strip()]
    right = [literal_prefix(g) for g in b if str(g).strip()]
    if not left or not right:
        return False
    for x in left:
        for y in right:
            if _prefix_covers(x, y) or _prefix_covers(y, x):
                return False
    return True
