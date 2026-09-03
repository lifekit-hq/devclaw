"""The ONE answer to "what did the agent change?" (spec 013, #630).

Two components used to compute that independently and disagree. Delivery staged
everything and committed, so it could not miss a file. The gates diffed the
working tree against the pre-run ref, which shows only content the agent chose
to *record* — and what made the agent record its work was a sentence in a worker
skill file. When the agent complied both views agreed; when it did not, the gates
judged a strict subset of what shipped, and a change made entirely of new
unrecorded files reached every gate as an EMPTY span and passed them trivially
(live, 2026-08-22: delivery shipped 4 files / +179, the gates judged 1 / +32).

This module removes the second computation. **Materialization** happens once, on
the host, the moment the agent's run ends: stage everything, write it into a
commit, and hand back a :class:`ChangeSet` naming both ends of the range. Gates,
the change-size projection, the advisory checks and delivery all read that one
object, so two consumers cannot disagree about what the change is — the invariant
stops being a docstring and becomes structural.

Deliberately NOT best-effort, unlike :mod:`devclaw.task_git`'s helpers: an empty
result is no longer safely equivalent to "no change", so a git failure is
reported as :data:`ERROR` (the caller's gate fails it CLOSED, #186) instead of
degrading to ``""``.
"""

from __future__ import annotations

import fnmatch
import os
import re
import subprocess
from dataclasses import dataclass

from .git_identity import git_identity_env

# ---- what KIND of thing each changed path is (spec 032 US3) -----------------
#
# The audit that produced spec 032 found sandbox lore landing in product repos
# through the worker: committed binaries, LD_LIBRARY_PATH in a Playwright
# config, a postinstall monkey-patch of node_modules, deleted-but-true
# CLAUDE.md claims, "--no-verify is required per repo convention" in AGENTS.md.
# Every one of those edits a GATE INPUT — a file the verification reads — to
# make a gate pass. The class is closed here, in the one definition of the
# change: every path in the span carries a class, one always-hard gate fails
# gate-input edits and binaries, and no consumer re-derives the rule.

PRODUCT = "product"
GATE_INPUT = "gate_input"
ENV_DECL = "env_decl"

#: paths the verification reads. A glob without "/" matches the basename
#: anywhere in the tree; one with "/" matches from the repository root.
GATE_INPUT_GLOBS: tuple[str, ...] = (
    "AGENTS.md",
    ".github/workflows/*",
    ".github/actions/*",
    "playwright.config.*",
    "angular.json",
    "jest.config.*",
    "vitest.config.*",
    "karma.conf.*",
    "pytest.ini",
    "tox.ini",
    ".pre-commit-config.yaml",
    ".husky/*",
    ".npmrc",
    "global.json",
    ".tool-versions",
    ".mise.toml",
    "mise.toml",
)
#: the project's environment declaration — a legitimate edit, recorded loudly
ENV_DECL_GLOBS: tuple[str, ...] = ("devclaw.json", ".devcontainer/*")
#: a package.json hunk that ADDS one of these keys is an install script
INSTALL_SCRIPT_KEYS: tuple[str, ...] = ("preinstall", "postinstall", "prepare")
_INSTALL_KEY_RE = re.compile(
    r'^\+\s*"(' + "|".join(INSTALL_SCRIPT_KEYS) + r')"\s*:', re.MULTILINE,
)
_BACKTICK_RE = re.compile(r"`([^`\n]{1,200})`")


def _match(path: str, glob: str) -> bool:
    glob = glob.strip()
    if not glob:
        return False
    if "/" in glob:
        bare = glob.rstrip("/")
        return fnmatch.fnmatchcase(path, glob) or (
            not any(c in bare for c in "*?") and path.startswith(bare + "/")
        )
    return fnmatch.fnmatchcase(os.path.basename(path), glob) or fnmatch.fnmatchcase(path, glob)


def classify_path(path: str, *, added: str = "") -> str:
    """One path's class. ``added`` is the text the span ADDS to that file
    (used for the install-script rule on package.json). Pure."""
    if any(_match(path, g) for g in ENV_DECL_GLOBS):
        return ENV_DECL
    if any(_match(path, g) for g in GATE_INPUT_GLOBS):
        return GATE_INPUT
    if os.path.basename(path) == "package.json" and _INSTALL_KEY_RE.search(added or ""):
        return GATE_INPUT
    return PRODUCT


def in_scope_from_text(text: str) -> tuple[str, ...]:
    """Gate-input paths or globs an issue DECLARES in scope (a ticket that is
    about CI, spec 032 FR-008): every backticked token in the dispatch brief
    that itself names a gate input. Nothing else in the prose counts."""
    out: list[str] = []
    for tok in _BACKTICK_RE.findall(text or ""):
        tok = tok.strip()
        if not tok or " " in tok:
            continue
        if any(_match(tok, g) for g in GATE_INPUT_GLOBS) or (
            any(c in tok for c in "*?") and any(_match(g.replace("*", "x"), tok) for g in GATE_INPUT_GLOBS)
        ):
            out.append(tok)
    return tuple(dict.fromkeys(out))


