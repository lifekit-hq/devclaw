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
#   DEVCLAW_ENV_FILE   compose --env-file (default /srv/lifekit-stack/.env)
#   DEVCLAW_REGISTRY   image registry prefix (default ghcr.io/lifekit-hq)
#
# Rollback: re-run with a prior SHA tag — images are tagged by commit SHA, so
# no source revert is needed (SC-007).
set -euo pipefail

TAG="${1:-latest}"
REGISTRY="${DEVCLAW_REGISTRY:-ghcr.io/lifekit-hq}"
ENV_FILE="${DEVCLAW_ENV_FILE:-/srv/lifekit-stack/.env}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_FILE="${HERE}/docker-compose.devclaw.yml"
PROJECT="devclaw"
HEALTH_URL="http://127.0.0.1:18791/health"

say()  { printf '\033[1;36m▸ %s\033[0m\n' "$*"; }
die()  { printf '\033[1;31m✗ %s\033[0m\n' "$*" >&2; exit 1; }

[[ -f "$COMPOSE_FILE" ]] || die "compose file not found: $COMPOSE_FILE"
[[ -f "$ENV_FILE" ]]     || die "env file not found: $ENV_FILE (set DEVCLAW_ENV_FILE)"

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
