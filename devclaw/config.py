"""The single doorway for ``DEVCLAW_*`` configuration.

Every ``DEVCLAW_*`` environment variable the host runtime consumes is read in
THIS module — one home per variable, one default, one parse. Before it existed
the same ~60 variables were read ad-hoc across ~30 modules: two files parsed
``DEVCLAW_DB`` and ``DEVCLAW_GOALS_DIR`` independently with their own defaults
(the same-fact-computed-twice drift class, cf. #630), and *when* a module got
imported silently decided which value it froze. ``tests/test_config_single_doorway.py``
enforces the boundary structurally; ``tests/test_env_vars_doc_sync.py`` keeps
``docs/reference/env-vars.md`` in lockstep with the reads, exactly as before.

Two shapes, matching how each value was already consumed:

- **Import-time constants** — parsed once when this module first loads (after
  the idempotent ``.env`` load below). Consumers that used to freeze their own
  module constant now import it from here; tests that monkeypatch the
  *consumer's* binding keep working (``from`` imports create per-module names).
- **Call-time accessors** (``def``) — read the environment live on every call,
  for the values whose consumers always did (and whose tests ``setenv`` them).

Deliberately OUTSIDE this module:

- ``runner/`` — the in-sandbox worker is standalone by design (spec 011) and
  reads its own env inside the container.
- ``_env_loader``'s ``DEVCLAW_DOTENV`` — the bootstrap that must run before
  any config exists.
- Env *mutation* for subprocesses (the OAuth key-stripping in cognition/llm_call/
  engine, ``GIT_TERMINAL_PROMPT``, child-env composition) — that is not
  configuration, and the stripping is a load-bearing invariant.
- Non-``DEVCLAW_`` reads (``PATH``, ``HOME``, ``CLAUDE_CONFIG_DIR``, …) and the
  console's env-catalog introspection (``server/routes/control.py``), which
  reports the raw environment on purpose.

Nontrivial fail-safe parses (cognition timeout/retries, retention days) stay
with their consumers — this module hands them the raw string; their tuned
never-crash-import semantics are theirs.
"""

from __future__ import annotations

import os
from pathlib import Path

# .env is the per-machine default layer; real env (shell/systemd/compose) wins.
# Idempotent + non-overriding, so the server's own early load stays harmless.
from ._env_loader import load_dotenv as _load_dotenv

_load_dotenv()


# ---- storage & identity --------------------------------------------------

def db_path() -> str:
    """Absolute path of the SQLite home (``DEVCLAW_DB``, default ./devclaw.db).
    Read live: the CLI resolves it per invocation."""
    return os.path.abspath(os.environ.get("DEVCLAW_DB", "devclaw.db"))


def goals_dir_raw() -> str:
    """``DEVCLAW_GOALS_DIR`` before expansion (default ``~/memory/goals``)."""
    return os.environ.get("DEVCLAW_GOALS_DIR", "~/memory/goals")


def goals_dir() -> str:
    """User-expanded goals directory."""
    return os.path.expanduser(goals_dir_raw())


def git_name() -> "str | None":
    return os.environ.get("DEVCLAW_GIT_NAME")


def git_email() -> "str | None":
    return os.environ.get("DEVCLAW_GIT_EMAIL")


def github_owner() -> "str | None":
    """Owner for new repos (``DEVCLAW_GITHUB_OWNER``). None → gh's own account."""
    return os.environ.get("DEVCLAW_GITHUB_OWNER") or None


def self_repo() -> str:
    """``owner/name`` of devclaw's own repo for self-issue filing (may be "")."""
    return (os.environ.get("DEVCLAW_SELF_REPO") or "").strip()


def webhook_secret() -> str:
    """Spec 023: the GitHub webhook HMAC secret. Empty ⇒ the webhook route is
    OFF (answers 404) — no unauthenticated surface ever exists."""
    return (os.environ.get("DEVCLAW_WEBHOOK_SECRET") or "").strip()


def deploy_quiescence_s() -> int:
    """Spec 025 US2: how long a pending self-deploy may wait for task
    quiescence before it expires loudly (re-armed by the next devclaw-repo
    close or operator resume). Invalid/unset ⇒ 6h."""
    try:
        return int(os.environ.get("DEVCLAW_DEPLOY_QUIESCENCE_S", "21600"))
    except ValueError:
        return 21600


# ---- server (MCP surface) ------------------------------------------------

HTTP_PORT = int(os.environ.get("DEVCLAW_PORT", "8000"))
#: default 0.0.0.0 so sibling compose containers reach the endpoint;
#: set 127.0.0.1 to restrict to loopback.
HTTP_HOST = os.environ.get("DEVCLAW_HOST", "0.0.0.0")
#: bearer token for the HTTP transport; "" disables auth (local dev).
AUTH_TOKEN = os.environ.get("DEVCLAW_TOKEN", "")
#: "" = production sandcastle; "host" / "stub" select the dev/test engines.
ENGINE = os.environ.get("DEVCLAW_ENGINE", "")


