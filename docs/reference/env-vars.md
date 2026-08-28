# DevClaw environment variables

Single source of truth for every env var the runtime reads — enforced by
`tests/test_env_vars_doc_sync.py` (a var read in code but missing here, or
documented here but read nowhere, fails the suite). The CODE home is
`devclaw/config.py`: the one module allowed to read `DEVCLAW_*` vars
(`tests/test_config_single_doorway.py` enforces the boundary; the in-sandbox
`runner/` reads its own env by design). Grouped by what each one
controls. Set in `.env` (devclaw loads it on startup), the systemd unit, the
compose file, or the shell — devclaw doesn't care.

**What earns a row here:** facts that genuinely differ per host (paths, ports,
binaries, images, capacity), operator cost/behavior levers, and migration
flags. Internal tuning (protocol timeouts, retry buffer sizes, breaker
thresholds) is **code constants, changed by PR** — that's how every such value
has actually been tuned in this repo's history. If you're looking for a knob
that used to be here (per-role `*_MODEL` vars, `*_TIMEOUT_MS`,
`DEVCLAW_RATE_LIMIT_*`, `DEVCLAW_WORKSPACE_BREAK_*`, per-flag env defaults for
project-overridable behavior), it's now a named constant next to its use site.

**Convention:** empty string or unset = "use the default in the table." Bools
are truthy unless explicitly `0` / `false`. **Real env vars always win** over
`.env` — `.env` is the per-machine default surface, not an override.

The committed [`.env.example`](../../.env.example) lists every var with its
default; copy it to `.env` and uncomment what you want to change.

