"""The single doorway for ``DEVCLAW_*`` configuration.

Every ``DEVCLAW_*`` variable the host runtime reads is read HERE — one home,
one default, one parse (``tests/test_config_single_doorway.py`` enforces it;
``tests/test_env_vars_doc_sync.py`` keeps ``docs/reference/env-vars.md`` in
lockstep). Import-time constants are read once; ``def`` accessors read live.

Outside this module on purpose: ``runner/`` (the in-sandbox worker reads its
own env inside the container), ``_env_loader``'s ``DEVCLAW_DOTENV`` bootstrap,
and env *mutation* for subprocesses (the credential strip is an invariant, not
configuration).
"""

from __future__ import annotations

import os
from pathlib import Path

from ._env_loader import load_dotenv as _load_dotenv

_load_dotenv()

#: Shape of a persisted run_schedule and the disabled default when none is set.
DEFAULT_RUN_SCHEDULE: dict = {"enabled": False, "start": "09:00", "end": "18:00", "tz": "Europe/Kyiv"}


# ---- storage & identity --------------------------------------------------

def db_path() -> str:
    """Absolute path of the SQLite home (``DEVCLAW_DB``, default ./devclaw.db)."""
    return os.path.abspath(os.environ.get("DEVCLAW_DB", "devclaw.db"))


def git_name() -> "str | None":
    return os.environ.get("DEVCLAW_GIT_NAME")


def git_email() -> "str | None":
    return os.environ.get("DEVCLAW_GIT_EMAIL")


def self_repo() -> str:
    """``owner/name`` of devclaw's own repo — the self-deploy target (may be "")."""
    return (os.environ.get("DEVCLAW_SELF_REPO") or "").strip()


def webhook_secret() -> str:
    """The GitHub webhook HMAC secret. Empty ⇒ the route answers 404."""
    return (os.environ.get("DEVCLAW_WEBHOOK_SECRET") or "").strip()


def deploy_quiescence_s() -> int:
    """How long a pending self-deploy may wait for task quiescence (default 6h)."""
    try:
        return int(os.environ.get("DEVCLAW_DEPLOY_QUIESCENCE_S", "21600"))
    except ValueError:
        return 21600


def git_sha() -> "str | None":
    return os.environ.get("DEVCLAW_GIT_SHA") or None


def built_at() -> "str | None":
    return os.environ.get("DEVCLAW_BUILT_AT") or None


# ---- server ---------------------------------------------------------------

HTTP_PORT = int(os.environ.get("DEVCLAW_PORT", "8000"))
HTTP_HOST = os.environ.get("DEVCLAW_HOST", "0.0.0.0")
#: bearer token for the HTTP transport; "" disables auth (local dev).
AUTH_TOKEN = os.environ.get("DEVCLAW_TOKEN", "")
#: "" = production sandbox; "host" / "stub" select the dev/test engines.
ENGINE = os.environ.get("DEVCLAW_ENGINE", "")


def transport() -> str:
    return os.environ.get("DEVCLAW_TRANSPORT", "stdio")


# ---- task queue -----------------------------------------------------------

GLOBAL_MAX_CONCURRENT = int(os.environ.get("DEVCLAW_MAX_CONCURRENT", "4"))
#: host RAM kept free beyond the sandboxes (docker mem string).
HOST_MEM_RESERVE = os.environ.get("DEVCLAW_HOST_MEM_RESERVE", "1536m")
TICK_SECONDS = float(os.environ.get("DEVCLAW_TICK_SECONDS", "10"))
TASK_TIMEOUT_S = float(os.environ.get("DEVCLAW_TASK_TIMEOUT_S", "3600"))


# ---- goal layer -----------------------------------------------------------

def goal_notify_url() -> str:
    return os.environ.get("DEVCLAW_GOAL_NOTIFY_URL", "")


def goal_tick_seconds() -> int:
    return int(os.environ.get("DEVCLAW_GOAL_TICK_SECONDS", "900"))


def ci_log_tail_lines() -> int:
    """Lines of a failing CI job's log carried to the session, per check."""
    return int(os.environ.get("DEVCLAW_CI_LOG_TAIL_LINES", "120"))


def sessions_per_day() -> int:
    """``DEVCLAW_GOAL_SESSIONS_PER_DAY`` — the ONE money brake per goal (spec
    046): sessions a goal may start per UTC calendar day. Read from task rows."""
    try:
        return max(1, int(os.environ.get("DEVCLAW_GOAL_SESSIONS_PER_DAY", "8")))
    except ValueError:
        return 8


def mention() -> str:
    """``DEVCLAW_MENTION`` — the handle an issue/PR comment must carry to be an
    instruction (the owner's one channel, spec 046)."""
    return (os.environ.get("DEVCLAW_MENTION") or "@devclaw").strip()


def context_tripwire_pct() -> int:
    """Forwarded into the sandbox: the context-usage % at which the runner
    lands the session. ``0`` disables."""
    try:
        return int(os.environ.get("DEVCLAW_CONTEXT_TRIPWIRE_PCT", "75"))
    except ValueError:
        return 75


# ---- engines & sandbox ----------------------------------------------------

SANDBOX_IMAGE = os.environ.get("DEVCLAW_SANDBOX_IMAGE", "devclaw-sandbox:latest")
DOCKER_BIN = os.environ.get("DEVCLAW_DOCKER_BIN", "docker")
#: the model the IN-SANDBOX worker runs; "" → the agent's own default.
EXEC_MODEL = os.environ.get("DEVCLAW_EXEC_MODEL", "claude-sonnet-4-6") or None
ACP_COMMAND = os.environ.get("DEVCLAW_ACP_COMMAND", "") or None
SANDBOX_MEMORY = os.environ.get("DEVCLAW_SANDBOX_MEMORY", "2g")
SANDBOX_CPUS = os.environ.get("DEVCLAW_SANDBOX_CPUS", "2.0")
SANDBOX_CLAUDE_ALLOWLIST_RAW = os.environ.get("DEVCLAW_SANDBOX_CLAUDE_ALLOWLIST", "")


def container_path_prefix() -> "str | None":
    return os.environ.get("DEVCLAW_CONTAINER_PATH_PREFIX")


def host_path_prefix() -> "str | None":
    return os.environ.get("DEVCLAW_HOST_PATH_PREFIX")


def host_claude_dir() -> str:
    return os.environ.get("DEVCLAW_HOST_CLAUDE_DIR") or str(Path.home() / ".claude")


RUNNER_PY_OVERRIDE = os.environ.get("DEVCLAW_RUNNER_PY")
SKILLS_DIR_OVERRIDE = os.environ.get("DEVCLAW_SKILLS_DIR")
HOOKS_DIR_OVERRIDE = os.environ.get("DEVCLAW_HOOKS_DIR")
RUNNER_PYTHON_OVERRIDE = os.environ.get("DEVCLAW_RUNNER_PYTHON")


# ---- retention ------------------------------------------------------------

def events_retention_days_raw() -> "str | None":
    return os.environ.get("DEVCLAW_EVENTS_RETENTION_DAYS")
