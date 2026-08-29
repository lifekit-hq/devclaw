#!/usr/bin/env bash
# Unattended self-deploy wrapper (spec 025 US2) — probe-checked, ONE automatic
# rollback. Called by deploy.yml's auto lane (devclaw triggered the workflow
# itself after a devclaw-repo merge-on-close, once the instance was quiescent).
#
# Sequence:
#   1. capture the currently running git_sha from /health (the rollback target)
#   2. run deploy-devclaw.sh <NEW_TAG> — its own 90s /health gate is the probe
#   3. on probe failure: re-run deploy-devclaw.sh <previous sha> exactly ONCE,
#      then exit non-zero so the workflow records the failed deploy
#   4. rollback itself failing is the INSTANCE-DEAD class: fire the notify
#      relay directly (the instance may be down and cannot ping for itself),
#      then exit non-zero
#
# Env:
#   DEVCLAW_NOTIFY_URL  the notify relay (optional — unset ⇒ skip the ping)
# plus everything deploy-devclaw.sh reads (DEVCLAW_ENV_FILE, DEVCLAW_REGISTRY).
#
# The manual lane (a human running deploy-devclaw.sh directly, or the
# workflow's tag input) is untouched — no rollback magic on a deliberate
# operator action.
set -uo pipefail

NEW_TAG="${1:?usage: deploy-devclaw-auto.sh <new-tag>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTH_URL="http://127.0.0.1:18791/health"

say() { printf '\033[1;36m▸ auto-deploy: %s\033[0m\n' "$*"; }
err() { printf '\033[1;31m✗ auto-deploy: %s\033[0m\n' "$*" >&2; }

ping_relay() {
  # Best-effort instance-dead ping straight to the relay — quiet mode does not
  # apply here by design (rollback failure IS the one class that must reach
  # the owner; spec 025 FR-010/FR-013).
  local text="$1"
  [[ -n "${DEVCLAW_NOTIFY_URL:-}" ]] || return 0
  curl -fsS -X POST -H 'Content-Type: application/json' \
    -d "{\"text\": \"${text}\"}" "${DEVCLAW_NOTIFY_URL}" >/dev/null 2>&1 || true
}

PREV_SHA="$(curl -fsS "$HEALTH_URL" 2>/dev/null | grep -o '"git_sha":"[^"]*"' | cut -d'"' -f4 || true)"
if [[ -z "$PREV_SHA" ]]; then
  # No healthy instance to roll back TO — refuse the unattended path rather
  # than deploy over an unknown state with no rescue anchor.
  err "cannot read the running git_sha from ${HEALTH_URL} — no rollback anchor; refusing the unattended deploy"
  ping_relay "🟥 devclaw self-deploy REFUSED: /health unreadable before deploy (no rollback anchor). Instance state unknown — check the box."
  exit 1
fi
say "rollback anchor: ${PREV_SHA}"

if bash "${HERE}/deploy-devclaw.sh" "${NEW_TAG}"; then
  say "deployed ${NEW_TAG} — probe green"
  exit 0
fi

err "deploy of ${NEW_TAG} failed its health gate — rolling back to ${PREV_SHA} (exactly once)"
if bash "${HERE}/deploy-devclaw.sh" "${PREV_SHA}"; then
  err "rolled back to ${PREV_SHA} — instance healthy on the prior version; the failed deploy of ${NEW_TAG} needs a human"
  # exit non-zero: the WORKFLOW must record this deploy as failed even though
  # the instance is healthy again.
  exit 2
fi

err "ROLLBACK FAILED — the instance may be down"
ping_relay "🟥 devclaw self-deploy: deploy of ${NEW_TAG} failed AND rollback to ${PREV_SHA} failed — the instance may be DOWN. This is the instance-dead class; intervene."
exit 3