## Server transport + auth

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_TRANSPORT` | `stdio` | `stdio` (local dev / tests) or `http` (long-running service) |
| `DEVCLAW_PORT` | `8000` | HTTP port when `DEVCLAW_TRANSPORT=http` |
| `DEVCLAW_HOST` | `0.0.0.0` | HTTP bind address. Set `127.0.0.1` to restrict to loopback. |
| `DEVCLAW_TOKEN` | — | Bearer-token gate for every HTTP route except `/health`. Sent as `Authorization: Bearer <token>` (MCP clients) or `?token=` (dashboard/SSE). Unset = no auth (local dev). |
| `DEVCLAW_DOTENV` | `.env` (repo cwd) | Path of the `.env` file loaded at startup. Must be set in the shell to bootstrap (it can't live in the file it locates). |
| `DEVCLAW_GIT_SHA` | — | Deployed git commit, baked into the image at build time (the deploy script already computes it for CACHEBUST). Surfaced on `/health` + `/node.json` (#494); unset ⇒ `null`, never guessed. |
| `DEVCLAW_BUILT_AT` | — | Image build timestamp (ISO-8601), same provenance and surfaces as `DEVCLAW_GIT_SHA`. |

## State + concurrency

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_DB` | `./devclaw.db` | SQLite path. Holds the task-queue tables (programs, tasks, events) AND, since Tranche 1, the goal-state tables (`goal_status`, `goal_steering`, `goal_log`, `goal_deliveries`, `goal_docs`, `goal_phase_history`) — `GoalStore` is wired onto this same `StateStore` in production. |
| `DEVCLAW_TICK_SECONDS` | `10` | Task-queue heartbeat interval. Advances DAGs + resumes recovered work. |
| `DEVCLAW_MAX_CONCURRENT` | `4` | Global cap on concurrently-running tasks — size to the host. |
| `DEVCLAW_MAX_CONCURRENT_PER_PROGRAM` | `2` | Per-program concurrency cap. Also the ceiling on a goal's planned fan-out: the number of `[P]` lanes dispatched at once is `min(what the plan declared, this, `DEVCLAW_MAX_CONCURRENT`)` — spec 010 FR-105, the plan and the host's caps decide the degree, nothing else. |
| `DEVCLAW_FANOUT` | unset (**off**) | Planned fan-out (spec 010 US3): when set (`1`/`true`/`yes`/`on`), a goal whose task graph declares consecutive `[P]` tasks with disjoint declared file scopes dispatches them as concurrent lanes that integrate serially onto the goal branch. Off by default: US3 is the spec's earned exception, and two lanes are two sandboxes against one OAuth account. With it off — and with it on for a plan that declares no `[P]` scopes — dispatch is unchanged. |
| `DEVCLAW_MAX_HOST_COGNITION` | `2` | Cap on concurrent **host-side** `claude --print` cognition subprocesses (review gate, evaluator, planner, done-gate) — an `asyncio.Semaphore` at the spawn chokepoint in `llm_call.py`. These processes are invisible to `DEVCLAW_MAX_CONCURRENT` (which counts sandboxed tasks); unbounded, 4 review gates + goal cognition ran at once and the kernel OOM-killed them (`exited -9`). Queued calls just wait (the per-call cognition timeout starts after the wait, not during). Invalid / `<1` / unset → 2 (`0` is not honored — it would deadlock every call). |
| `DEVCLAW_MAX_RETRIES` | `1` | Re-runs of a gate-failing task before escalation. Each retry feeds the failure back as steering. Timeouts are never retried. `0` disables. |
| `DEVCLAW_TASK_TIMEOUT_S` | `3600` | Per-task wall-clock cap. Exceeded → cancelled, sandbox torn down, task `failed`. `<=0` disables. |
| `DEVCLAW_VERIFY_TIMEOUT_S` | `900` | Wall-clock cap for the `verify_cmd` step (the gate that runs after the agent finishes). |
| `DEVCLAW_VALIDATION_STEP_TIMEOUT_S` | `900` | Per-step wall-clock cap for a `validate_product` run's boot and suites commands (spec 015). A cut suite is reported as explicit partial coverage. Read in the sandbox by the runner. |
| `DEVCLAW_TRACE_RETENTION_DAYS` | `30` | Days of `traces`-table telemetry to keep. The goal heartbeat prunes older rows once a day on its cheap path (batched DELETEs, pure SQLite, zero LLM). `0`, a negative value, or an unparseable value disables pruning gracefully. |
| `DEVCLAW_EVENTS_RETENTION_DAYS` | `30` | Days of `events`-table history to keep (raw runner SDK events, one row per agent action — the highest-volume append-only log after traces). Pruned by the same daily, batched, pure-SQLite, zero-LLM heartbeat pass as the trace prune, on an independent watermark. `0`, a negative value, or an unparseable value disables pruning gracefully. |
| `DEVCLAW_WORKSPACE_RETENTION_DAYS` | `3` | Days to keep the workspace of a goal-scoped checkout after the goal ended CLEANLY. Short on purpose: the work is on a branch and nobody inspects the checkout. Swept by the same daily, batched, zero-LLM heartbeat pass as the trace prune, on its own watermark. Never applies to a REGISTERED project's workspace (that is released by `delete_project`) and never to a workspace with a non-terminal goal or a running task. `0`/negative/unparseable disables gracefully. |
| `DEVCLAW_WORKSPACE_RETENTION_DAYS_FAILED` | `14` | Same sweep, for a goal that ended BADLY (`off_track`/`stalled` direction, or a block never cleared). Long on purpose: the checkout is the only forensics that exists for a run that went wrong. |
| `DEVCLAW_DB_SIZE_ALERT_MB` | `2000` | Size (MB) at which the heartbeat pings the owner ONCE that `devclaw.db` (incl. the WAL sidecar) has grown too large despite retention+VACUUM — the loud-not-silent guard against a silent disk-fill wedge. Re-arms when size drops back under. `0`, a negative value, or an unparseable value disables the alarm gracefully. |

