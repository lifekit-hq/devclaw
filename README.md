# devclaw

**A software-development loop you supervise instead of operate.**

You file a GitHub issue with a `## Done when` section. devclaw hands it to a
session — `claude -p` in the goal's own treehouse worktree — that plans the way the
repository itself plans, lands one reviewable increment on the goal's branch,
runs what the project's CI runs before it finishes, and hands back one line:
`DELIVERED`, `DONE`, `BLOCKED`, or `NOTHING`. Every ~15 minutes the host reads
the world — the PR, its CI, the threads — and spawns the next session only when
something changed. When the session proposes `DONE`, a fresh read-only review
session judges the repository against the contract; the host validates that
verdict mechanically and squash-merges. When it stops, it stops on a fact
posted to the thread, and a comment mentioning the bot wakes it.

Some Python for what is determined, a model for what is reasoned, and one line
between them. The design is [`specs/046-devclaw-v2/spec.md`](./specs/046-devclaw-v2/spec.md).

It sits behind MCP; an [OpenClaw](https://openclaw.ai) waiter translates chat
into tool calls. Cognition is `claude` over a Pro/Max OAuth session — **no
`ANTHROPIC_API_KEY`, no metered billing**: a stray key is stripped at every
spawn site rather than honored.

## Run it

Prerequisites: Python 3.11+, git, a logged-in `claude` CLI,
[treehouse](https://github.com/kunchenguid/treehouse) v2, and a `GH_TOKEN`
with `repo` (the host reads PRs, posts the devclaw records, merges).

A worktree is **not a filesystem sandbox**: a session runs on the host with the
host's tools and can reach anything the devclaw user can. What it never gets is
a metered API key or the host's GitHub token — those are stripped from its
environment.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .

DEVCLAW_TRANSPORT=stdio devclaw-mcp                    # MCP over stdio
DEVCLAW_TRANSPORT=http DEVCLAW_PORT=8000 devclaw-mcp   # MCP at /mcp, the console at /console
```

`devclaw-mcp` is the server; `devclaw` is the CLI (`devclaw doctor`,
`devclaw schedule …`, `devclaw projects …`). Configuration is environment-only:
every variable is in [`docs/reference/env-vars.md`](./docs/reference/env-vars.md),
pinned to `devclaw/config.py` by a sync test.

| `DEVCLAW_ENGINE` | Engine | Use |
|---|---|---|
| *(unset)* | `claude -p` in the goal's treehouse worktree | production (refuses to boot without its credentials) |
| `host` | the same engine, no boot-time credential check | dev on a logged-in host |
| `stub` | deterministic, no `claude`, no treehouse | the test suite |

```bash
pip install -e ".[dev]"
pytest          # the tripwire suite — no claude, no treehouse
ruff check . && mypy && lint-imports
```

The console lives in `frontend/`: an Angular 21 PWA on the shared
`@lifekit-hq/ui` stack, installable from `/console/` with a precache-only
service worker. `npm run build` writes it into the server package's gitignored
`console_dist` directory, which the host serves at `/console` and the image
bakes in. `npm ci` there reads
`@lifekit-hq/*` from GitHub Packages, so it needs `NODE_AUTH_TOKEN` (the image
build takes the same token as a BuildKit secret):

```bash
cd frontend && npm ci && npm run lint && npm test && npm run build && npm run e2e
```

`npm run e2e` includes the design and accessibility gate
(`e2e/quality.spec.ts`): every route, light and dark, desktop and phone,
through the impeccable URL scan and axe. The scan's engine is fetched once by
`e2e/impeccable-engine.sh` and pinned by sha256. A finding the console cannot
fix itself is accepted, with its reason, in `e2e/quality-exceptions.json`.

## How it works

| Layer | Code | Role |
|---|---|---|
| MCP surface | `devclaw/server/` | tools, routes, the console, auth |
| Goal layer | `devclaw/goal/` | the tick rule, the world fingerprint, the two prompts, the done-gate |
| Queue + engine | `devclaw/task_queue.py`, `devclaw/queue/`, `devclaw/engine/`, `devclaw/gates.py`, `devclaw/delivery/` | one session: place the branch, run, the four gates, deliver |
| Session engine | `devclaw/engine/claude.py`, `devclaw/skills/` | the `claude -p` stream and its silence watchdog, the skill bundle, the in-session verify loop over `--resume` |

**One tick, per goal.** Read the world: the PR's head and state, the CI rollup
for that head, the newest comment mentioning the bot, the newest devclaw
record, the credentials that probe green. Merged → close. Blocked (a devclaw
stop newer than the owner's last instruction) → nothing. Red CI on a delivered
head → stop with the failing log, never retry. `DONE` + green CI → the review
session, then merge. Fingerprint unchanged → nothing, zero tokens. Otherwise →
one session with the world as facts.

**The owner's one channel.** A comment on the issue or PR that mentions
`@devclaw` (the `decide` tool posts one). Everything devclaw itself writes on a
thread carries a marker and is a fact, never an instruction.

**Tools.** `create_goal`, `get_goal`, `list_goals`, `decide`, `cancel_goal`,
`get_status`, `get_events`; `register_project`, `list_projects`,
`project_status`, `update_project`, `delete_project`; `get_run_schedule`,
`set_run_schedule`, `set_operator_hold`, `set_max_concurrent`,
`clear_usage_pause`; `doctor`.

## What this is NOT

- **Not a chatbot** — a backend service the waiter calls.
- **Not a rebuild of Claude Code** — `claude -p` is the agent in the goal's
  worktree; devclaw supplies facts and tools and reads the mechanical verdict.
- **Not a sandbox** — the worktree isolates one goal's branch from another's,
  not the host from the session.
- **Not infallible.** Autonomous means "does not need the next prompt". What
  bounds the model judging the model's work is mechanical evidence: the
  project's own CI on the exact delivered head, the materialized change span,
  the session's own verify run.

## Docs

- [`docs/INDEX.md`](./docs/INDEX.md) — every doc, its purpose, a currency tag.
- [`docs/architecture.md`](./docs/architecture.md) — the mental model and the locked contract.
- [`CLAUDE.md`](./CLAUDE.md) — the working contract an agent reads first.
- [`specs/`](./specs/) — direction memory; v1's forty-five specs are superseded rows in its ledger.

## License

[MIT](./LICENSE). Copyright 2026 Denys Sychov.
