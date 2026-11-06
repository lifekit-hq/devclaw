# docs/ — index & currency map

Every doc under `docs/`, its one-line purpose, and a **currency tag**: **CURRENT**
(verified against the code on the date given) or **STALE — see note**. Currency
is verified by checking each doc's load-bearing claims against the code, never
by trusting its own status line. A change that makes a doc wrong fixes the doc
and this tag in the same PR.

```
docs/
├── architecture.md    the mental model + the locked contracts (spec 046)
├── case-study.md      what devclaw delivered, what failed, what it costs
├── flows/             one session's journey; how a session becomes a PR and a merge
├── reference/         env vars
├── runbooks/          doctor, live shakedown, webhooks, self-deploy, no-mistakes in the container
├── spikes/            dated experiments that settle one design question
└── assets/            console screenshots (the case study's)
```

Direction memory lives in [`../specs/`](../specs/): spec 046 is the design;
every v1 spec is a superseded row in `specs/README.md`.

## System

| Doc | Purpose | Currency |
|---|---|---|
| [`architecture.md`](./architecture.md) | **Start here.** The four layers, the tick rule, the world fingerprint, what the host stores and what GitHub carries, the invariants and where each is enforced. | **CURRENT** — 2026-11-06 (a hard-to-reverse change — migration, deletion, production data, classified from the Done-when text and the PR's paths — waits for the owner's `merge` (from the host's gh identity or an `OWNER`) before the squash-merge — #979; the done-gate scope-checks the PR's files against the files the issue and the owner name before the first review — #978; the worker image carries the Chromium system libraries and the .NET SDK; Chromium runs without its own sandbox in the container, through a wrapper only that image sets — #987; the session is told at its first turn when the host's Chromium cannot load a library, and the verify run carries `DEVCLAW_DOCS_ONLY=1` for a documentation-only diff — #987; the hand-off check falls back to the run's `head_sha` after `git fetch no-mistakes` once a run has pushed or finished — #984; 2026-11-05: the no-mistakes pipeline is initialised before the session starts and its home is on the state volume — [runbook](./runbooks/no-mistakes-in-container.md); 2026-10-21: the four-layer diagram marks what the re-cut kept and what `claude -p`, a treehouse worktree and no-mistakes replaced; any MCP client drives the tools, no chat waiter — #955; the done-gate on the new engine: a review that changes its worktree fails closed, a verdict counts only for the head it read and the merge is pinned to it, an interrupted review is a review again, the owner's answer wakes a refused gate — #954; the home-built gates and delivery are gone: the session ships through no-mistakes, the engine requires a passed run carrying its head, CI stays the tick's read — #953; the session engine is `engine/claude.py` in a treehouse worktree leased per goal, a silent stream — not a wall clock — interrupts a session, Safety names the worktree and the credential strip, no sandbox, `--resume` only on the quota pause path, an interrupted task's successor starts fresh — #952; 2026-10-13: the code map adds the `me` route and `state_store/preferences.py`, and the stored state names `user_preferences`; the console is the Angular PWA `frontend/`, React `console/` deleted; written 2026-09-13 for spec 046) |
| [`case-study.md`](./case-study.md) | The case study and evidence page: what makes devclaw distinct, current console screenshots, the delivered PRs and how they were verified, the v0.1 result and lessons, the cost model. | **CURRENT** — 2026-10-21 (written for #958; screenshots taken from the hosted demo, PRs verified from GitHub's records) |

## Flows

| Doc | Purpose | Currency |
|---|---|---|
| [`flows/task-execution.md`](./flows/task-execution.md) | One session, hop by hop: tick → row → branch placement → the session in the goal's treehouse worktree → the verify loop → the gates → delivery → the exit. | **CURRENT** — 2026-11-05 (the no-mistakes pipeline is initialised before the session starts and its home is on the state volume — [runbook](./runbooks/no-mistakes-in-container.md); 2026-10-21: the no-mistakes hand-off replaces the gates and delivery, `hand_off`/`closes` on the request, `pr_url` on the result — #953; `claude -p` in a treehouse worktree, the silence watchdog, `--resume` only on the quota pause path while an interrupted task's successor starts fresh, the session env strips every host-only credential, a failed lease recorded `INTERRUPTED` and retried up to the daily session cap, the session and verify process groups reaped on exit and made the OOM killer's preferred victims — #952) |
| [`flows/delivery.md`](./flows/delivery.md) | The goal branch, its one PR, the done-gate review, the merge-on-close. | **CURRENT** — 2026-11-06 (the scope check before the first review — #978; 2026-10-21: the session ships the branch and its PR through no-mistakes; delivery titling is gone — #953; the session, not a sandbox, runs what CI runs — #952) |

## Reference

| Doc | Purpose | Currency |
|---|---|---|
| [`reference/env-vars.md`](./reference/env-vars.md) | Every env var the runtime reads, grouped; pinned to `devclaw/config.py` by `tests/test_env_vars_doc_sync.py`. | **CURRENT** — 2026-11-05 (`NM_HOME`'s state is reported by `devclaw doctor` — #982; 2026-10-21: `stub` also serves the README *Try it* quickstart — #957; the Engine group: `DEVCLAW_CLAUDE_BIN`, the stream watchdog, the verify rounds, `DEVCLAW_TREEHOUSE_BIN`; the sandbox and Runner groups removed — #952; 2026-10-13: `DEVCLAW_DB` names the per-user console appearance it now holds) |

## Runbooks

| Doc | Purpose | Currency |
|---|---|---|
| [`runbooks/doctor.md`](./runbooks/doctor.md) | The read-only post-deploy check: `doctor` tool / `devclaw doctor`. | **CURRENT** — 2026-11-05 (the `handoff` check: `no-mistakes` on PATH and the state of `NM_HOME` — #982; 2026-10-21: tools: `gh`, `git`, and the `claude` and `treehouse` binaries off the stub engine, no `docker`; a goal's leased worktree — #955) |
| [`runbooks/live-shakedown.md`](./runbooks/live-shakedown.md) | Exercising the real pipeline (logged-in `claude` + treehouse) on one goal. | **CURRENT** — 2026-11-05 (`devclaw doctor` also names a missing `no-mistakes` — #982; 2026-10-21: no sandbox image — #952) |
| [`runbooks/webhooks.md`](./runbooks/webhooks.md) | The GitHub webhook that wakes the tick early. | **CURRENT** — 2026-10-21 (re-verified, unchanged) |
| [`runbooks/devclaw-self-deploy.md`](./runbooks/devclaw-self-deploy.md) | The self-deploy: a published main commit (or the heartbeat noticing the build trails main) arms it, the heartbeat fires it once no session runs. | **CURRENT** — 2026-10-21 (§4 is the dated 2026-08-16 cutover record in past tense, making no current sandbox or waiter claim; the seam step attaches `notify-relay` only, no waiter seam, and the spec link points at its superseded row — #955; one image, `devclaw-mcp`, builds on hosted arm64 via `docker-build.yml`; the box pulls by SHA; the sandbox image is gone — #952) |
| [`runbooks/no-mistakes-in-container.md`](./runbooks/no-mistakes-in-container.md) | How the no-mistakes pipeline is wired into the `devclaw-mcp` container (`NM_HOME` on the state volume, the pre-session `init`, the entrypoint's stale-daemon cleanup), the fresh-container check, the read-only live check, and the undo. | **CURRENT** — 2026-11-05 (`devclaw doctor` reports the hand-off — #982; written for the wiring; the fresh-container check was run on the deployed image) |

## Spikes

| Doc | Purpose | Currency |
|---|---|---|
| [`spikes/no-mistakes-done-when.md`](./spikes/no-mistakes-done-when.md) | Whether no-mistakes' intent review judges a goal's `## Done when` clauses (#951): the run, the per-clause result, what it means for the done-gate. | **CURRENT** — 2026-10-21 (a dated record of one no-mistakes v1.99.0 run) |

## Where the docs are NOT

- **The agent harness contract** — [`../CLAUDE.md`](../CLAUDE.md).
- **The product narrative** — [`../README.md`](../README.md).
- **The design** — [`../specs/046-devclaw-v2/spec.md`](../specs/046-devclaw-v2/spec.md).
- **Worker-layer skills** — `devclaw/skills/` (the one home; the engine prepends them to the session prompt).
