# DevClaw environment variables

Single source of truth for every env var the runtime reads — enforced by
`tests/test_env_vars_doc_sync.py` (a var read in code but missing here, or
documented here but read nowhere, fails the suite). The CODE home is
`devclaw/config.py`, the one module allowed to read `DEVCLAW_*` vars
(`tests/test_config_single_doorway.py`); the in-sandbox `runner/` reads its
own env by design. Empty or unset means "the default in the table". Real env
vars always win over `.env`.

## Server transport + auth

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_TRANSPORT` | `stdio` | `stdio` (local dev / tests) or `http` (the long-running service) |
| `DEVCLAW_PORT` | `8000` | HTTP port when `DEVCLAW_TRANSPORT=http` |
| `DEVCLAW_HOST` | `0.0.0.0` | HTTP bind address; `127.0.0.1` restricts to loopback |
| `DEVCLAW_TOKEN` | — | Bearer token for every HTTP route except `/health` (`Authorization: Bearer` or `?token=`). Unset = no auth. |
| `DEVCLAW_DOTENV` | `.env` (cwd) | Path of the `.env` file loaded at startup |
| `DEVCLAW_GIT_SHA` | — | Deployed commit, baked at build time; shown on `/health` |
| `DEVCLAW_BUILT_AT` | — | Image build timestamp, same provenance |
| `DEVCLAW_WEBHOOK_SECRET` | — | HMAC secret for `POST /webhooks/github`; unset ⇒ the route answers 404. A verified delivery wakes the goal tick early. |

## Storage

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_DB` | `./devclaw.db` | The SQLite home: tasks, events, goals, decisions, control flags |
| `DEVCLAW_EVENTS_RETENTION_DAYS` | `30` | Days of session events kept; `0` disables the prune |

## Engine + sandbox

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_ENGINE` | *(unset)* | *(unset)* = the worker in a per-session docker sandbox (production); `host` = on the host, no sandbox (dev); `stub` = deterministic, no docker, no claude (the suite) |
| `DEVCLAW_SANDBOX_IMAGE` | `devclaw-sandbox:latest` | The sandbox image (`.sandcastle/Dockerfile`) |
| `DEVCLAW_DOCKER_BIN` | `docker` | The docker binary |
| `DEVCLAW_EXEC_MODEL` | `claude-sonnet-4-6` | The model the in-sandbox worker runs; `""` = the agent's own default |
| `DEVCLAW_ACP_COMMAND` | `claude-agent-acp` | The ACP agent the runner spawns — the one seam that swaps the agent (spec 011) |
| `DEVCLAW_SANDBOX_MEMORY` | `2g` | Per-sandbox memory ceiling (a project may override it in the registry) |
| `DEVCLAW_SANDBOX_CPUS` | `2.0` | Per-sandbox CPU cap |
| `DEVCLAW_SANDBOX_CLAUDE_ALLOWLIST` | `.credentials.json,.claude.json` | Files from `~/.claude` mounted into the sandbox |
| `DEVCLAW_HOST_CLAUDE_DIR` | `~/.claude` | Host path of the claude config to mount |
| `DEVCLAW_CONTAINER_PATH_PREFIX` | — | Container-side workspace prefix for host↔container path translation |
| `DEVCLAW_HOST_PATH_PREFIX` | — | Host-side counterpart |
| `DEVCLAW_CONTEXT_TRIPWIRE_PCT` | `75` | Context-usage % at which the runner lands the session; `0` disables |
| `DEVCLAW_GIT_NAME` | `devclaw` | Git author/committer name for every commit devclaw or the worker writes |
| `DEVCLAW_GIT_EMAIL` | `devclaw@local` | Its email |
| `DEVCLAW_RUNNER_PY` | — | Host-engine override: the runner script path |
| `DEVCLAW_RUNNER_PYTHON` | — | Host-engine override: the interpreter |
| `DEVCLAW_SKILLS_DIR` | `/opt/devclaw/skills` | Where the runner reads the skill bundle (`runner/skills/` in-repo for the host engine) |
| `DEVCLAW_HOOKS_DIR` | `/opt/devclaw/hooks` | Where the runner reads the pre/post-run hooks |

## Queue (money brakes)

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_MAX_CONCURRENT` | `4` | Cap on concurrently-running sandboxes; `set_max_concurrent` overrides it live |
| `DEVCLAW_HOST_MEM_RESERVE` | `1536m` | Host RAM kept free beyond the sandboxes before another launch is admitted |
| `DEVCLAW_TICK_SECONDS` | `10` | The queue pump interval |
| `DEVCLAW_TASK_TIMEOUT_S` | `3600` | Wall-clock cap per session; a hit tears the sandbox down and the session is resumed next tick |

## Goal layer

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_GOAL_TICK_SECONDS` | `900` | The goal tick interval (a settled session and a webhook wake it early) |
| `DEVCLAW_GOAL_SESSIONS_PER_DAY` | `8` | The ONE money brake per goal: sessions per UTC day, counted from task rows |
| `DEVCLAW_MENTION` | `@devclaw` | The handle an issue/PR comment must carry to be an instruction |
| `DEVCLAW_CI_LOG_TAIL_LINES` | `120` | Lines of a failing CI job's log carried to the session, per check |
| `DEVCLAW_GOAL_NOTIFY_URL` | — | Where owner pings go (`{"text": ...}` POST; the notify relay) |
| `DEVCLAW_SELF_REPO` | — | `owner/name` of devclaw's own repo — the self-deploy target |
| `DEVCLAW_DEPLOY_QUIESCENCE_S` | `21600` | How long a pending self-deploy waits for no session to run before it expires |

## Runner (read inside the sandbox)

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_VERIFY_TIMEOUT_S` | `900` | Wall-clock cap for one `.devclaw/verify` run |
| `DEVCLAW_VERIFY_ROUNDS` | `3` | Red-verify rounds handed back to the same session before it ends |
| `DEVCLAW_ACP_IDLE_TIMEOUT_S` | `1800` | The agent is killed after this long with no protocol traffic |
| `DEVCLAW_VALIDATION_STEP_TIMEOUT_S` | `900` | Per-step cap of the agent-less validation runner path |