@dataclass(frozen=True)
class ChangedPath:
    """One path in the judged span, with its class (spec 032 US3)."""

    path: str
    #: git name-status letter: A M D R T C
    status: str
    binary: bool = False
    cls: str = PRODUCT
    #: a gate-input path the issue declared in scope — judged as product
    in_scope: bool = False


def _diff_blocks(diff: str) -> "dict[str, tuple[bool, str]]":
    """``path -> (is_binary, added_text)`` per file block of a unified diff."""
    out: dict[str, tuple[bool, str]] = {}
    path = ""
    binary = False
    added: list[str] = []
    def flush() -> None:
        if path:
            out[path] = (binary, "\n".join(added))
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            flush()
            path, binary, added = "", False, []
            parts = line.split(" b/", 1)
            if len(parts) == 2:
                path = parts[1].strip()
        elif line.startswith("+++ b/"):
            path = line[6:].strip() or path
        elif line.startswith("Binary files ") or line.startswith("GIT binary patch"):
            binary = True
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(line)
    flush()
    return out


def changed_entries_sync(host_dir: str, base: str, head: str) -> "list[tuple[str, str]]":
    """``(status letter, path)`` for every path in ``base..head`` — rename
    targets count as the path. Raises on a git failure (the caller fails the
    span CLOSED: a span whose paths cannot be named cannot be classified)."""
    proc = _git(host_dir, "diff", "--name-status", "-M", f"{base}..{head}")
    if proc.returncode != 0:
        raise RuntimeError(f"git diff --name-status failed: {(proc.stderr or '').strip()[:200]}")
    entries: list[tuple[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        cols = line.split("\t")
        if len(cols) < 2 or not cols[0]:
            continue
        entries.append((cols[0][0], cols[-1].strip()))
    return entries


def build_paths(
    entries: "list[tuple[str, str]]", diff: str, in_scope: "tuple[str, ...]" = (),
) -> "tuple[ChangedPath, ...]":
    """Classify every entry ONCE. Pure."""
    blocks = _diff_blocks(diff)
    out: list[ChangedPath] = []
    for status, path in entries:
        binary, added = blocks.get(path, (False, ""))
        cls = classify_path(path, added=added)
        scoped = cls == GATE_INPUT and any(_match(path, g) for g in in_scope)
        out.append(ChangedPath(path=path, status=status, binary=binary, cls=cls, in_scope=scoped))
    return tuple(out)

#: the agent produced a real span — gates judge it, delivery publishes it
CHANGE = "change"
#: the agent changed nothing. A first-class outcome: the task settles
#: successfully, publishes nothing, and is reported upstream as no progress.
NO_CHANGE = "no_change"
#: the workspace is not a git repository. Distinct from ERROR on purpose: the
#: judged-vs-shipped divergence this module exists to close cannot occur without
#: a repository — delivery already fails loudly there ("workspace is not a git
#: repository" is NOT a benign error), so nothing can ship unjudged. Still
#: reported out loud; never presented as an empty change.
NO_REPO = "no_repo"
#: the workspace IS a repository and git could not answer. Fails CLOSED.
ERROR = "error"


@dataclass(frozen=True)
class ChangeSet:
    """Everything the agent changed, as one object.

    ``base_sha`` is the task's pinned pre-run reference; ``head_sha`` is its
    post-run counterpart, so the change is expressible as a range between two
    points. ``diff`` is that range rendered once — the single value every
    consumer reads.
    """

    status: str
    base_sha: str = ""
    head_sha: str = ""
    diff: str = ""
    #: why, for :data:`ERROR` / :data:`NO_REPO`
    reason: str = ""
    #: the AGENT itself wrote the commit at ``head_sha``. Measured before
    #: staging, so it answers "did the worker write a commit message this run",
    #: not delivery's old ``ahead > 0`` proxy (true in goal-branch mode for
    #: prior increments this worker never touched).
    agent_authored: bool = False
    #: devclaw created or amended a commit to capture unrecorded work
    materialized: bool = False
    #: every path in the span with its class (spec 032 US3); ``()`` for a
    #: span with nothing to classify or a stub that predates the field
    paths: "tuple[ChangedPath, ...]" = ()

    @property
    def gate_input_paths(self) -> "tuple[str, ...]":
        """gate-input edits the issue did NOT declare in scope — the gate fails on any"""
        return tuple(p.path for p in self.paths if p.cls == GATE_INPUT and not p.in_scope)

    @property
    def binary_paths(self) -> "tuple[str, ...]":
        return tuple(p.path for p in self.paths if p.binary and p.status != "D")

    @property
    def env_decl_paths(self) -> "tuple[str, ...]":
        return tuple(p.path for p in self.paths if p.cls == ENV_DECL)

    @property
    def is_error(self) -> bool:
        return self.status == ERROR

    @property
    def is_change(self) -> bool:
        return self.status == CHANGE

    @property
    def is_no_change(self) -> bool:
        return self.status == NO_CHANGE


def _git(host_dir: str, *args: str, env_extra: "dict[str, str] | None" = None):
    return subprocess.run(
        ["git", "-C", host_dir, *args],
        capture_output=True, text=True, timeout=60,
        env=({**os.environ, **env_extra} if env_extra else None),
    )


def materialize_worktree_sync(
    host_dir: str, base: str, *, task_id: str, message: str
) -> dict:
    """Capture everything the agent left in ``host_dir`` as a git commit.

    Mechanical by construction — ``git add -A`` cannot miss a file, and no
    instruction to the agent is load-bearing for its completeness (FR-002).

    Returns ``{"status", "head", "agent_authored", "materialized", "reason"}``
    where status is ``ok`` / ``no_repo`` / ``error``.

    * A CLEAN tree makes no commit at all, so a worker that recorded all of its
      own work ends the run with exactly the history it has today — no extra
      commit, no altered message (FR-008).
    * A dirty tree is folded into the worker's OWN commit when it made one this
      run and that commit is unpushed (``commit --amend --no-edit``) — the same
      rule delivery has applied for the stray-lockfile case, applied to the whole
      class instead of one instance. Otherwise devclaw writes the commit, with
      the same message delivery composes today.
    * ``git add -A`` honours ``.gitignore``, so ignored files are excluded from
      the judged span exactly as they are from what ships.
    """
    out: dict = {"status": ERROR, "head": "", "agent_authored": False,
                 "materialized": False, "reason": ""}
    try:
        probe = _git(host_dir, "rev-parse", "--is-inside-work-tree")
    except (OSError, subprocess.SubprocessError) as exc:
        out["status"] = NO_REPO
        out["reason"] = f"git is not usable in {host_dir}: {exc.__class__.__name__}: {exc}"
        return out
    if probe.returncode != 0:
        out["status"] = NO_REPO
        out["reason"] = f"{host_dir} is not a git repository"
        return out

    try:
        if base:
            ahead = _git(host_dir, "rev-list", "--count", f"{base}..HEAD")
            if ahead.returncode == 0 and ahead.stdout.strip().isdigit():
                out["agent_authored"] = int(ahead.stdout.strip()) > 0

        status = _git(host_dir, "status", "--porcelain")
        if status.returncode != 0:
            out["reason"] = f"git status failed: {(status.stderr or '').strip()[:200]}"
            return out

        if status.stdout.strip():
            add = _git(host_dir, "add", "-A")
            if add.returncode != 0:
                out["reason"] = f"git add -A failed: {(add.stderr or '').strip()[:200]}"
                return out
            staged = _git(host_dir, "diff", "--cached", "--quiet")
            # rc 1 => something is staged; rc 0 => nothing (the dirt was all
            # ignored/untracked-and-ignored, which does not ship either).
            if staged.returncode != 0:
                pushed = _git(host_dir, "branch", "-r", "--contains", "HEAD")
                head_is_pushed = pushed.returncode == 0 and bool(pushed.stdout.strip())
                if out["agent_authored"] and not head_is_pushed:
                    commit = _git(host_dir, "commit", "--amend", "--no-edit",
                                  env_extra=git_identity_env())
                else:
                    commit = _git(host_dir, "commit", "-m", message,
                                  env_extra=git_identity_env())
                if commit.returncode != 0:
                    out["reason"] = (
                        f"git commit failed: "
                        f"{((commit.stderr or '') + (commit.stdout or '')).strip()[:200]}"
                    )
                    return out
                out["materialized"] = True

        head = _git(host_dir, "rev-parse", "HEAD")
        if head.returncode != 0 or not head.stdout.strip():
            out["reason"] = f"git rev-parse HEAD failed: {(head.stderr or '').strip()[:200]}"
            return out
        out["head"] = head.stdout.strip()
        out["status"] = "ok"
        return out
    except (OSError, subprocess.SubprocessError) as exc:
        out["reason"] = f"{exc.__class__.__name__}: {exc}"
        return out


#: The self-describing commit subject devclaw uses when the agent committed
#: nothing. Fixed, never derived from the dispatch prompt — so neither the PR
#: title nor the branch slug ever echoes the ask (criteria 2 and 4, spec 017).
MACHINE_COMMIT_SUBJECT = "chore: machine-captured uncommitted workspace state"


def materialization_message(task_id: str) -> str:
    """The commit message devclaw writes when the worker recorded nothing.

    Self-describing by design: it says WHAT the commit is (a machine snapshot),
    not what the agent was ASKED to do. The dispatch prompt is never a source for
    either the PR title or the commit subject (spec 017 criteria 2 and 4)."""
    return (
        f"{MACHINE_COMMIT_SUBJECT}\n\n"
        f"Delivered by devclaw (task {task_id}). "
        "Agent authored no commit — this captures the uncommitted workspace tree."
    )
