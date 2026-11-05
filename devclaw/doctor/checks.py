"""The checks. Each is ``fn(store, registry) -> list[Finding]``, read-only."""

from __future__ import annotations

import os
import shutil

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
#: only reports whether it is there.
NM_HOME_DEFAULT = "~/.no-mistakes"
_RUNBOOK = "docs/runbooks/no-mistakes-in-container.md"


def check_handoff(store, registry) -> list[Finding]:
    """The hand-off a delivered branch ships through: the ``no-mistakes``
    binary on PATH and whether its home exists.

    A host without a usable no-mistakes stops the goal at the hand-off, which
    read as a mute instance until a human went looking (goal
    dc-phone-tab-bar-971) — so the binary is a ``fail``, exactly like the
    ``claude`` and ``treehouse`` binaries above. The home is a ``warn``: the
    first goal's ``no-mistakes init`` creates it, so an instance that has not
    run one yet is healthy without it (same shape as a goal whose project
    checkout is not cloned yet)."""
    if _config.ENGINE == "stub":
        return [Finding("handoff", Verdict.OK, "stub engine — no hand-off")]
    out: list[Finding] = []
    binary = shutil.which(NO_MISTAKES)
    if binary:
        out.append(Finding(f"handoff.{NO_MISTAKES}", Verdict.OK, f"on PATH ({binary})"))
    else:
        out.append(Finding(f"handoff.{NO_MISTAKES}", Verdict.FAIL, "not on PATH",
                           remedy=f"install it in the image (deploy/Dockerfile, NO_MISTAKES_VERSION) — {_RUNBOOK}"))
    home = os.path.expanduser((os.environ.get("NM_HOME") or "").strip() or NM_HOME_DEFAULT)
    if os.path.isdir(home):
        out.append(Finding("handoff.home", Verdict.OK, home))
    else:
        out.append(Finding("handoff.home", Verdict.WARN, f"no directory at {home} yet",
                           remedy=f"the first goal's `no-mistakes init` creates it — {_RUNBOOK}"))
    return out


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
