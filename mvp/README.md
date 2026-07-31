# devclaw v2 — the stubborn loop

One file. One cognition boundary. A run always ends with a PR or a readable
report — never a wedged process waiting for a human at 3am.

## Thesis

v1 failed nights because it put **intelligence in the control plane**: ~6
distinct `claude --print` callers (planner / evaluator / decomposer / firming /
review / summary) plus per-checklist-item tasks, gates, and pushes made one
night's reliability the *product* of ~30 fragile LLM boundaries. v2 inverts
that: the shell is purely mechanical, and ALL cognition lives inside worker
sessions. The unit of work is the **whole goal** — one session-chain, one
branch, one PR. Full decision record: [`docs/proposals/v2-mvp.md`](../docs/proposals/v2-mvp.md);
v1 is archived intact at tag `v1-final` / branch `archive/v1`.

## Run it

```bash
python mvp/loop.py /path/to/repo "Add a /health endpoint with a test" \
    --strategy plan-first --deliver pr
```

```
positional: workspace (a git repo), goal ("text" or @goal.md)
--strategy  plan-first | replan | direct | @file.md      (default plan-first)
--verify    'shell cmd' — done only when it exits 0      (default OFF)
--max-iters N sessions                                   (default 10)
--branch    work branch                                  (default v2/<goal-slug>)
--deliver   pr | push | commit | none                    (default pr)
--session-timeout seconds                                (default 3600)
```

## How it works

Each iteration is a fresh `claude -p` session continuing the same goal in the
same working tree. Under `plan-first` (default), session 1 writes
`.devclaw2/PLAN.md` — a markdown checklist — and each later session executes one
item, checks it off, and commits it. **You steer by editing PLAN.md between
sessions.** Done = the agent writes `.devclaw2/DONE.md` (plus `--verify`
passing, if set) → the loop commits, pushes, opens a PR.

A **strategy is a prompt variant only** — the shell is byte-identical across
all of them. `replan` lets each session revise the plan first; `direct` skips
planning entirely; `@file.md` is your own template, used verbatim.

## The contract (never-block)

The shell has no "ask the human and wait" state. Sessions that die from OOM or
timeouts are retried (bounded, backoff); a session-chain making no progress for
2 rounds — or hitting the session cap — is **abandoned loudly**:
`.devclaw2/REPORT.md` is written and committed, and with `--deliver pr` a WIP
draft PR is opened. The morning artifact is always a finished PR or a readable
report.

Kept from v1's scar tissue: transient-retry on claude signal-death (#448/#449),
the no-progress brake, loud delivery failure (#183), and the OAuth-only
invariant (`ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` stripped from the child
env — an autonomous run must never silently switch to metered billing).

## What v2 deliberately does not have

Docker sandbox, MCP server, heartbeat/tick, goal phases, decomposer, evaluator,
LLM review gates, browser gate, SQLite state. State is the git repo itself;
visibility is stdout + the PR. The tripwire: if this shell ever needs a second
LLM call or a third config knob to work, the simple shape is wrong — stop and
reassess, don't grow it back into v1.

## Tests

`tests/test_v2_loop.py` — fully stubbed (fake claude via `DEVCLAW2_CLAUDE_CMD`,
scratch git repos). `DEVCLAW2_BACKOFF_S=0` makes retries instant in tests.