def transport() -> str:
    """``DEVCLAW_TRANSPORT`` (default stdio) — read at serve time."""
    return os.environ.get("DEVCLAW_TRANSPORT", "stdio")


def git_sha() -> "str | None":
    """Build identity stamped by the deploy workflow (``DEVCLAW_GIT_SHA``)."""
    return os.environ.get("DEVCLAW_GIT_SHA") or None


def built_at() -> "str | None":
    return os.environ.get("DEVCLAW_BUILT_AT") or None


# ---- task queue ----------------------------------------------------------

GLOBAL_MAX_CONCURRENT = int(os.environ.get("DEVCLAW_MAX_CONCURRENT", "4"))
#: raw docker mem string; the queue parses it with its ``_parse_mem``.
COGNITION_MEM_RESERVE = os.environ.get("DEVCLAW_COGNITION_MEM_RESERVE", "1536m")
TICK_SECONDS = float(os.environ.get("DEVCLAW_TICK_SECONDS", "10"))
TASK_TIMEOUT_S = float(os.environ.get("DEVCLAW_TASK_TIMEOUT_S", "3600"))
TASK_MAX_RETRIES = int(os.environ.get("DEVCLAW_MAX_RETRIES", "1"))
#: Wall-clock cap for the host-side evidence verify run after a no-result
#: termination (issue #565). Mirrors the runner's own default.
EVIDENCE_VERIFY_TIMEOUT_S = int(os.environ.get("DEVCLAW_VERIFY_TIMEOUT_S", "900"))
BROWSER_GATE_ENABLED = os.environ.get("DEVCLAW_GOAL_BROWSER_GATE", "1") not in ("0", "false", "")


# ---- goal layer ----------------------------------------------------------

def goal_notify_url() -> str:
    return os.environ.get("DEVCLAW_GOAL_NOTIFY_URL", "")


def goal_tick_seconds() -> int:
    return int(os.environ.get("DEVCLAW_GOAL_TICK_SECONDS", "900"))


# ---- autonomy-ratchet thresholds (spec 018 US4) ---------------------------
# The finish-line numbers the scorecard grades itself against (agreed
# 2026-08-25). Configuration, not code: tune without a deploy. The gate they
# feed is INFORMATIONAL ONLY — the spec 007 autonomy flip stays a manual
# operator act; no mechanism reads the verdict.


def goal_text_budget() -> int:
    """``DEVCLAW_GOAL_TEXT_BUDGET`` — max characters of free text (the
    objective) on a REFERENCED goal (spec 019 US3). The knowledge belongs in
    the referenced issue; the goal is ordering/scope glue. Issue-less goals
    are exempt. Hard refusal at the doorway, no override (clarified
    2026-08-25)."""
    return int(os.environ.get("DEVCLAW_GOAL_TEXT_BUDGET", "1000"))


def context_tripwire_pct() -> int:
    """``DEVCLAW_CONTEXT_TRIPWIRE_PCT`` — the worker context-usage percentage
    at which the in-sandbox runner ends the turn and lands a coherent partial
    increment instead of running into the model's context wall (spec 021 US2).
    ``0`` disables. Forwarded into the sandbox env by the engine; the runner
    reads its own copy (the runner/ doorway exception above)."""
    try:
        return int(os.environ.get("DEVCLAW_CONTEXT_TRIPWIRE_PCT", "75"))
    except ValueError:
        return 75


def ratchet_first_pass() -> float:
    """``DEVCLAW_RATCHET_FIRST_PASS`` — per-goal first-pass rate threshold."""
    return float(os.environ.get("DEVCLAW_RATCHET_FIRST_PASS", "0.70"))


def ratchet_decided_merge() -> float:
    """``DEVCLAW_RATCHET_DECIDED_MERGE`` — decided-PR merge-rate threshold."""
    return float(os.environ.get("DEVCLAW_RATCHET_DECIDED_MERGE", "0.80"))


def ratchet_window_days() -> int:
    """``DEVCLAW_RATCHET_WINDOW_DAYS`` — the rolling window every ratchet
    metric (and the wedge-free-cycles condition) is judged over."""
    return int(os.environ.get("DEVCLAW_RATCHET_WINDOW_DAYS", "14"))


#: wall-clock seconds an EXECUTING goal may go without a delivery before the
#: no-progress watchdog pings the owner once. 0 disables. Default 6h.
NO_PROGRESS_S = int(os.environ.get("DEVCLAW_GOAL_NO_PROGRESS_S", "21600"))
REMOTE_CHECKS_ENABLED = os.environ.get("DEVCLAW_GOAL_REMOTE_CHECKS", "1") not in ("0", "false", "")
#: on by default; 0 sends raw text instead of the plain-language rewrite.
PLAIN_SUMMARY_ENABLED = os.environ.get("DEVCLAW_GOAL_PLAIN_SUMMARY", "1") not in ("0", "false", "")
DONEGATE_LEAN = os.environ.get("DEVCLAW_DONEGATE_LEAN", "0") == "1"

