#!/usr/bin/env bash
# Scoped devclaw-only deploy (spec 005). Pulls the pre-built devclaw images from
# ghcr.io by tag, recreates ONLY the devclaw compose project, and verifies the
# MCP server answers /health — failing loud if it doesn't.
#
# It touches devclaw services ONLY: openclaw-gateway, openclaw-cli,
# lifekit-dashboard and notify-relay (the lifekit-stack project) are never
# recreated (SC-001). No build here — CI (.github/workflows/deploy.yml) builds
# and pushes the images; this script pulls and recreates.
#
# Usage:
#   deploy-devclaw.sh [TAG]        # default TAG=latest; pass a SHA to roll back
#   DEVCLAW_ALLOW_VOLUME_CREATE=1 deploy-devclaw.sh   # cold first-deploy only
#
# Env:
#   DEVCLAW_ENV_FILE      compose --env-file (default /srv/devclaw/.env — devclaw-owned)
#   DEVCLAW_SECRETS_FILE  the credentials' one home, the compose env_file
#                         (default /srv/devclaw/secrets.env, 0600; written HERE)
#   DEVCLAW_REGISTRY      image registry prefix (default ghcr.io/lifekit-hq)
#
# Rollback: re-run with a prior SHA tag — images are tagged by commit SHA, so
# no source revert is needed (SC-007).
set -euo pipefail

TAG="${1:-latest}"
REGISTRY="${DEVCLAW_REGISTRY:-ghcr.io/lifekit-hq}"
ENV_FILE="${DEVCLAW_ENV_FILE:-/srv/devclaw/.env}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${HERE}/docker-compose.devclaw.yml"
PROJECT="devclaw"
HEALTH_URL="http://127.0.0.1:18791/health"

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$COMPOSE_FILE" ]] || die "compose file not found: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]]     || die "env file not found: $ENV_FILE (set DEVCLAW_ENV_FILE)"

# ─── Credentials: ONE durable home, written here, never blank ──────────────
# The two credentials the instance cannot run without — CLAUDE_CODE_OAUTH_TOKEN
# (the `claude setup-token` subscription credential: host cognition + sandbox
# auth) and NODE_AUTH_TOKEN (read:packages, in-sandbox `npm ci`) — live in ONE
# place on the box: $SECRETS_FILE, the env_file the compose file declares.
# This script is that file's only writer. Under the workflow the values come
# from the repo's Actions secrets (the source of truth); on a hand run from
# the box (a rollback, an emergency recreate) they are read back from the file
# itself — so no creation path can yield a container different from the one
# the last deploy made. A required value missing or blank at any stage stops
# the deploy HERE, before the box is touched: the previous file is left
# intact, never overwritten with blank. Set-but-malformed stays fatal (the
# 2026-08-31 class). The container refuses to start without them
# (devclaw/boot_guard.py), so nothing can run degraded. Never echo a value.
# (2026-09-03: a hand recreate resolved both `${VAR:-}` to blank; the
# instance reported healthy for ~20h and a worker burned a session on a 401.)
SECRETS_FILE="${DEVCLAW_SECRETS_FILE:-/srv/devclaw/secrets.env}"
export DEVCLAW_SECRETS_FILE="$SECRETS_FILE"   # compose resolves the same path

# `|| true`: a key absent from the file is "not found", not a script error —
# under `set -e -o pipefail` the bare pipeline would abort the deploy SILENTLY.
_from_file() { { grep -E "^$1=" "$SECRETS_FILE" 2>/dev/null || true; } | tail -1 | cut -d= -f2-; }
_resolve_secret() {   # $1 = name → sets _RESOLVED from the env, else the file
  local name="$1"
  # tokens carry no whitespace, so strip ALL of it: a whitespace-only value
  # (a pasted secret with a stray newline) is blank, and blank is missing.
  _RESOLVED="$(printf '%s' "${!name:-}" | tr -d '[:space:]')"
  if [[ -z "$_RESOLVED" ]]; then
    if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
      die "$name is not supplied by the workflow — the repo Actions secret is unset or blank. \`gh secret set $name\` and re-run; $SECRETS_FILE was left untouched."
    fi
    _RESOLVED="$(_from_file "$name" | tr -d '[:space:]')"
  fi
  [[ -n "$_RESOLVED" ]] || die "$name is not set (neither in the environment nor in $SECRETS_FILE). The instance cannot run without it: set the repo Actions secret and deploy through the workflow, or add a $name=… line to $SECRETS_FILE and re-run."
}

_resolve_secret CLAUDE_CODE_OAUTH_TOKEN; _oauth="$_RESOLVED"
_resolve_secret NODE_AUTH_TOKEN;         _reg="$_RESOLVED"
unset _RESOLVED
if [[ ! "$_reg" =~ ^(ghp_|github_pat_|ghs_|gho_) ]]; then
  unset _oauth _reg
  die "NODE_AUTH_TOKEN is set but is not a GitHub token (expected a ghp_/github_pat_/ghs_/gho_ prefix). A malformed registry token reaches every sandbox and 401s there. Regenerate a read:packages-only classic PAT, \`gh secret set NODE_AUTH_TOKEN\`, and redeploy."
fi

