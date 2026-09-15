# docs/ — index & currency map

Every doc under `docs/`, its one-line purpose, and a **currency tag**: **CURRENT**
(verified against the code on the date given) or **STALE — see note**. Currency
is verified by checking each doc's load-bearing claims against the code, never
by trusting its own status line. A change that makes a doc wrong fixes the doc
and this tag in the same PR.

```
docs/
├── architecture.md    the mental model + the locked contracts (spec 046)
├── flows/             one session's journey; how a session becomes a PR and a merge
├── reference/         env vars
├── runbooks/          doctor, live shakedown, webhooks, the VPS waiter, self-deploy
└── assets/            console screenshots
```

Direction memory lives in [`../specs/`](../specs/): spec 046 is the design;
every v1 spec is a superseded row in `specs/README.md`.

## System

| Doc | Purpose | Currency |
|---|---|---|
| [`architecture.md`](./architecture.md) | **Start here.** The four layers, the tick rule, the world fingerprint, what the host stores and what GitHub carries, the invariants and where each is enforced. | **CURRENT** — 2026-09-14 (the first-run manifest gate; written 2026-09-13 for spec 046) |

## Flows

| Doc | Purpose | Currency |
|---|---|---|
| [`flows/task-execution.md`](./flows/task-execution.md) | One session, hop by hop: tick → row → branch placement → sandbox → the verify loop → the gates → delivery → the exit. | **CURRENT** — 2026-09-13 |
| [`flows/delivery.md`](./flows/delivery.md) | The goal branch, its one PR, the done-gate review, the merge-on-close. | **CURRENT** — 2026-09-13 |

## Reference

| Doc | Purpose | Currency |
|---|---|---|
| [`reference/env-vars.md`](./reference/env-vars.md) | Every env var the runtime reads, grouped; pinned to `devclaw/config.py` by `tests/test_env_vars_doc_sync.py`. | **CURRENT** — 2026-09-13 |

## Runbooks

| Doc | Purpose | Currency |
|---|---|---|
| [`runbooks/doctor.md`](./runbooks/doctor.md) | The read-only post-deploy check: `doctor` tool / `devclaw doctor`. | **CURRENT** — 2026-09-13 |
| [`runbooks/live-shakedown.md`](./runbooks/live-shakedown.md) | Exercising the real pipeline (logged-in `claude` + docker) on one goal. | **CURRENT** — 2026-09-13 |
| [`runbooks/webhooks.md`](./runbooks/webhooks.md) | The GitHub webhook that wakes the tick early. | **CURRENT** — 2026-09-13 |
| [`runbooks/vps-waiter-deploy.md`](./runbooks/vps-waiter-deploy.md) | The OpenClaw waiter on the VPS and its tool menu. | **CURRENT** — 2026-09-13 (tool menu) |
| [`runbooks/devclaw-self-deploy.md`](./runbooks/devclaw-self-deploy.md) | The self-deploy: a push to main arms it, the heartbeat fires it once no session runs. | **CURRENT** — 2026-09-15 (the auto lane now prunes old devclaw-mcp/devclaw-sandbox tags after a healthy deploy) |

## Where the docs are NOT

- **The agent harness contract** — [`../CLAUDE.md`](../CLAUDE.md).
- **The product narrative** — [`../README.md`](../README.md).
- **The design** — [`../specs/046-devclaw-v2/spec.md`](../specs/046-devclaw-v2/spec.md).
- **Worker-layer skills** — `runner/skills/` (the one home; baked into the sandbox image).