## Engine selection

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_ENGINE` | *(unset)* | `(unset)` → the worker runner in a per-task docker sandbox (production). `host` → the runner on the host with **no** sandbox (dev/CI, agent has full FS access). `stub` → deterministic stub (harness validation, no docker, no claude). |
| `DEVCLAW_COGNITION` | `claude` | Which `Cognition` impl every role's `default_caller` routes through. `claude` → `claude --print` over Pro/Max OAuth (production). `stub` → deterministic canned responses (offline harnesses + eval scaffolding). `agent_sdk` → **OPT-IN** streaming backend over `claude-agent-sdk.query()` (same Pro/Max OAuth session, native liveness + structured usage/rate-limit events; requires the optional `agent-sdk` extra — `pip install -e ".[agent-sdk]"`). **Not yet live-shaken.** Unknown values fail loud at first use. |
| `DEVCLAW_COGNITION_TIMEOUT_S` | `180` | Default budget (seconds) for one cognition call, read by both backends. `claude`: wall-clock cap on one `claude --print` call (`PLANNER_TIMEOUT_MS` in `call_claude`) — a peak-hour latency lever (successful calls measured 50–78s while the old hardcoded 90s cap timed five out at exactly the cap, 2026-07-14/15). `agent_sdk`: inactivity window — each yielded message resets it; a stall closes the stream (killing the spawned `claude`) and raises a timed-out `PlannerError`. Roles with their own budget (review 180s, decomposer 300s) are unaffected; a per-call `timeout_ms` overrides it. Invalid / `<=0` / unset → 180. |
| `DEVCLAW_COGNITION_RETRIES` | `2` | Bounded retries for a **TRANSIENT** `claude --print` failure in `call_claude` (timeout, signal-death `exited -9` OOM-kill under host memory pressure, `overloaded`/network blip). Short geometric backoff (5s, 20s, …, capped 60s; a provider-stated retry-after hint wins). Only the TRANSIENT class retries — QUOTA/RATE/AUTH re-raise on the first failure so the pause-and-resume machinery fires, and a REAL bug fails fast. Exhausted retries re-raise the last error (fail-closed intact). `0` disables retries; invalid / negative / unset → 2. The clean-run fix (2026-07-30). |

## Model tiering (cognition cost lever)

Cognition cost is steered by **three tiers**, not per-role vars. Which role
runs at which tier is a code decision — the table in
[`devclaw/model_tiers.py`](../../devclaw/model_tiers.py) — changed by PR (the
twelve per-role vars this replaced were never once set on any host). Tier
values are `claude --model` inputs: an alias (`haiku`/`sonnet`/`opus`) or a
full id. Empty → account default. **No API key = the constraint is your
session quota, not a bill.**

| Var | Default | Runs |
|---|---|---|
| `DEVCLAW_MODEL_DEEP` | `opus` | Rare, high-leverage calls: firming, decomposer (the one planning spine — goals AND programs), world-research. |
| `DEVCLAW_MODEL_STANDARD` | `sonnet` | Judgment at volume: direction evaluator, scope grill, review gate, trend classification. |
| `DEVCLAW_MODEL_LIGHT` | `haiku` | Mechanical prose: per-delivery summaries, failure-analysis judge. |
| `DEVCLAW_EXEC_MODEL` | `claude-sonnet-4-6` | **The in-sandbox coding agent — the token/quota bulk.** Full id, not alias. Set `claude-opus-4-8` to opt a run up to Opus. Empty → ACP server's default. |
| `DEVCLAW_ACP_COMMAND` | *(unset)* → `claude-agent-acp` | **The ACP agent command the worker session runs on** — the layer-5 replaceability seam. A string like `my-acp --profile x`; the runner shlex-splits it. Read host-side and threaded via the runner JSON payload (host env does NOT cross the container boundary; the runner's own env read only serves manual `docker run` / host-engine runs). Scope caveat: this swaps the *command only* — the `acp_env` (CLAUDE_* vars), the `~/.claude` auth mounts, `DEVCLAW_EXEC_MODEL`'s claude model ids, and the auth/rate-limit classifiers are still claude-shaped, and the alternate binary must be baked into the sandbox image. |
| `DEVCLAW_ACP_IDLE_TIMEOUT_S` | `1800` | Runner-side hang brake: seconds of TOTAL agent silence (no protocol frames, no stderr) before the runner kills the agent subprocess and fails the task with a legible reason. Generous on purpose — one quiet long-running tool call (a full test suite) is legitimate silence; the container-level timeout stays the hard backstop. Read in-sandbox by the runner's ACP client. |

## Sandbox (auth + resources)

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_SANDBOX_IMAGE` | `devclaw-sandbox:latest` | Per-task sandbox image (built from `.sandcastle/Dockerfile`). A project can pin its own via the registry's per-project `sandbox_image` override (ADR 0005 escape hatch/migration bridge), which beats this default for that project's tasks. |
| `DEVCLAW_DOCKER_BIN` | `docker` | docker binary to spawn. |
| `DEVCLAW_SANDBOX_MEMORY` | `2g` | Hard per-container memory ceiling. `--memory-swap == --memory` disables swap growth. Also DECLARED into the sandbox env verbatim (spec 020) so the worker can bound its tooling — `/proc/meminfo` inside the cgroup reports the host. A project registry `sandbox_memory` override (spec 020 US4) beats this default for that project's tasks and is what launch admission accounts. |
| `DEVCLAW_COGNITION_MEM_RESERVE` | `1536m` | Host RAM kept free for the host-side `claude --print` cognition + OS when admitting sandbox launches. Dispatch defers a launch when `/proc/meminfo` MemAvailable would drop below `DEVCLAW_SANDBOX_MEMORY + this` — so N containers can't overcommit the box and get the host `claude` OOM-killed (`exited -9`). Sandbox engine only; fail-open if MemAvailable is unreadable. Size to the host. |
| `DEVCLAW_CONTEXT_TRIPWIRE_PCT` | `75` | Worker context-usage percentage at which the in-sandbox runner ends the turn and lands a coherent partial increment instead of overflowing the model context (spec 021 US2). `0` disables. Declared into the sandbox env by the engine; firings surface as a `ContextTripwire` event + a `limit|context_tripwire` problems-catalog row — the SC-005 ratchet metric. |
| `DEVCLAW_SANDBOX_CPUS` | `2.0` | Per-container CPU limit. Declared into the sandbox env verbatim (spec 020; `nproc` reports the host); per-project `sandbox_cpus` override beats it. |
| `DEVCLAW_HOST_CLAUDE_DIR` | `~/.claude` | Host path bind-mounted read-only into each sandbox. |
| `DEVCLAW_SANDBOX_CLAUDE_ALLOWLIST` | `.credentials.json,.claude.json` | Comma-separated entries **under** `~/.claude` to bind in. Default = the OAuth identity pair (token + identity — both needed for the ACP agentic loop). Add more only with intent; missing entries surface as docker bind errors, not silent skips. |
| `DEVCLAW_CONTAINER_PATH_PREFIX` | — | When devclaw itself runs in a container, the workspace path the host sees ≠ what devclaw sees. Set this to devclaw's view; pair with `DEVCLAW_HOST_PATH_PREFIX`. |
| `DEVCLAW_HOST_PATH_PREFIX` | — | The host-side prefix that swaps in for `DEVCLAW_CONTAINER_PATH_PREFIX` when invoking `docker run`. |
| `DEVCLAW_RUNNER_PY` | `runner/runner.py` (resolved against repo) | Worker runner script path (host engine mode). |
| `DEVCLAW_RUNNER_PYTHON` | derived | Python interpreter the host engine spawns the runner with. |
| `DEVCLAW_SKILLS_DIR` | `/opt/devclaw/skills` in the sandbox; `runner/skills/` under the host engine | The ONE home for worker-kind instructions, read by `runner/runner.py` and prepended per task kind. The sandbox image bakes `runner/skills/` to the default path; the host engine points here at the in-repo source. No bundle ⇒ the runner raises rather than briefing the worker on substitute text (#613). |
| `DEVCLAW_HOOKS_DIR` | `/opt/devclaw/hooks` | (In-sandbox.) Universal pre/post hook `.sh` files, run alongside any per-repo `.agent/hooks/`. |
| `DEVCLAW_GIT_NAME` | `devclaw` | Git author/committer **name** for every commit devclaw produces (worker in sandbox + host engine via `GIT_AUTHOR_*`/`GIT_COMMITTER_*` env, delivery's own commit via `-c`). Env beats every git config level, so ambient/leaked identities can't author devclaw's commits. The worker's `Co-Authored-By: Claude …` trailer is unaffected. |
| `DEVCLAW_GIT_EMAIL` | `devclaw@local` | Git author/committer **email** for the same. Point at a machine account's GitHub noreply address (`<id>+<user>@users.noreply.github.com`) to link commits to a real GitHub profile. |

## Auth (Pro OAuth posture)

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_CLAUDE_BIN` | `claude` | The `claude` binary the planner / Claude-SDK engine drives. |
| `DEVCLAW_TAILSCALE_BIN` | `tailscale` | Tailscale CLI used by `deploy.py` for `tailscale serve`. |
| `CLAUDE_CODE_OAUTH_TOKEN` | *(unset)* | A long-lived (~1 year) subscription OAuth token from `claude setup-token` — **the recommended production credential**. Still Pro/Max OAuth, never metered: it is the same subscription, supplied as a token instead of an interactive login. Claude Code ranks it ABOVE the `/login` credential, so setting it stops the box depending on `~/.claude/.credentials.json`, whose revocation by an interactive login elsewhere on the same account is what takes an unattended instance down mid-run. Read by host cognition from the process env, and forwarded into each sandbox as the one auth var that crosses the container boundary (`engine/sandcastle.py`). Unset/blank ⇒ unchanged behavior: the mounted identity pair stays the only auth path. **Its home is the repo's `CLAUDE_CODE_OAUTH_TOKEN` Actions secret**, injected into the deploy step by `.github/workflows/deploy.yml`; compose resolves the shell environment in preference to `--env-file`, so it needs no line in `/srv/devclaw/.env` and nothing is written to disk on the VPS. A line in the env file still works as an operator fallback. Absent from both, `deploy-devclaw.sh` warns loudly and the instance runs on the mounted `~/.claude` login. Never commit the value. |
| `NODE_AUTH_TOKEN` | *(unset)* | A **`read:packages`-scoped** GitHub token forwarded into each sandbox (`engine/sandcastle.py`) so `npm ci` on an `@lifekit-hq`-consuming repo (`.npmrc`: `//npm.pkg.github.com/:_authToken=${NODE_AUTH_TOKEN}`) can resolve GitHub Packages — without it no real frontend build, and so no real-app e2e evidence, is possible in-sandbox. Read-only by scope: the sandbox still holds no credential that can push, merge, or touch issues/PRs; delivery ceremony stays host-side. Same home and contract as `CLAUDE_CODE_OAUTH_TOKEN`: the repo's `NODE_AUTH_TOKEN` Actions secret, injected into the deploy step by `.github/workflows/deploy.yml`, resolved by compose from the shell env — nothing written to disk on the VPS; an env-file line is the operator fallback. Unset/blank ⇒ no forward at all, behavior byte-identical. Never commit the value. |
| `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` | — | **Actively refused.** The sandbox runner strips these from the env before spawning the container. Set anywhere = no effect; the design pillar is Pro/Max OAuth, not metered billing. |
| `GITHUB_TOKEN` / `GH_TOKEN` | — | Repo push + PR access for `open_pr` delivery (or use a logged-in `gh`). Git access only — not cognition billing. |
| `DEVCLAW_GITHUB_OWNER` | — | GitHub account/org `create_repo`/`delete_repo` operate under (falls back to `gh`'s active login). |

## Goal layer

Behavior that a **project** can own is not env anymore: `review_gate`,
`verify_done`, `autodeploy` and the CI-gate
stance resolve as *code default → project-registry override* (set via
`register_project` / `update_project`). The env middle-layer was removed —
three precedence layers with divergent defaults was a debugging trap.
`autodeploy`'s code default is **conditional** (#554): on goal completion a
preview deploy runs only if the workspace has an app surface the preview
launcher can actually serve (`delivery.deploy.workspace_has_app_surface`) — a
pure library gets no preview container unless its project pins `autodeploy=on`.

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_GOALS_DIR` | `~/memory/goals` | Root holding one folder per durable goal. `goal.yaml` (facts) and `spec.md` are plain files; `STATUS.md` / `log.md` / `inbox.md` / `deliveries.md` are generated **views** over the SQLite goal-state tables (`DEVCLAW_DB`) — human-readable, never read back for decisions. |
| `DEVCLAW_GOAL_TICK_SECONDS` | `900` | Goal heartbeat interval. Also poked in-process the moment a task settles. |
| `DEVCLAW_GOAL_TEXT_BUDGET` | `1000` | Max free-text characters on a REFERENCED goal (spec 019): the spec lives in the graded issue, the goal is a pointer. Hard doorway refusal, no override; issue-less goals exempt. |
| `DEVCLAW_RATCHET_FIRST_PASS` | `0.70` | Autonomy-ratchet threshold (spec 018): per-goal first-pass rate the scorecard grades against. Informational — the spec 007 flip stays manual. |
| `DEVCLAW_RATCHET_DECIDED_MERGE` | `0.80` | Autonomy-ratchet threshold: decided-PR merge rate (merged / merged+rejected, bench excluded). |
| `DEVCLAW_RATCHET_WINDOW_DAYS` | `14` | Rolling window the ratchet metrics and the wedge-free-cycles condition are judged over; also the scorecard's default display window. |
| `DEVCLAW_DONEGATE_LEAN` | `0` | De-fat the done-gate evaluator prompt (structural-root-2026-08-05): at the done-gate, omit the two re-fed diary blocks — the `## Recent event log` and the `## What has actually shipped` delivery record (each cap 24K, ~half the 105K prompt that OOMs/times out). The gate then judges every `done_when` clause from the fresh read-only repo review + repository-context snapshot — the repo END STATE, not the journey. Fails toward CLOSED (the repo review is still required for every clause; only non-evidence claim/history context is dropped). Default OFF; flip per box after the prompt-anatomy view (PR #467) confirms the diary is dead weight. Only affects the done-gate — the on-demand `evaluate_goal` direction check keeps its history. |
| `DEVCLAW_GOAL_REMOTE_CHECKS` | `1` | Ops kill-switch: whether the done-gate also queries the repo's remote CI (GitHub Actions) state. `0` disables — the internal verify gate is the only check. |
| `DEVCLAW_GOAL_BROWSER_GATE` | `1` | Whether the settle path enforces the browser-E2E gate: a change touching a web-UI path must carry a passing real-browser Playwright run (proven via the runner's `browser_report` counts) before it ships. `0` disables — UI changes are gated by verify + review only (the pre-2026-07-17 behaviour). The gate *stance* (`flexible`/`strict`) and the reachability escape valve are no longer env-tuned: stance is `task_queue.BROWSER_GATE_MODE` (fleet default `flexible`, per-project overridable via the registry's `browser_gate_mode`); the reachability valve is always on (strictly safe — can only relax a would-be block). |
| `DEVCLAW_GOAL_NO_PROGRESS_S` | `21600` | Wall-clock seconds an executing goal may go without a delivery before the watchdog pings the owner once. Zero-token check; complements the per-task timeout. `0` disables. |
| `DEVCLAW_GOAL_NOTIFY_URL` | — | Notify-relay endpoint for goal-level Telegram messages (free-text `/text` passthrough). |
| `DEVCLAW_GOAL_PLAIN_SUMMARY` | `1` | One-line plain-prose summary per delivery for `deliveries.md` (quota lever — one `claude` call per delivery). |
| `DEVCLAW_NOTIFY_ALTITUDE` | `owner` | Floor for goal-layer notifications: `owner` (only real blockers / direction questions / completions) or `task` (also includes per-task chatter). |
| `DEVCLAW_RUN_CYCLE_START` | `22:00` | Open time (`HH:MM`, in `DEVCLAW_RUN_CYCLE_TZ`) of the recurring run cycle (nightly by default) whose close fires the continuous-eval **cycle report** (ADR 0006): a mechanical, zero-LLM heartbeat edge that pushes the cycle's clean/wedge/pause slice through the notifier once per cycle. |
| `DEVCLAW_RUN_CYCLE_END` | `05:00` | Close time (`HH:MM`) of the run cycle — the heartbeat emits the report on the first wakeup after this instant (once per `cycle_key`; the `cycle_reports` PK dedupes). |
| `DEVCLAW_RUN_CYCLE_TZ` | `Europe/London` | Timezone the run-cycle `start`/`end` are read in, and the tz of the report's `cycle_key`. An unresolvable tz fails safe — the report is skipped, never crashes the heartbeat. |
| `DEVCLAW_SELF_REPO` | *(unset)* | `owner/name` of the repo devclaw files its own recurring problems against — **itself** (self-issue-filing Stage 1, `docs/proposals/self-issue-filing.md`). Fires on the same once-per-cycle report edge (zero LLM). **Unset = the whole feature is off** — no GitHub call, nothing shelled (the default + every test path). Issue creation uses a `GITHUB_TOKEN`/`GH_TOKEN` credential, never `ANTHROPIC_*` (OAuth-only cognition untouched). |
| `DEVCLAW_SELF_ISSUE_MIN_CYCLES` | `2` | Distinct run-cycles a problem must survive (with ≥1 terminal occurrence) before it earns a self-filed issue — the recurrence gate (rescues the ops-agent O4 trend-repeat threshold). A one-night burst is one cycle; two cycles running is a real, file-worthy problem (a 3-cycle bar proved unreachable — the session-led fix loop repairs recurrences in ~a day). |
| `DEVCLAW_SELF_ISSUE_QUIET_DAYS` | `3` | Quiet span (≈ cycle-spans, cycles being ~daily) after which an OPEN self-filed issue auto-closes as stale — the age-out exit that keeps the board from accumulating (backlog #259). It reopens automatically if the problem recurs. |
| `DEVCLAW_SELF_ISSUE_MAX_PER_CYCLE` | `3` | Cap on NEW self-filed issues opened per cycle (noise budget). Reopens and closes are not capped (not new noise); anything suppressed over the cap is **named** in the cycle-report line, never silently dropped. |
| `DEVCLAW_SELF_FIX_CONCURRENCY` | `1` | How many self-fix goals may be in flight at once (self-issue-filing **Stage 2 / P2 — FIX pickup**). At the same once-per-cycle edge, a human-`accepted` + `devclaw:self-filed` issue is picked up as ONE `one_shot` self-fix goal that opens a PR for **human** review (no auto-merge — the tiered classifier is deferred to P2.1/P2.2). `1` serialises self-modification: parallel self-fixes multiply the self-brick surface and muddy failure attribution. Gated by `DEVCLAW_SELF_REPO` (unset ⇒ no pickup, nothing shelled). |

## Deploy hosting

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_DEPLOY_IMAGE` | falls back to `DEVCLAW_SANDBOX_IMAGE` | Image used for durable deploys. |
| `DEVCLAW_DEPLOY_PORT_BASE` | `8200` | Lower bound of the per-slug deterministic deploy port range. |
| `DEVCLAW_DEPLOY_PORT_SPAN` | `200` | Number of slots in the deploy port range (so `8200`–`8399` by default). |
| `DEVCLAW_DEPLOY_MEMORY` | `512m` | Per-deploy memory ceiling. |
| `DEVCLAW_DEPLOY_CPUS` | `1.0` | Per-deploy CPU limit. |
| `DEVCLAW_DEPLOY_MAX` | `5` | Max concurrent durable deploys on the VPS. |

## Trend detection (self-observation)

Detects recurring failure/friction patterns across goals (e.g. the same class of
steer landing repeatedly) and writes them to the owner's vault for review — a
zero-token-by-default background signal, not a cognition role.

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_TREND_ENABLED` | `1` | Master switch for trend detection. `0` disables entirely. |
| `DEVCLAW_TREND_DISABLE` | — | Comma-separated signal ids to mute individually (e.g. `R2,H4`) while a signal is being calibrated, without disabling the rest. |
| `DEVCLAW_TREND_HARNESS_SELF_FILE` | `~/memory/projects/devclaw/trends.md` | Where detected trends are appended for Denys to review. |

## What's NOT here on purpose

- The waiter agent's env (model, profile, allowed tools) lives in OpenClaw's `openclaw.json` on the VPS — not this repo. See [runbooks/vps-waiter-deploy.md](../runbooks/vps-waiter-deploy.md).
- Per-project verify commands and goal `done_when` strings are runtime arguments to MCP tools, not env. They belong with the project, not the host.
- The eval-harness `MEASURE_*` vars (see `evals/measure_passrate.py`) and the test-suite gates (`DEVCLAW_RUN_COGNITION_EVALS`, `DEVCLAW_TEST_*`) — offline tooling, not the runtime.
- Internal tuning constants (timeouts, retry buffers, breaker thresholds, review diff caps) — named constants at their use sites, tuned by PR.
