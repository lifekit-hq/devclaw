# devclaw

**A software-development loop you supervise instead of operate.**

You file a GitHub issue with a `## Done when` section. devclaw hands it to a
session — Claude Code in a per-session docker sandbox — that plans with speckit
inside the repository, lands one reviewable increment on the goal's branch,
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

Prerequisites: Python 3.11+, docker, a logged-in `claude` CLI, and a `GH_TOKEN`
with `repo` (the host reads PRs, posts the devclaw records, merges).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
docker build -t devclaw-sandbox:latest -f .sandcastle/Dockerfile .

DEVCLAW_TRANSPORT=stdio devclaw-mcp                    # MCP over stdio
DEVCLAW_TRANSPORT=http DEVCLAW_PORT=8000 devclaw-mcp   # MCP at /mcp, the console at /console
```

`devclaw-mcp` is the server; `devclaw` is the CLI (`devclaw doctor`,
`devclaw schedule …`, `devclaw projects …`). Configuration is environment-only:
every variable is in [`docs/reference/env-vars.md`](./docs/reference/env-vars.md),
pinned to `devclaw/config.py` by a sync test.

| `DEVCLAW_ENGINE` | Engine | Use |
|---|---|---|
| *(unset)* | the worker in a per-session **docker sandbox** | production |
| `host` | the worker on the host, no container | dev where docker is unavailable |
| `stub` | deterministic, no sandbox, no `claude` | the test suite |

```bash
pip install -e ".[dev]"
pytest          # the tripwire suite — no docker, no claude
ruff check . && mypy && lint-imports
```

## How it works

| Layer | Code | Role |
|---|---|---|
| MCP surface | `devclaw/server/` | tools, routes, the console, auth |
| Goal layer | `devclaw/goal/` | the tick rule, the world fingerprint, the two prompts, the done-gate |
| Queue + engine | `devclaw/task_queue.py`, `devclaw/queue/`, `devclaw/engine/`, `devclaw/gates.py`, `devclaw/delivery/` | one session: place the branch, run, the four gates, deliver |
| Worker harness | `runner/runner.py` (inside the sandbox) | the ACP turn-loop, the skill bundle, the in-session verify loop |

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
- **Not a rebuild of Claude Code** — `claude-code` over ACP is the agent inside
  the sandbox; devclaw supplies facts and tools and reads the mechanical verdict.
- **Not infallible.** Autonomous means "does not need the next prompt". What
  bounds the model judging the model's work is mechanical evidence: the
  project's own CI on the exact delivered head, the materialized change span,
  the sandbox's own verify run.

## Docs

- [`docs/INDEX.md`](./docs/INDEX.md) — every doc, its purpose, a currency tag.
- [`docs/architecture.md`](./docs/architecture.md) — the mental model and the locked contract.
- [`CLAUDE.md`](./CLAUDE.md) — the working contract an agent reads first.
- [`specs/`](./specs/) — direction memory; v1's forty-five specs are superseded rows in its ledger.

## License

[MIT](./LICENSE). Copyright 2026 Denys Sychov.