# The home must pre-exist with the right ownership — /srv/devclaw is root-owned
# and this runs as the deploy user, so the file is created ONCE by hand and
# rewritten through its inode here (docs/runbooks/devclaw-self-deploy.md §1).
if [[ ! -f "$SECRETS_FILE" ]]; then
  unset _oauth _reg
  die "secrets file absent: $SECRETS_FILE. One-time provisioning (as root): install -m 0600 -o $(id -un) -g $(id -gn) /dev/null $SECRETS_FILE — then re-run."
fi
if [[ ! -w "$SECRETS_FILE" ]]; then
  unset _oauth _reg
  die "secrets file not writable by $(id -un): $SECRETS_FILE — chown it to the deploy user (mode 0600) and re-run."
fi
_mode="$(stat -c '%a' "$SECRETS_FILE" 2>/dev/null || stat -f '%Lp' "$SECRETS_FILE" 2>/dev/null || echo '?')"
if [[ "$_mode" != "600" ]]; then
  unset _oauth _reg
  die "secrets file mode is $_mode, expected 600: chmod 600 $SECRETS_FILE — a credential file readable by others is not a home."
fi
printf 'CLAUDE_CODE_OAUTH_TOKEN=%s\nNODE_AUTH_TOKEN=%s\n' "$_oauth" "$_reg" > "$SECRETS_FILE"
unset _oauth _reg _mode
say "credentials: CLAUDE_CODE_OAUTH_TOKEN + NODE_AUTH_TOKEN present, well-formed, written to ${SECRETS_FILE} (the one home)"

export DEVCLAW_MCP_IMAGE="${REGISTRY}/devclaw-mcp:${TAG}"
export DEVCLAW_SANDBOX_IMAGE="${REGISTRY}/devclaw-sandbox:${TAG}"

# The durable-state volume's real name — matches the compose fragment's
# ${DEVCLAW_STATE_VOLUME:-devclaw-state}. On the cutover host this is
# `compose_devclaw-state` (adopt-in-place); a fresh host uses `devclaw-state`.
STATE_VOL="${DEVCLAW_STATE_VOLUME:-$(grep -E '^DEVCLAW_STATE_VOLUME=' "$ENV_FILE" 2>/dev/null | tail -1 | cut -d= -f2- || true)}"
STATE_VOL="${STATE_VOL:-devclaw-state}"

# ─── External seams: create-if-absent (idempotent, cold-start safe) ─────────
# The network holds no data — auto-create is harmless.
if ! docker network inspect lifekit-shared >/dev/null 2>&1; then
  say "creating external network lifekit-shared"
  docker network create lifekit-shared >/dev/null
fi

# The devclaw-state volume holds ALL durable goal state. If it is absent this is
# either a genuine cold host OR a cutover where the existing volume was not yet
# adopted under this name. Creating an empty volume in the latter case silently
# loses every goal — so DEFAULT is to fail loud (FR-009). A real cold deploy
# opts in explicitly (FR-012).
if ! docker volume inspect "$STATE_VOL" >/dev/null 2>&1; then
  if [[ "${DEVCLAW_ALLOW_VOLUME_CREATE:-0}" == "1" ]]; then
    say "creating empty external volume '${STATE_VOL}' (cold first-deploy)"
    docker volume create "$STATE_VOL" >/dev/null
  else
    die "external volume '${STATE_VOL}' is absent.
    Refusing to create an empty one — a cutover with un-adopted state would
    silently start with ZERO goals. Either:
      • set DEVCLAW_STATE_VOLUME=compose_devclaw-state to adopt the existing
        volume in place (see docs/runbooks/devclaw-self-deploy.md), or
      • for a genuine fresh host, re-run with DEVCLAW_ALLOW_VOLUME_CREATE=1."
  fi
fi

# ─── Pull the pinned images (no build) ──────────────────────────────────────
say "pulling ${DEVCLAW_MCP_IMAGE}"
docker pull "$DEVCLAW_MCP_IMAGE"
say "pulling ${DEVCLAW_SANDBOX_IMAGE}"
docker pull "$DEVCLAW_SANDBOX_IMAGE"

# ─── Recreate ONLY the devclaw project ──────────────────────────────────────
say "recreating devclaw project (${PROJECT}) @ ${TAG}"
docker compose -p "$PROJECT" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d

# ─── Readiness gate — fail loud if the new container doesn't come up ─────────
say "waiting for devclaw-mcp /health"
deadline=$(( SECONDS + 90 ))
until curl -fsS "$HEALTH_URL" >/dev/null 2>&1; do
  if (( SECONDS >= deadline )); then
    printf '\033[1;31m--- last 40 log lines ---\033[0m\n' >&2
    docker compose -p "$PROJECT" -f "$COMPOSE_FILE" logs --tail=40 devclaw-mcp >&2 || true
    die "devclaw-mcp did not answer ${HEALTH_URL} within 90s — deploy FAILED (container is up but unhealthy, or crashed on boot)."
  fi
  sleep 3
done

RUNNING_SHA="$(curl -fsS "$HEALTH_URL" | grep -o '"git_sha":"[^"]*"' | cut -d'"' -f4 || true)"
say "deployed devclaw @ tag=${TAG}  git_sha=${RUNNING_SHA:-unknown}"
echo "  OpenClaw-side containers were NOT touched (scoped to project '${PROJECT}')."