#: nightly run-cycle window (cycle reports group by it).
CYCLE_WINDOW_START = os.environ.get("DEVCLAW_RUN_CYCLE_START", "22:00")
CYCLE_WINDOW_END = os.environ.get("DEVCLAW_RUN_CYCLE_END", "05:00")
CYCLE_WINDOW_TZ = os.environ.get("DEVCLAW_RUN_CYCLE_TZ", "Europe/London")

#: self-issue filing dials.
SELF_ISSUE_MIN_CYCLES = int(os.environ.get("DEVCLAW_SELF_ISSUE_MIN_CYCLES", "2"))
SELF_ISSUE_QUIET_DAYS = int(os.environ.get("DEVCLAW_SELF_ISSUE_QUIET_DAYS", "3"))
SELF_ISSUE_MAX_PER_CYCLE = int(os.environ.get("DEVCLAW_SELF_ISSUE_MAX_PER_CYCLE", "3"))
SELF_FIX_CONCURRENCY = int(os.environ.get("DEVCLAW_SELF_FIX_CONCURRENCY", "1"))


def notify_altitude_raw() -> str:
    """``DEVCLAW_NOTIFY_ALTITUDE`` normalized (default owner) — read each call
    so it's overridable per process / in tests."""
    return os.environ.get("DEVCLAW_NOTIFY_ALTITUDE", "owner").strip().lower()


# ---- cognition (host claude) ---------------------------------------------

CLAUDE_BIN = os.environ.get("DEVCLAW_CLAUDE_BIN", "claude")


def cognition_name() -> str:
    """Backend selector (``DEVCLAW_COGNITION``, default claude)."""
    return os.environ.get("DEVCLAW_COGNITION", "claude").strip().lower()


def cognition_timeout_s_raw() -> "str | None":
    """``DEVCLAW_COGNITION_TIMEOUT_S`` unparsed — llm_call/cognition own the
    fail-safe parse (invalid must never crash import or a call)."""
    return os.environ.get("DEVCLAW_COGNITION_TIMEOUT_S")


def cognition_retries_raw() -> "str | None":
    return os.environ.get("DEVCLAW_COGNITION_RETRIES")


def max_host_cognition_raw() -> "str | None":
    return os.environ.get("DEVCLAW_MAX_HOST_COGNITION")


#: model tiers (light/standard/deep); empty string → None → the CLI default.
MODEL_LIGHT = os.environ.get("DEVCLAW_MODEL_LIGHT", "haiku") or None
MODEL_STANDARD = os.environ.get("DEVCLAW_MODEL_STANDARD", "sonnet") or None
MODEL_DEEP = os.environ.get("DEVCLAW_MODEL_DEEP", "opus") or None


# ---- engines & sandbox ---------------------------------------------------

SANDBOX_IMAGE = os.environ.get("DEVCLAW_SANDBOX_IMAGE", "devclaw-sandbox:latest")
DOCKER_BIN = os.environ.get("DEVCLAW_DOCKER_BIN", "docker")
#: the model the IN-SANDBOX worker runs; "" → None → the agent's own default.
EXEC_MODEL = os.environ.get("DEVCLAW_EXEC_MODEL", "claude-sonnet-4-6") or None
#: override agent command for the runner's ACP client (spec 011 seam).
ACP_COMMAND = os.environ.get("DEVCLAW_ACP_COMMAND", "") or None
SANDBOX_MEMORY = os.environ.get("DEVCLAW_SANDBOX_MEMORY", "2g")
SANDBOX_CPUS = os.environ.get("DEVCLAW_SANDBOX_CPUS", "2.0")
#: comma-separated extra allowed tools for the sandbox claude (raw entries;
#: sandcastle owns the split/default).
SANDBOX_CLAUDE_ALLOWLIST_RAW = os.environ.get("DEVCLAW_SANDBOX_CLAUDE_ALLOWLIST", "")


def container_path_prefix() -> "str | None":
    """Container-side workspace prefix for host↔container path translation."""
    return os.environ.get("DEVCLAW_CONTAINER_PATH_PREFIX")


def host_path_prefix() -> "str | None":
    return os.environ.get("DEVCLAW_HOST_PATH_PREFIX")


def host_claude_dir() -> str:
    """Host path of the ~/.claude to mount into sandboxes (see sandcastle for
    why a container-invisible path is passed through verbatim)."""
    return os.environ.get("DEVCLAW_HOST_CLAUDE_DIR") or str(Path.home() / ".claude")


