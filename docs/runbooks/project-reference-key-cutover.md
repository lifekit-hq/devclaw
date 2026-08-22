# Runbook — deploying the `project_id` dispatch cutover (#520)

**Currency:** accurate as of 2026-08-22 (P1 #522 · P2 #525 · P3 #528 shipped; cutover deployed + verified live. #616 gave the P3 backfill a one-shot marker — step 4 updated).

Spec `003-project-reference-key` made the registry the single source of truth for
dispatch, as a **hard cutover** (P1, clarify decision 2): `main` rejects a raw
`workspace_dir` and requires a registered `project_id`. This runbook is the
coordination checklist so a devclaw redeploy doesn't break dispatch on the VPS,
and a record of how the first cutover deploy actually went.

## How devclaw is deployed (ground truth, 2026-08-14)

- The MCP runs as the docker-compose service **`compose-devclaw-mcp-1`**, image
  `devclaw-mcp:local`, from `/srv/lifekit-stack/compose` (GitOps repo
  `dsdevq/lifekit-stack`).
- The image is built by `compose/devclaw-mcp/Dockerfile`, which does
  `git clone --depth 1 --branch main https://github.com/lifekit-hq/devclaw.git /app`
  — **a rebuild pulls the latest `main`**, so shipping = rebuild + recreate.
- A **self-hosted GitHub Actions runner** (`dsdevq-devclaw.lifekit-vps-devclaw`)
  auto-builds + redeploys on merge to `main`. In practice the P1–P3 cutover was
  deployed by CI within ~minutes of the last merge; a manual redeploy is the
  fallback, not the default.
- **State persists across recreate:** `devclaw.db` and `workspaces/` are
  volume-mounted at container `/var/lib/devclaw/…`. So the **run-window schedule
  survives a redeploy** (it is stored in the DB, not the image) — the older
  "re-arm the window after every deploy" note no longer holds.

## Pre-deploy (prep)

1. **Register every project you dispatch to.** Raw paths are gone; the tools need
   a registered `project_id` with a correct `repoUrl` + container-side
   `workspaceDir`. `list_projects` and eyeball each. A missing workspace is fine
   — P2 auto-clones it from `repoUrl` on first dispatch.
2. **Point each project's `repoUrl` at the canonical repo.** The clone's git
   `origin` is what delivery pushes to — a stale `repoUrl` *or* a stale clone
   remote sends PRs to the wrong repo (the 2026-08-14 finance-sentry incident).
   Fix a live clone's remote in the container (volume-mounted, persists):
   ```bash
   ssh lifekit-vps 'docker exec compose-devclaw-mcp-1 \
     git -C /var/lib/devclaw/workspaces/<project> remote set-url origin <canonical-url>'
   ```

## Deploy

3. Merge to `main`; CI rebuilds `devclaw-mcp:local` from `main` and recreates the
   container. Confirm it landed:
   ```bash
   ssh lifekit-vps 'docker image inspect devclaw-mcp:local --format created={{.Created}}; \
     docker ps --filter name=devclaw-mcp --format "{{.Status}}"'
   # image "created" should post-date your merge; container "Up <seconds>" on it
   ```
   Manual fallback (only if CI didn't fire): rebuild + recreate from
   `/srv/lifekit-stack/compose` (`docker compose build --no-cache devclaw-mcp &&
   docker compose up -d devclaw-mcp`; the Dockerfile has a `CACHEBUST` arg).
4. **The P3 backfill runs automatically at startup, ONCE** (`lifecycle.py`
   after `recover()`), stamping `project_id` onto goals whose workspace matches
   a registered project. It logs `backfilled project_id on N goal(s)` only when
   `N>0`. Since #616 it is marker-guarded in the `meta` table
   (`goal_project_id_backfill_done_at_ms`, see
   `devclaw/goal/project_id_cutoff.py`): the first boot after that deploy runs
   the scan, stamps the marker, and no later boot rescans. A goal created after
   that point gets its `project_id` at creation, not from a backfill. Verify the
   code + the marker:
   ```bash
   ssh lifekit-vps 'docker exec compose-devclaw-mcp-1 \
     grep -c "def backfill_project_ids" /app/devclaw/goal/service.py'   # → 1
   ssh lifekit-vps 'docker exec compose-devclaw-mcp-1 sqlite3 \
     /var/lib/devclaw/devclaw.db \
     "select value from meta where key='"'"'goal_project_id_backfill_done_at_ms'"'"'"'
   # → an epoch-ms stamp once the first post-#616 boot has completed
   ```

## Post-deploy (verify)

5. **Prove the contract is live** — an unknown project must reject synchronously
   (this fires at the tool layer, before the run-window gate):
   ```
   dispatch_task(kind="fix_bug", project_id="__nope__", goal="smoke")
   → ToolError: unknown project_id: '__nope__' — register it first
   ```
6. **Run window** — confirm it's still armed (it persists; no re-arm needed):
   `get_run_schedule()` → your window, `dispatch_open` gated only by the clock.
7. **Backfill outcome** — a goal whose `workspace_dir` does NOT match its
   project's registered `workspaceDir` will NOT be stamped (it wasn't associated
   before either — the backfill only adds, never regresses). If a long-lived
   goal *should* carry a project's knobs but its workspace drifted from the
   registry, that is pre-existing registry drift to fix by hand (align the
   project's `workspaceDir`, or re-file the goal), not a deploy failure.

## Waiter (OpenClaw) — the lockstep

8. The waiter (`compose-openclaw-*`, config `/srv/openclaw/config/agents/devclaw/`)
   forms tool calls from the **MCP tool schema**, which the redeployed server now
   publishes with `project_id` required and no `workspace_dir`. So a
   schema-following waiter passes `project_id` automatically — **no prompt edit is
   required** as long as the waiter prompt names tools (`devclaw__dispatch_task`)
   rather than hardcoding a `workspace_dir=` example. Grep the waiter prompt for a
   stale `workspace_dir` dispatch example if in doubt; session-log hits don't
   count (they're history).

## What actually happened (first cutover, 2026-08-14)

- CI auto-built `devclaw-mcp:local` from `main` at 14:31 UTC and recreated the
  container on it — devclaw was already running P1+P2+P3 before any manual step.
- Run window survived (22:00–05:00 Europe/London, DB-persisted).
- Backfill stamped `ledger-2026-08-10 → project_id: ledger` (the one
  workspace-matching goal); everything else correctly unchanged.
- finance-sentry: `repoUrl` corrected to `lifekit-hq` (persisted the recreate);
  its clone `origin` re-pointed to `lifekit-hq` in the container.
- Smoke test: unknown-project dispatch rejected synchronously. Contract live.
- Pre-existing drift noted: the `ledger` project's registered `workspaceDir`
  (`…/ledger-2026-08-10`) does not match the live `ledger-2026-08-12` goal's
  workspace, so that goal is unassociated (was already, pre-P3).
