"""The checks. Each is ``fn(store, registry) -> list[Finding]``, read-only."""

from __future__ import annotations

import os
import shutil
import stat

from .. import config as _config
from .. import credentials as _credentials
from ..engine.claude import NO_MISTAKES
from ..engine.workspace import goal_checkout_dir
from ..probes import green_credentials
from ..state_store import _now_ms
from .model import Finding, Verdict


def check_credentials(store, registry) -> list[Finding]:
    """Required credentials present, well-formed and (GitHub-shaped) live."""
    out: list[Finding] = []
    green = set(green_credentials())
    for cred in _credentials.probed():
        value = (os.environ.get(cred.var) or "").strip()
        if not value:
            out.append(Finding(f"credentials.{cred.var}", Verdict.FAIL if cred.required else Verdict.WARN,
                               "not set", remedy="set it in the on-box secrets file and redeploy"))
        elif not _credentials.well_formed(cred, value):
            out.append(Finding(f"credentials.{cred.var}", Verdict.FAIL, "set but malformed",
                               remedy=f"expected {cred.scope}"))
        elif cred.prefixes and cred.var not in green:
            out.append(Finding(f"credentials.{cred.var}", Verdict.FAIL, "set but GitHub does not accept it",
                               remedy="rotate the token"))
        else:
            out.append(Finding(f"credentials.{cred.var}", Verdict.OK, "present" + (" and live" if cred.prefixes else "")))
    return out


def check_tools(store, registry) -> list[Finding]:
    out = []
    engine = () if _config.ENGINE == "stub" else (_config.CLAUDE_BIN, _config.TREEHOUSE_BIN)
    for binary in ("gh", "git", *engine):
        out.append(Finding(f"tools.{os.path.basename(binary)}",
                           Verdict.OK if shutil.which(binary) else Verdict.FAIL,
                           "on PATH" if shutil.which(binary) else "not on PATH"))
    return out


#: no-mistakes' own home — gates, run history, daemon files. Not a ``DEVCLAW_*``
#: dial: no-mistakes reads it itself (docs/reference/env-vars.md), and doctor
#: only reports the state of the directory it names.
NM_HOME_DEFAULT = "~/.no-mistakes"
_RUNBOOK = "docs/runbooks/no-mistakes-in-container.md"
_UNCREATABLE = f"`no-mistakes init` cannot create it — clear the path — {_RUNBOOK}"


def check_handoff(store, registry) -> list[Finding]:
    """The hand-off a delivered branch ships through: the ``no-mistakes``
    binary on PATH and the state of its home.

    A host without a usable no-mistakes stops the goal at the hand-off, which
    reads as a mute instance until a human goes looking (#982). The binary is a
    ``fail``, exactly like the ``claude`` and ``treehouse`` binaries above."""
    if _config.ENGINE == "stub":
        return [Finding("handoff", Verdict.OK, "stub engine — no hand-off")]
    binary = shutil.which(NO_MISTAKES)
    if binary:
        found = Finding(f"handoff.{NO_MISTAKES}", Verdict.OK, f"on PATH ({binary})")
    else:
        found = Finding(f"handoff.{NO_MISTAKES}", Verdict.FAIL, "not on PATH",
                        remedy=f"install it in the image (deploy/Dockerfile, NO_MISTAKES_VERSION) — {_RUNBOOK}")
    return [found, _handoff_home()]


def _handoff_home() -> Finding:
    """``$NM_HOME`` (or no-mistakes' own default) and what its state means.

    Three states, not two. A home that is merely not created yet is a ``warn``
    — the engine's pre-session ``no-mistakes init`` creates it, so a goal heals
    it. A home no session can heal is a ``fail`` rather than an exit 0 that
    reads as a healthy instance: one ``init`` cannot create or write into, or
    one doctor cannot even read.

    The operator's value is judged as the tool receives it — the engine passes
    no-mistakes the raw environment (``engine.claude.host_env``) — so a padded,
    relative or unexpanded ``NM_HOME`` is a ``fail``, not a path doctor tidies
    into an ``ok``. The sole normalisation is that an empty value means unset,
    which no-mistakes reads the same way."""
    raw = os.environ.get("NM_HOME") or ""
    #: Only the DEFAULT is expanded. no-mistakes does not expand a tilde in
    #: NM_HOME — `NM_HOME=~/x` makes it a literal `~` directory in its cwd —
    #: and a compose `environment:` block passes `~/.no-mistakes` through
    #: unexpanded, so expanding it here would clear the likeliest misconfig.
    home = raw or os.path.expanduser(NM_HOME_DEFAULT)
    if not os.path.isabs(home):
        return Finding("handoff.home", Verdict.FAIL, f"NM_HOME does not resolve to an absolute path ({raw!r})",
                       remedy=f"set it to an absolute path on the state volume — {_RUNBOOK}")
    try:
        present_and_dir = stat.S_ISDIR(os.stat(home).st_mode)
    except FileNotFoundError:
        # The leaf is absent; the walk to it succeeded, so islink is honest here.
        if os.path.islink(home):
            return Finding("handoff.home", Verdict.FAIL, f"{home} is a dangling symlink", remedy=_UNCREATABLE)
        if not _creatable(home):
            return Finding("handoff.home", Verdict.FAIL, f"no directory at {home}, and it cannot be created",
                           remedy=f"make {_nearest_existing(home)} a writable directory on the state volume"
                                  f" — {_RUNBOOK}")
        return Finding("handoff.home", Verdict.WARN, f"no directory at {home} yet",
                       remedy=f"the first goal's `no-mistakes init` creates it — {_RUNBOOK}")
    except OSError as exc:
        # Permission, a non-directory on the walk, a symlink loop: states that
        # neither `init` nor a session resolves, so never the self-healing warn.
        blocked = _blocked_at(home)
        return Finding("handoff.home", Verdict.FAIL,
                       f"{home} cannot be read ({exc.strerror or type(exc).__name__}"
                       + (f", at {blocked}" if blocked != home else "") + ")",
                       remedy=_UNCREATABLE if isinstance(exc, NotADirectoryError)
                       else f"make {blocked} a readable directory on the state volume — {_RUNBOOK}")
    if not present_and_dir:
        return Finding("handoff.home", Verdict.FAIL, f"{home} is not a directory", remedy=_UNCREATABLE)
    # `init` writes state.sqlite and the daemon files into it. Doctor's own
    # uid answers, the same caveat `_creatable` states.
    if not os.access(home, os.W_OK | os.X_OK):
        return Finding("handoff.home", Verdict.FAIL, f"{home} is not writable",
                       remedy=f"make {home} a writable directory on the state volume — {_RUNBOOK}")
    return Finding("handoff.home", Verdict.OK, home)


