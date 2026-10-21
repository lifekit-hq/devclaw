"""The ONE registry of credentials that cross devclaw's hops (spec 042).

A credential travels through six hops before the tool that needs it runs:
the repository's Actions secret, the deploy script, the on-box secrets file,
the compose ``env_file``, the devclaw container, the session's environment.
Before
this module every hop was a hand-written list with its own spelling of the
same name, so each credential was added to some hops and not others and the
miss surfaced at the last hop as a worker's prose — the OAuth token on
2026-08-24 (#644: in the container, never in the agent), the registry token
on 2026-09-08 (same hop), plus the wrong-prefix, unset, and nested-``.npmrc``
incidents in between. Six instance fixes, no registry.

This module is the registry. Every hop devclaw owns iterates it instead of
naming a credential: the boot guard (``required``), doctor (presence, shape,
live probe), and the session environment (:func:`session_env` — ``agent``
credentials stay, every other registered one is removed). A credential name
spelled anywhere else in the package fails the build
(``tests/test_credentials_single_registry.py``).

Least privilege lives here too: ``scope`` is the minimum the credential is
issued with, and ``agent`` is the only way a secret reaches a shell. A read
the host can do stays on the host and only the fact goes down (the red-CI
log, tinyspec ``red-ci-log-to-worker``) — which is why the GitHub credential
is registered ``agent=False``: it is the most privileged thing devclaw holds
and it is stripped from the session's environment. The session runs in a
worktree, not a sandbox, so this is an environment strip, not a fence.

Leaf module: imports nothing from devclaw.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Credential:
    """One credential and the hops it crosses."""

    #: the env var — the ONE spelling of this credential's name
    var: str
    #: what it is for, one line
    purpose: str
    #: the least privilege it is issued with (what an operator grants)
    scope: str
    #: the production engine refuses to start without it (``boot_guard``)
    required: bool = False
    #: left in the session's environment — the only way a secret reaches a
    #: tool the agent runs
    agent: bool = False
    #: accepted value prefixes; empty ⇒ any shape
    prefixes: tuple[str, ...] = ()


#: GitHub token shapes — classic PAT, fine-grained PAT, app installation, OAuth
#: app. Asserted at deploy time and by doctor; a wrong-shaped value is worse
#: than none (it rides every hop and only surfaces as a 401 after a session).
GH_TOKEN_PREFIXES: tuple[str, ...] = ("ghp_", "github_pat_", "ghs_", "gho_")

OAUTH_TOKEN = Credential(
    "CLAUDE_CODE_OAUTH_TOKEN",
    purpose="subscription OAuth for the session's claude",
    scope="Pro/Max subscription setup-token (claude setup-token); never a metered key",
    required=True, agent=True,
)

REGISTRY_TOKEN = Credential(
    "NODE_AUTH_TOKEN",
    purpose="npm ci against GitHub Packages (@lifekit-hq/*) inside a session",
    scope="read:packages only — cannot push, merge, or touch issues/PRs",
    required=True, agent=True,
    prefixes=GH_TOKEN_PREFIXES,
)

#: ``GH_TOKEN``, not ``GITHUB_TOKEN``: the deploy carries every credential in
#: from a repository Actions secret, and GitHub refuses to create a secret
#: whose name starts with ``GITHUB_`` (``secrets.GITHUB_TOKEN`` is the
#: workflow's own job-scoped, hourly-expiring token — useless to a long-lived
#: instance). ``gh`` and ``gh auth git-credential`` both rank ``GH_TOKEN``
#: above ``GITHUB_TOKEN``, so one name serves every host-side read and write.
DELIVERY_TOKEN = Credential(
    "GH_TOKEN",
    purpose=(
        "every host-side GitHub call: delivery (push, PR, merge), the goal "
        "layer's reads of the issue, PR and CI, and the failing-job log read"
    ),
    scope=(
        "repo — push/PR/merge on the driven repos, the goal layer's reads, "
        "and the Actions job-log read (`repo` covers the Actions API for a "
        "classic/OAuth token). Declared rather than mounted because a "
        "bind-mounted ~/.config/gh is a revocable personal login nothing "
        "checks"
    ),
    required=True, agent=False,
    prefixes=GH_TOKEN_PREFIXES,
)

MCP_TOKEN = Credential(
    "DEVCLAW_TOKEN",
    purpose="bearer auth on devclaw's own HTTP/MCP surface (decide, cancel_goal, …)",
    scope="the owner's verbs on this instance; unset = no auth (local dev only)",
    agent=False,
)

NOTIFY_URL = Credential(
    "DEVCLAW_GOAL_NOTIFY_URL",
    purpose="where owner pings go (a webhook URL that can embed its own secret)",
    scope="post to the owner's notification channel only",
    agent=False,
)

#: the registry — the order is the order every hop reports them in
REGISTRY: tuple[Credential, ...] = (OAUTH_TOKEN, REGISTRY_TOKEN, DELIVERY_TOKEN, MCP_TOKEN, NOTIFY_URL)

#: metered-billing keys that are actively refused at every hop (constitution I):
#: stripped by the host before any subprocess
REFUSED: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def by_var(var: str) -> Credential:
    for c in REGISTRY:
        if c.var == var:
            return c
    raise KeyError(var)


def required_vars() -> tuple[str, ...]:
    """Names the production engine cannot start without."""
    return tuple(c.var for c in REGISTRY if c.required)


def probed() -> tuple[Credential, ...]:
    """The credentials doctor checks and the world fingerprint probes: the ones
    the instance needs or a session uses. A host-only optional one is stripped
    from the session and never probed."""
    return tuple(c for c in REGISTRY if c.required or c.agent)


def agent_vars() -> tuple[str, ...]:
    """Names a session's own shells keep."""
    return tuple(c.var for c in REGISTRY if c.agent)


def strip_refused(env: Mapping[str, str]) -> dict[str, str]:
    """A copy of ``env`` without the refused metered keys — the ONE strip
    every host subprocess uses."""
    clean = dict(env)
    for name in REFUSED:
        clean.pop(name, None)
    return clean


def session_env(env: Mapping[str, str]) -> dict[str, str]:
    """The environment a session (and its verify run) gets: the refused keys
    and every registered credential that is not ``agent`` removed."""
    clean = strip_refused(env)
    for c in REGISTRY:
        if not c.agent:
            clean.pop(c.var, None)
    return clean


def well_formed(c: Credential, value: str) -> bool:
    """Shape check only — never a liveness probe."""
    return not c.prefixes or value.startswith(c.prefixes)
