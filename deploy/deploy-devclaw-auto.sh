#!/usr/bin/env bash
# Unattended self-deploy wrapper (spec 025 US2) — probe-checked, ONE automatic
# rollback. Called by deploy.yml's auto lane (devclaw triggered the workflow
# itself after a devclaw-repo merge-on-close, once the instance was quiescent).
#
# Sequence:
#   1. capture the currently running git_sha from /health (the rollback target)
#   2. run deploy-devclaw.sh <NEW_TAG> — its own 90s /health gate is the probe
#   3. on probe success: prune old devclaw-mcp/devclaw-sandbox tags (see
#      cleanup_old_images below), then exit 0
#   4. on probe failure: re-run deploy-devclaw.sh <previous sha> exactly ONCE,
#      then exit non-zero so the workflow records the failed deploy — no
#      cleanup on this path
#   5. rollback itself failing is the INSTANCE-DEAD class: fire the notify
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
#
# Image cleanup (source of the box's Docker garbage — 151 of 263 images
# pruned 2026-09-15 were devclaw-mcp/devclaw-sandbox, ~5.6 GB per merge):
# on the SUCCESS path only (probe green, no rollback), prune every
# devclaw-mcp/devclaw-sandbox tag except NEW_TAG, PREV_SHA and latest.
# A failed or rolled-back deploy removes nothing. A cleanup failure is
# logged and never fails an otherwise healthy deploy (see cleanup_old_images).
set -uo pipefail

NEW_TAG="${1:?usage: deploy-devclaw-auto.sh <new-tag>}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HEALTH_URL="http://127.0.0.1:18791/health"
REGISTRY="${DEVCLAW_REGISTRY:-ghcr.io/lifekit-hq}"

say() { printf '\033[1;36m▸ auto-deploy: %s\033[0m\n' "$*"; }
err() { printf '\033[1;31m✗ auto-deploy: %s\033[0m\n' "$*" >&2; }

# Prune old devclaw-mcp/devclaw-sandbox tags, keeping exactly $1 (new tag),
# $2 (the rollback anchor) and 'latest'. Never removes an image a container
# — running or stopped — still references (by name:tag or by image ID, since
# a container's image reference does not always match the tag being pruned).
# An empty/unreadable rollback anchor means keep EVERYTHING: this function
# never guesses what was safe to drop. Every failure is logged and swallowed
# — cleanup is best-effort tidying, never a deploy-blocking step.
cleanup_old_images() {
  local new_tag="$1" prev_sha="$2"
  if [[ -z "$new_tag" || -z "$prev_sha" ]]; then
    err "cleanup: NEW_TAG or PREV_SHA is empty/unreadable — keeping all images, skipping cleanup"
    return 0
  fi

  local -a in_use_names in_use_ids all_containers
  mapfile -t all_containers < <(docker ps -a --format '{{.ID}}' 2>/dev/null || true)
  mapfile -t in_use_names < <(docker ps -a --format '{{.Image}}' 2>/dev/null || true)
  in_use_ids=()
  if (( ${#all_containers[@]} > 0 )); then
    mapfile -t in_use_ids < <(docker inspect -f '{{.Image}}' "${all_containers[@]}" 2>/dev/null || true)
  fi

  local repo tag image id keep_tag skip n
  for repo in devclaw-mcp devclaw-sandbox; do
    local -a keep_tags=("$new_tag" "$prev_sha" "latest")
    while IFS= read -r image; do
      [[ -n "$image" ]] || continue
      tag="${image##*:}"
      skip=0
      for keep_tag in "${keep_tags[@]}"; do
        [[ "$tag" == "$keep_tag" ]] && { skip=1; break; }
      done
      (( skip == 1 )) && continue

      for n in "${in_use_names[@]}"; do
        [[ "$n" == "$image" ]] && { skip=1; break; }
      done
      if (( skip == 1 )); then
        say "cleanup: keeping ${image} — a container still references it"
        continue
      fi

      id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
      if [[ -n "$id" ]]; then
        for n in "${in_use_ids[@]}"; do
          [[ "$n" == "$id" ]] && { skip=1; break; }
        done
      fi
      if (( skip == 1 )); then
        say "cleanup: keeping ${image} — a container still references its image ID"
        continue
      fi

      say "cleanup: removing ${image}"
      docker rmi "$image" >/dev/null 2>&1 \
        || err "cleanup: failed to remove ${image} (non-fatal, deploy stands)"
    done < <(docker images --format '{{.Repository}}:{{.Tag}}' "${REGISTRY}/${repo}" 2>/dev/null || true)
  done
}

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
  cleanup_old_images "${NEW_TAG}" "${PREV_SHA}"
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