def _nearest_existing(path: str) -> str:
    """The deepest ancestor of an absent ``path`` that exists — the directory
    ``mkdir -p`` would create the rest under, and the one an operator has to
    fix when it cannot. A dangling symlink counts as existing, as ``mkdir -p``
    treats it: the entry is there, and it refuses to replace it."""
    ancestor = os.path.dirname(path)
    while not os.path.lexists(ancestor) and ancestor != os.path.dirname(ancestor):
        ancestor = os.path.dirname(ancestor)
    return ancestor


def _creatable(path: str) -> bool:
    """``mkdir -p`` semantics: the nearest existing ancestor must be a
    directory this process can write into. Read-only — probes, never creates.

    It answers for doctor's own uid, which is the uid the session's ``init``
    runs under on this deployment (one container, one user)."""
    ancestor = _nearest_existing(path)
    return os.path.isdir(ancestor) and os.access(ancestor, os.W_OK | os.X_OK)


def _blocked_at(path: str) -> str:
    """For a ``path`` the stat walk could not read: the shallowest ancestor an
    operator has to fix, the first one that is not a directory this process can
    descend into. Falls back to ``path`` itself, which is the answer for a
    symlink loop.

    A wrongly-owned mount point is reported at the mount point, not at the leaf
    the operator configured — otherwise the line leaves them to bisect."""
    parts = path.split(os.sep)
    for depth in range(2, len(parts)):
        ancestor = os.sep.join(parts[:depth]) or os.sep
        if not os.path.isdir(ancestor) or not os.access(ancestor, os.X_OK):
            return ancestor
    return path


def check_pause(store, registry) -> list[Finding]:
    until, reason = store.global_pause()
    if until and until > _now_ms():
        return [Finding("pause", Verdict.WARN, f"dispatch paused until {until} ({reason})",
                        remedy="waits out on its own; clear_usage_pause if the cause is fixed")]
    return [Finding("pause", Verdict.OK, "no pause")]


def check_goals(store, registry) -> list[Finding]:
    out = []
    for g in store.list_goals(open_only=True):
        if not os.path.exists(os.path.join(g.workspace_dir, ".git")):
            out.append(Finding(f"goal.{g.id}.workspace", Verdict.WARN,
                               f"project checkout {g.workspace_dir} is not a git repo yet",
                               remedy="the first session clones it"))
            continue
        checkout = goal_checkout_dir(g.workspace_dir, g.id)
        if not checkout:
            out.append(Finding(f"goal.{g.id}.checkout", Verdict.OK, "no worktree leased yet (leased at first spawn)"))
        else:
            out.append(Finding(f"goal.{g.id}.checkout", Verdict.OK, checkout))
    return out or [Finding("goals", Verdict.OK, "no open goals")]


def check_projects(store, registry) -> list[Finding]:
    out = []
    for p in registry.list():
        if not p.repo_url:
            out.append(Finding(f"project.{p.id}.repo", Verdict.WARN, "no repo_url", remedy="update_project"))
        elif not p.workspace_dir:
            out.append(Finding(f"project.{p.id}.workspace", Verdict.WARN, "no workspace_dir", remedy="update_project"))
        else:
            out.append(Finding(f"project.{p.id}", Verdict.OK, p.workspace_dir))
    return out or [Finding("projects", Verdict.OK, "no projects registered")]


def check_database(store, registry) -> list[Finding]:
    size = store.db_size_bytes()
    return [Finding("database", Verdict.OK, f"{store.db_path} ({size >> 20} MB)")]


CHECKS = (check_credentials, check_tools, check_handoff, check_pause, check_goals, check_projects,
          check_database)
