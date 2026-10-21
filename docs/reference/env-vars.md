# DevClaw environment variables

Single source of truth for every env var the runtime reads — enforced by
`tests/test_env_vars_doc_sync.py` (a var read in code but missing here, or
documented here but read nowhere, fails the suite). The CODE home is
`devclaw/config.py`, the one module allowed to read `DEVCLAW_*` vars
(`tests/test_config_single_doorway.py`). Empty or unset means "the default in the table". Real env
vars always win over `.env`.

## Server transport + auth

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_TRANSPORT` | `stdio` | `stdio` (local dev / tests) or `http` (the long-running service) |
| `DEVCLAW_PORT` | `8000` | HTTP port when `DEVCLAW_TRANSPORT=http` |
| `DEVCLAW_HOST` | `0.0.0.0` | HTTP bind address; `127.0.0.1` restricts to loopback |
| `DEVCLAW_TOKEN` | — | Bearer token for every HTTP route except `/health` and `/metrics` (`Authorization: Bearer` or `?token=`). Unset = no auth. |
| `DEVCLAW_DOTENV` | `.env` (cwd) | Path of the `.env` file loaded at startup |
| `DEVCLAW_GIT_SHA` | — | Deployed commit, baked at build time; shown on `/health` |
| `DEVCLAW_BUILT_AT` | — | Image build timestamp, same provenance |
| `DEVCLAW_WEBHOOK_SECRET` | — | HMAC secret for `POST /webhooks/github`; unset ⇒ the route answers 404. A verified delivery wakes the goal tick early. |

## Storage

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_DB` | `./devclaw.db` | The SQLite home: tasks, events, goals, decisions, control flags, per-user console appearance |
| `DEVCLAW_EVENTS_RETENTION_DAYS` | `30` | Days of session events kept; `0` disables the prune |

## Engine

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_ENGINE` | *(unset)* | *(unset)* = `claude -p` in the goal's treehouse worktree (production; refuses to boot without its credentials); `host` = the same engine with no boot-time credential check (a dev run on a logged-in host); `stub` = deterministic, no claude (the suite) |
| `DEVCLAW_CLAUDE_BIN` | `claude` | The claude CLI each session runs |
| `DEVCLAW_EXEC_MODEL` | `claude-sonnet-4-6` | The model each session runs; `""` = the CLI's own default |
| `DEVCLAW_FIRST_FRAME_S` | `120` | A session that streams no first frame within this many seconds is killed and settles interrupted |
| `DEVCLAW_SILENCE_S` | `1800` | A session silent this long after its first frame is killed and settles interrupted |
| `DEVCLAW_VERIFY_TIMEOUT_S` | `900` | Wall-clock cap for one `.devclaw/verify` run |
| `DEVCLAW_VERIFY_ROUNDS` | `3` | Red-verify rounds handed back to the same session (`--resume`) before it ends |
| `DEVCLAW_TREEHOUSE_BIN` | `treehouse` | The treehouse CLI that leases one worktree per goal (pool root: treehouse's own `TREEHOUSE_ROOT`) |
| `DEVCLAW_GIT_NAME` | `devclaw` | Git author/committer name for every commit devclaw or a session writes |
| `DEVCLAW_GIT_EMAIL` | `devclaw@local` | Its email |

## Queue (money brakes)

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_MAX_CONCURRENT` | `4` | Cap on concurrently-running sessions; `set_max_concurrent` overrides it live |
| `DEVCLAW_TICK_SECONDS` | `10` | The queue pump interval |

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
