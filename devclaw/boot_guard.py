"""Boot-time refusal: the production engine never starts without its credentials.

The two credentials the instance cannot run without have ONE durable home on
the box: the secrets file the compose file declares as its ``env_file``,
written by ``deploy/deploy-devclaw.sh`` from the repository's Actions secrets.
Every path that creates the container reads that file — the workflow's manual
and auto lanes, a rollback, a hand ``docker compose up`` — so every path
yields the same container. This module is the last line: a container that
somehow starts WITHOUT a required credential exits here, non-zero, before it
can answer ``/health``. It never runs degraded.

Why it exists (2026-09-03): a hand recreate after an env-file edit resolved
``${NODE_AUTH_TOKEN:-}`` and ``${CLAUDE_CODE_OAUTH_TOKEN:-}`` to blank; the
container reported healthy for ~20 hours, cognition rode the revocable
mounted login, and a worker burned a session on an ``npm ci`` 401 before a
project-wide hold fired. Absence was *supported* at every layer, so a wrong
container was indistinguishable from a right one. It no longer is
(``specs/tiny/durable-container-secrets.md``).
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from . import config as _config
from .engine.sandcastle import OAUTH_TOKEN_VAR, REGISTRY_TOKEN_VAR

#: credentials the production engine (``DEVCLAW_ENGINE`` unset) refuses to
#: start without. ``host`` / ``stub`` are the dev/test engines and need neither.
REQUIRED_PRODUCTION_ENV: tuple[str, ...] = (OAUTH_TOKEN_VAR, REGISTRY_TOKEN_VAR)

#: the one home — where the deploy writes them and the compose file reads them
#: (docs/reference/env-vars.md, docs/runbooks/devclaw-self-deploy.md §1).
SECRETS_FILE_DEFAULT = "/srv/devclaw/secrets.env"


def required_env(engine: str) -> tuple[str, ...]:
    """The credentials this engine mode cannot start without."""
    return REQUIRED_PRODUCTION_ENV if engine == "" else ()


def missing_required_env(environ: Mapping[str, str], engine: str) -> list[str]:
    """Names (never values) of required credentials that are unset or blank —
    blank and absent are the same thing at every stage."""
    return [name for name in required_env(engine) if not environ.get(name, "").strip()]


def refusal_message(missing: list[str]) -> str:
    names = ", ".join(missing)
    return (
        f"devclaw-mcp refuses to start: required credential(s) not set: {names}. "
        "The production engine never runs without them — a container started "
        "this way would silently run on the revocable mounted login and burn "
        "worker sessions on registry 401s. Their one home is the on-box secrets "
        f"file the compose file declares (default {SECRETS_FILE_DEFAULT}), "
        "written by deploy/deploy-devclaw.sh from the repo's Actions secrets: "
        "redeploy through the Deploy workflow (`gh workflow run deploy.yml -f "
        "tag=<sha>`), or fix that file and recreate. A credential-less dev run "
        "wants DEVCLAW_ENGINE=host or DEVCLAW_ENGINE=stub."
    )


def assert_required_env(
    environ: Mapping[str, str] | None = None, engine: str | None = None
) -> None:
    """Raise ``SystemExit`` (non-zero, names only — never a value) when a
    required credential is missing. Called first thing in the server
    entrypoint, before anything serves."""
    env = os.environ if environ is None else environ
    mode = _config.ENGINE if engine is None else engine
    missing = missing_required_env(env, mode)
    if missing:
        raise SystemExit(refusal_message(missing))
