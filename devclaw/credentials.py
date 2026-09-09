"""The ONE registry of credentials that cross devclaw's hops (spec 042).

A credential travels through eight hops before the tool that needs it runs:
the repository's Actions secret, the deploy script, the on-box secrets file,
the compose ``env_file``, the devclaw container, ``docker run -e``, the
sandbox container, the runner's agent allowlist, the agent's shell. Before
this module every hop was a hand-written list with its own spelling of the
same name, so each credential was added to some hops and not others and the
miss surfaced at the last hop as a worker's prose — the OAuth token on
2026-08-24 (#644: in the container, never in the agent), the registry token
on 2026-09-08 (same hop), plus the wrong-prefix, unset, and nested-``.npmrc``
incidents in between. Six instance fixes, no registry.

This module is the registry. Every hop devclaw owns iterates it instead of
naming a credential: the boot guard (``required``), doctor (presence, shape,
live probe), the sandbox launcher (``sandbox`` ⇒ ``-e``), and the runner's
agent allowlist (``agent`` ⇒ the host hands the runner the exact names to
forward — the runner never spells a credential itself, spec 011). A
credential name spelled anywhere else in the package fails the build
(``tests/test_credentials_single_registry.py``).

Least privilege lives here too: ``scope`` is the minimum the credential is
issued with, and ``agent`` is the only way a secret reaches a shell. A read
the host can do stays on the host and only the fact goes down (the red-CI
log, tinyspec ``red-ci-log-to-worker``) — which is why the GitHub credential
is registered ``sandbox=False, agent=False``: it is the most privileged thing
devclaw holds and it never crosses the fence.

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
    #: crosses ``docker run -e`` into the sandbox container
    sandbox: bool = False
    #: forwarded by the runner into the agent's own shells — the only way a
    #: secret reaches a tool the agent runs
    agent: bool = False
    #: accepted value prefixes; empty ⇒ any shape
    prefixes: tuple[str, ...] = ()


#: GitHub token shapes — classic PAT, fine-grained PAT, app installation, OAuth
#: app. Asserted at deploy time and by doctor; a wrong-shaped value is worse
#: than none (it rides every hop and only surfaces as a 401 after a session).
GH_TOKEN_PREFIXES: tuple[str, ...] = ("ghp_", "github_pat_", "ghs_", "gho_")

OAUTH_TOKEN = Credential(
    "CLAUDE_CODE_OAUTH_TOKEN",
    purpose="subscription OAuth for host cognition and the in-sandbox agent",
    scope="Pro/Max subscription setup-token (claude setup-token); never a metered key",
    required=True, sandbox=True, agent=True,
)

REGISTRY_TOKEN = Credential(
    "NODE_AUTH_TOKEN",
    purpose="npm ci against GitHub Packages (@lifekit-hq/*) inside the sandbox",
    scope="read:packages only — cannot push, merge, or touch issues/PRs",
    required=True, sandbox=True, agent=True,
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
        "every host-side GitHub call: delivery (push, PR, merge), intake, the "
        "issue doorway, the self-issue filer, and the failing-job log read that "
        "feeds a red-CI correction"
    ),
    scope=(
        "repo (push/PR/merge on the driven repos) + actions:read — the log read "
        "is a distinct scope and its absence degrades silently, which is the "
        "reason this credential is declared rather than mounted"
    ),
    required=True, sandbox=False, agent=False,
    prefixes=GH_TOKEN_PREFIXES,
)

#: the registry — the order is the order every hop reports them in
REGISTRY: tuple[Credential, ...] = (OAUTH_TOKEN, REGISTRY_TOKEN, DELIVERY_TOKEN)

#: metered-billing keys that are actively refused at every hop (constitution I):
#: stripped by the host before any subprocess, refused by the runner outright
REFUSED: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")


def by_var(var: str) -> Credential:
    for c in REGISTRY:
        if c.var == var:
            return c
    raise KeyError(var)


def required_vars() -> tuple[str, ...]:
    """Names the production engine cannot start without."""
    return tuple(c.var for c in REGISTRY if c.required)


def sandbox_vars() -> tuple[str, ...]:
    """Names that cross into the sandbox container."""
    return tuple(c.var for c in REGISTRY if c.sandbox)


def agent_vars() -> tuple[str, ...]:
    """Names the runner forwards into the agent's own shells — handed to the
    runner in the task payload, never spelled inside it."""
    return tuple(c.var for c in REGISTRY if c.agent)


def sandbox_env(environ: Mapping[str, str]) -> list[tuple[str, str]]:
    """``(var, value)`` pairs for every sandbox credential the host carries.
    Absent/blank ⇒ not forwarded (byte-identical pre-credential posture)."""
    out: list[tuple[str, str]] = []
    for c in REGISTRY:
        if not c.sandbox:
            continue
        v = environ.get(c.var, "").strip()
        if v:
            out.append((c.var, v))
    return out


def strip_refused(env: Mapping[str, str]) -> dict[str, str]:
    """A copy of ``env`` without the refused metered keys — the ONE strip
    every host subprocess (cognition, the docker CLI, the host runner) uses."""
    clean = dict(env)
    for name in REFUSED:
        clean.pop(name, None)
    return clean


def well_formed(c: Credential, value: str) -> bool:
    """Shape check only — never a liveness probe."""
    return not c.prefixes or value.startswith(c.prefixes)
