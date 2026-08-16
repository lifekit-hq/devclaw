# DevClaw — self-deploy cutover runbook

Spec: [`specs/005-devclaw-self-deploy/spec.md`](../../specs/005-devclaw-self-deploy/spec.md).

devclaw now owns its own deployment. Its image is built **from the checked-out
source** (`deploy/Dockerfile` — no `git clone` inside the build), pushed to
`ghcr.io/lifekit-hq/devclaw-mcp` + `…/devclaw-sandbox` by
`.github/workflows/deploy.yml`, and run as its **own compose project**
(`docker compose -p devclaw -f deploy/docker-compose.devclaw.yml`), separate
from lifekit-stack's OpenClaw project. The two stay integrated at runtime through
two externally-declared seams: the `lifekit-shared` network and the
`devclaw-state` volume.

> **The one rule that matters: never lose the `devclaw-state` volume.** It holds
> `devclaw.db` + every durable goal. The cutover is designed so that volume is
> **adopted in place, byte-for-byte** — no copy, no re-namespace. Get the order
> below right and zero goals are lost.

---

## 0. What changes vs. today

| | Before (monolith) | After (self-deploy) |
|---|---|---|
| Image build | lifekit-stack `Dockerfile`, `git clone devclaw@main` at build | devclaw `deploy/Dockerfile`, `COPY` checked-out source |
| Cache | forced `--no-cache` every deploy + md5-verify crutch | normal cache-respecting build |
| Where it runs | `compose` project (with gateway, dashboard, …) | own `devclaw` project |
| Deploy trigger | `ci.yml` deploy job auto-fires on every push to main | `deploy.yml` `workflow_dispatch` only |
| Blast radius | full-stack rebuild SIGKILLs in-flight goals | devclaw-only recreate; OpenClaw untouched |

---

## 1. Prerequisites (once)

- The self-hosted `lifekit-vps` runner can push to ghcr: the `deploy.yml` job
  logs in with `GITHUB_TOKEN` (`packages: write`). Confirm the `lifekit-hq`
  packages allow the repo to publish.
- The env file the compose fragment + deploy script read
  (`DEVCLAW_ENV_FILE`, default `/srv/devclaw/.env`) exists and is
  **devclaw-owned** — reading another entity's env file (the old
  `/srv/lifekit-stack/.env` / `/srv/openclaw/config/.env` default) is the exact
  cross-entity coupling the ecosystem decoupling removes. It carries every var
  the fragment reads: the `LIFEKIT_*` host facts (docker GID, claude home,
  vault dir, workspaces dir — duplicated from the host on purpose; a little
  host-fact duplication is the price of entity independence), the operator-set
  `DEVCLAW_*` knobs, `DEVCLAW_TOKEN` for ops-agent, and on the cutover host
  `DEVCLAW_STATE_VOLUME=compose_devclaw-state`.
- The incident output dir exists (else ops-agent writes into a phantom mount):
  ```bash
  sudo mkdir -p /srv/memory/projects/ops-agent
  sudo chown 1000:1000 /srv/memory/projects/ops-agent
  ```

---

## 2. Cutover order (goal-safe)

Run on the VPS. Steps 1–3 do **not** touch the running instance.

**1. Build + push the images** (no live change yet). Trigger the `Deploy devclaw`
workflow with no tag, OR build manually:
```bash
cd /path/to/devclaw && SHA=$(git rev-parse HEAD)
docker build -f deploy/Dockerfile \
  --build-arg DEVCLAW_GIT_SHA="$SHA" \
  --build-arg DEVCLAW_BUILT_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  -t ghcr.io/lifekit-hq/devclaw-mcp:$SHA -t ghcr.io/lifekit-hq/devclaw-mcp:latest .
docker build -f .sandcastle/Dockerfile \
  -t ghcr.io/lifekit-hq/devclaw-sandbox:$SHA -t ghcr.io/lifekit-hq/devclaw-sandbox:latest .
docker push ghcr.io/lifekit-hq/devclaw-mcp:$SHA   # + :latest, + both sandbox tags
```