#: host-engine (DEVCLAW_ENGINE=host) file locations; None → the engine derives
#: its repo-relative default (it owns _REPO_ROOT).
RUNNER_PY_OVERRIDE = os.environ.get("DEVCLAW_RUNNER_PY")
SKILLS_DIR_OVERRIDE = os.environ.get("DEVCLAW_SKILLS_DIR")
HOOKS_DIR_OVERRIDE = os.environ.get("DEVCLAW_HOOKS_DIR")
RUNNER_PYTHON_OVERRIDE = os.environ.get("DEVCLAW_RUNNER_PYTHON")


# ---- delivery & deploys --------------------------------------------------

#: deploys default to the sandbox image unless explicitly overridden.
DEPLOY_IMAGE = os.environ.get("DEVCLAW_DEPLOY_IMAGE") or SANDBOX_IMAGE
TAILSCALE_BIN = os.environ.get("DEVCLAW_TAILSCALE_BIN", "tailscale")
DEPLOY_PORT_BASE = int(os.environ.get("DEVCLAW_DEPLOY_PORT_BASE", "8200"))
DEPLOY_PORT_SPAN = int(os.environ.get("DEVCLAW_DEPLOY_PORT_SPAN", "200"))
DEPLOY_MEMORY = os.environ.get("DEVCLAW_DEPLOY_MEMORY", "512m")
DEPLOY_CPUS = os.environ.get("DEVCLAW_DEPLOY_CPUS", "1.0")
DEPLOY_MAX = int(os.environ.get("DEVCLAW_DEPLOY_MAX", "5"))


# ---- retention & host resources (live reads — construction-time dials) ---

def trace_retention_days_raw() -> "str | None":
    return os.environ.get("DEVCLAW_TRACE_RETENTION_DAYS")


def events_retention_days_raw() -> "str | None":
    return os.environ.get("DEVCLAW_EVENTS_RETENTION_DAYS")


def task_result_retention_days_raw() -> "str | None":
    return os.environ.get("DEVCLAW_TASK_RESULT_RETENTION_DAYS")


def db_size_alert_mb_raw() -> "str | None":
    return os.environ.get("DEVCLAW_DB_SIZE_ALERT_MB")


def workspace_retention_days_raw() -> "str | None":
    return os.environ.get("DEVCLAW_WORKSPACE_RETENTION_DAYS")


def failed_workspace_retention_days_raw() -> "str | None":
    return os.environ.get("DEVCLAW_WORKSPACE_RETENTION_DAYS_FAILED")


# ---- instance health drift probe (issue #596) ----------------------------


def health_disk_warn_pct() -> float:
    """Disk-usage % at which the workspace filesystem is flagged as a problem.
    ``DEVCLAW_HEALTH_DISK_WARN_PCT``, default 80. Returns 80.0 on bad input."""
    raw = os.environ.get("DEVCLAW_HEALTH_DISK_WARN_PCT", "80")
    try:
        v = float(raw)
        return v if 0 < v <= 100 else 80.0
    except (ValueError, TypeError):
        return 80.0


def health_orphan_docker_warn() -> int:
    """Orphaned docker toolchain volume count threshold.
    ``DEVCLAW_HEALTH_ORPHAN_DOCKER_WARN``, default 10. Returns 10 on bad input."""
    raw = os.environ.get("DEVCLAW_HEALTH_ORPHAN_DOCKER_WARN", "10")
    try:
        v = int(raw)
        return v if v >= 0 else 10
    except (ValueError, TypeError):
        return 10


def health_stale_ws_warn() -> int:
    """Sweep-eligible workspace directory count threshold.
    ``DEVCLAW_HEALTH_STALE_WS_WARN``, default 20. Returns 20 on bad input."""
    raw = os.environ.get("DEVCLAW_HEALTH_STALE_WS_WARN", "20")
    try:
        v = int(raw)
        return v if v >= 0 else 20
    except (ValueError, TypeError):
        return 20


def health_check_interval_s() -> int:
    """Minimum seconds between health drift probe runs.
    ``DEVCLAW_HEALTH_INTERVAL_S``, default 3600. Returns 3600 on bad/non-positive input."""
    raw = os.environ.get("DEVCLAW_HEALTH_INTERVAL_S", "3600")
    try:
        v = int(raw)
        return v if v > 0 else 3600
    except (ValueError, TypeError):
        return 3600


# ---- trend detector ------------------------------------------------------

TREND_ENABLED = os.environ.get("DEVCLAW_TREND_ENABLED", "1") != "0"
TREND_DISABLE_RAW = os.environ.get("DEVCLAW_TREND_DISABLE", "")
TREND_HARNESS_SELF_FILE = os.environ.get(
    "DEVCLAW_TREND_HARNESS_SELF_FILE", "~/memory/projects/devclaw/trends.md"
)