**2. Create the shared network** (empty, harmless):
```bash
docker network inspect lifekit-shared >/dev/null 2>&1 || docker network create lifekit-shared
```

**3. Adopt the existing state volume — the critical step.** The live volume is
`compose_devclaw-state` (owned by the shared `compose` project). Rather than
copy it, adopt it in place by pointing the external volume name at it. Add to
`/srv/devclaw/.env`:
```bash
DEVCLAW_STATE_VOLUME=compose_devclaw-state
```
Verify it exists and holds the DB:
```bash
docker volume inspect compose_devclaw-state >/dev/null && echo "volume OK"
```

**4. Stop devclaw in the OLD project, bring it up in the NEW one.** This is the
only moment the MCP server bounces (same as any redeploy — crash-recovery reaps
and resumes in-flight sandboxes; keep this off a live overnight run):
```bash
# stop just devclaw + ops-agent in the shared project (leaves gateway et al. up)
cd /srv/lifekit-stack/compose
docker compose -p compose stop devclaw-mcp ops-agent
docker compose -p compose rm -f devclaw-mcp ops-agent

# bring devclaw up as its own project (pulls the tag, recreates, health-gates)
cd /path/to/devclaw
bash deploy/deploy-devclaw.sh "$SHA"   # reads /srv/devclaw/.env by default
```

**5. Attach the OpenClaw seam services to the shared network** so the waiter
(in `openclaw-gateway`) can still reach `devclaw-mcp`, and devclaw can still
reach `notify-relay`. Apply the lifekit-stack companion change (§4 below) and
`docker compose -p compose up -d openclaw-gateway notify-relay` — this recreates
only those two, once.

**6. Verify** (see §3).

---

## 3. Verify

```bash
# devclaw-mcp answers /health with the NEW commit SHA
curl -fsS http://127.0.0.1:18791/health | grep -o '"git_sha":"[^"]*"'

# goals survived the cutover — count must match pre-cutover
#   (list_goals via the MCP, or inspect the DB)
# OpenClaw-side containers were NOT recreated — uptimes unbroken
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'gateway|dashboard|notify-relay|cli'

# the waiter can still reach devclaw over the network
docker exec compose-openclaw-gateway-1 curl -fsS http://devclaw-mcp:8000/health >/dev/null && echo "seam OK"
```

Success = new SHA on `/health`, goal count unchanged, OpenClaw uptimes unbroken.

---

## 4. lifekit-stack companion change

In the lifekit-stack repo (separate branch), the devclaw build is removed and
the image is referenced by tag (spec FR-007):

- **Delete** `compose/devclaw-mcp/Dockerfile` and `compose/devclaw-sandbox/Dockerfile`.
- **Remove** the `devclaw-mcp`, `devclaw-sandbox`, and `ops-agent` *run/build*
  stanzas from `compose/docker-compose.yml` — except keep `ops-agent` as a
  **build-only** service (so its image is still produced here; devclaw's project
  runs it).
- **Attach** `openclaw-gateway` + `notify-relay` to the external
  `lifekit-shared` network (in addition to their default).
- **Strip** the devclaw block from `scripts/deploy.sh`: the `--no-cache` rebuild
  of both images, the `docker tag devclaw-sandbox` step, and the entire
  md5-verify-against-GitHub stage.
- `redeploy-devclaw.sh` in lifekit-stack is superseded by
  devclaw's `deploy/deploy-devclaw.sh` — delete it.

After this, lifekit-stack contains no devclaw Dockerfile, no `git clone` of
devclaw, no devclaw `build:` stanza, and no md5-verify (SC-006).

---

## 5. Rollback

Images are tagged by commit SHA, so rolling back is a pull — no source revert:
```bash
bash deploy/deploy-devclaw.sh <prior-sha>
```

---

## 6. Cold first-deploy (fresh host, no prior state)

Same as §2 but skip the adopt step — there is no volume to preserve. Let the
deploy script create the empty volume explicitly:
```bash
docker network create lifekit-shared
DEVCLAW_ALLOW_VOLUME_CREATE=1 bash deploy/deploy-devclaw.sh
```
