# CLAUDE.md — devclaw harness contract

The first doc an agent reads before touching this repo. [`README.md`](./README.md)
is the accurate, current narrative; this file distills it into a working contract.
When the two disagree, the code wins — cross-check before you trust either.

## What devclaw is

A **software-development agentic loop**: you hand it a durable goal with verifiable
completion criteria, and a self-executing loop carries it — plan → sandboxed
execution → verify gate → evaluate → iterate — with hard brakes (retry caps,
no-progress watchdog, `stalled`/`needs_human` verdicts) so it never optimizes into
the void. **One primitive, one dial** (ADR 0003): goal and program are the same
thing; `create_goal(mode=long_lived|one_shot)` selects the re-evaluation cadence over
ONE identical execution path — the worker plans and executes via speckit
in-sandbox (spec 008; the host-cognition chain was removed by the 008 shrink) —
(`start_program` is a deprecated alias for one_shot). It sits **behind MCP** and is driven by an **OpenClaw waiter agent** that
translates chat into tool calls; devclaw never talks to the user. Cognition is
always `claude` over Pro/Max **OAuth — no API key, no metered billing**.

## The layer map — where a change belongs

The system is 5 layers below the user (canonical detail: [`docs/architecture.md`](./docs/architecture.md)).
Only layer 5 is an agent harness in the technical sense.

| # | Layer | Code | Put a change here if it's about… |
|---|---|---|---|
| 1 | **MCP surface** | `devclaw/server/` | a tool/endpoint, auth, console, transport — pure protocol |
| 2 | **GoalService + heartbeat** | `devclaw/goal/` | goal state machine, lifecycle (`executing` only since the 008 shrink), the ~15-min tick |
| 3 | **Cognition callers** | `devclaw/goal/evaluator.py`; `devclaw/elicitation.py`; `devclaw/intake_readiness.py` | a one-shot `claude --print` prompt/parse (done-gate evaluation, scope-grill, intake readiness — planning cognition was relocated into the worker's speckit run, spec 008 shrink) |
| 4 | **TaskQueue + engine** | `devclaw/task_queue.py`, `devclaw/engine/` | dispatch, concurrency, the container launcher, the settle/gate path |
| 5 | **Worker harness** | `runner/runner.py` (runs *inside* the sandbox) | the in-sandbox agent turn-loop, skills/hooks, verify_cmd — the only true harness |

The chain is strict: `1 → 2 → 3` (cognition) or `1 → 2 → 4 → 5` (execution). No
layer reaches through another (layer 1 must not dispatch tasks; layer 2 must not
spawn containers itself — it goes through the engine).

## Load-bearing invariants — DO NOT VIOLATE

- **OAuth only.** `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are **actively stripped**
  at the cognition caller (`devclaw/cognition.py`), the LLM-call primitive (`devclaw/llm_call.py`),
  host engine (`devclaw/engine/host.py`), and sandbox
  (`devclaw/engine/sandcastle.py`, `runner/runner.py`) — a stray key
  must never silently switch autonomous runs onto metered billing.
- **Model-agnostic worker layer.** Skills are **plain markdown** (no model-specific
  frontmatter, no native `Skill(...)` calls); hooks are **bash `.sh` files** invoked
  by `runner.py` (never a `settings.json`); cross-tool capability goes through **MCP**,
  not vendor tool-wiring; per-repo discovery is `ls .agent/skills/` + `cat`. The day
  `claude-code` is swapped for another ACP-speaking agent, only the runner's
  agent-drive seam changes — the agent command (`acp_command` payload /
  `DEVCLAW_ACP_COMMAND`) its zero-dep ACP client spawns (spec 011; the
  fake-agent regression tests enforce this).
- **Zero-token idle guard.** An idle goal and an in-flight-still-running goal cost
  **~0 `claude` calls** — the heartbeat is mechanism; cognition runs only when there's
  real work. Ordered on purpose in `devclaw/goal/tick.py` (the cheap SQLite/timestamp
  checks run *before* any LLM call). Adding a tick-path LLM call that fires on idle
  breaks the quota guarantee (the test asserts `FakeClaude.calls == 0` on idle paths).
- **Single writer to state.** Only the **TaskQueue** mutates task rows; `StateStore` is
  an append-only event log, views are projections. Goal state is owned by `GoalStore`
  and (as of Tranche 1) lives in SQLite in the same `devclaw.db` — `goal_status`,
  `goal_steering`, `goal_log`, `goal_deliveries`, `goal_docs`, `goal_phase_history`,
  plus goal-transcending `project_docs` (the repo brief, keyed by workspace path).
  `STATUS.md`/`log.md`/`inbox.md`/`deliveries.md`/`RUN_SUMMARY.md`
  are generated **views** — human- and rollback-readable, never read back for
  decisions. Mutation is NOT heartbeat-exclusive: `steer_goal`/`resume_goal`/`cancel_goal` write from
  the MCP-tool call path too, concurrently with the heartbeat — `GoalStore.transition()`
  is the CAS'd choke point (`devclaw/goal/transitions.py`'s `LEGAL` table) that makes
  that safe: a stale-snapshot write raises `TransitionConflict` and is abandoned rather
  than silently clobbering the other writer. No upstream layer caches either.
- **"Done" is a proposal, gated on grounded evaluation.** The planner's `done` triggers
  a read-only `review_repository` against the firmed `done_when` + `stub_acceptable`; the
  goal closes **only if the evaluator confirms `achieved`**. Never gate completion on
  counting PRs or backlog items.
- **`done_when` is repository behavior, never delivery ceremony.** How the work ships,
  how many PRs it takes, which branch it lands on, whether/who merges it, and which
  issues or PRs get closed are NOT completion criteria — the evaluator drops them at
  decomposition (`prompts/goal-evaluator.md` step 1a) and names the drop in its
  rationale. Writing ceremony into a contract is how a goal ends up unclosable: under
  `goal-branch` the cumulative PR deliberately stays open for the done-gate, and the
  sandbox carries no GitHub credential, so no run can ever satisfy such a clause. Hold
  this when you AUTHOR a goal — the gate's drop is a backstop, not a licence.

## Hardening philosophy (Tranche 0 — baked in, not in the README yet)

Recent work made the loop fail **loud, not silent**. Match it when you add code:

- **Verification fails CLOSED.** A quality-gate crash is **not** an approval — an
  exception in the gate settles the task failed (#186). *Recalibrated by the gate
  strictness dial (ADR 0007), not repealed:* the dial sets which gates are
  *consulted*, not only their consequence. Under a goal's default `trust` the
  **per-increment adversarial diff review is dropped from the task gate chain
  entirely** (spec `001-review-gate-repositioning`) — it was the #1 mechanism-wedge
  source, and the human reviews every PR while the goal-level done-gate re-catches
  its findings; under `strict` it is consulted and fails closed exactly as before.
  The browser-E2E gate stays dial-able — under `trust` a surviving finding
  advises-and-ships (loud + surfaced in the PR, human merge is the backstop)
  instead of wedging; under `strict` it fails closed. The done-gate's verdict is owned by the
  ``done_when`` contract in both modes; its *structural* axis rides the same
  dial (under ``trust`` reported concerns advise-and-ship as follow-ups on the
  close, under ``strict`` they hold it open), and a done-gate that refuses to
  close the same goal 3 rounds in a row parks it for the owner
  (``donegate_churn``) instead of re-advancing forever. The
  verify/test-integrity/done gates stay always-hard, and every *unreviewable* case (crash/quota) in a
  *consulted* gate still fails closed in both modes — #186 governs consulted gates,
  and a gate not consulted under `trust` produces no silence to ship on.
- **An unreviewable change fails closed *and fast*, not forever.** When the review gate
  can't produce a verdict at all (a crash / non-JSON response on an oversized diff), the
  task fails **closed** (never ships — #186 holds) but **without an agent retry**:
  re-running reproduces the same diff and re-crashes the gate identically, so the retry
  is futile and only burns the budget + the goal-level re-dispatch loop. The failure
  carries an actionable reason (split the diff / review by hand). A crash is still not an
  approval; it's just not an infinite loop either (L1 fix, closeloop-bench scaffold wedge).
- **Broken delivery fails; never "done without a PR."** A delivery that can't push/PR
  settles the task `failed`, not a silent success (#183).
- **Lost/corrupt state blocks legibly.** A missing in-flight ref or corrupt contract
  file blocks the goal with an owner ping — it never wedges the tick loop or silently
  degrades (#185, #188 atomic contract writes + loud corruption blocking).
- **Usage limits pause-and-resume.** A quota/rate-limit hit is *classified*, not
  failed: one account-wide `paused_until` gates both queue and heartbeat, WIP is
  preserved, the owner is pinged once, and it auto-resumes when the cap resets
  (#189/#190/#191). Zero tokens while paused. Auth failures (expired login) ride
  the same pause since the 2026-07-20 night incident — actionable "re-login"
  ping, fixed re-probe cadence, auto-resume after the human fixes the login.
- **Mechanical blocks auto-heal; recovery is a verb, not a fake steer.** Blocks
  carry a structured `blocked_kind`; `mechanical:corrupt_doc` and
  `mechanical:prep` self-heal when their condition clears (zero LLM, damped by
  a persisted per-goal heal budget + backoff); `needs_answer`/`bug`/`lost_ref`/
  `dispatch_cap` stay human-gated. `resume_goal` re-attempts the SAME contract
  without recording steering; `steer_goal` stays the direction-change verb
  (2026-07-13 harden-loop tranche, #228–#238).

Rule of thumb: **loud failure over silent degradation.**

## Design doctrine — systemic over specific (Denys, 2026-07-18)

We are building a **system**, not a pile of fixes attached to the cases that
surfaced them. Apply this while triaging, planning, and fixing:

- **Fix the class, not the instance.** When a concrete failure arrives (one
  repo, one component, one gate misfire), first ask "what class of failure is
  this an instance of?" and change the *rule* — e.g. a Playwright gate wedging
  one library component is a trigger-semantics bug (app surface vs library
  surface), not a that-component problem. A fix that only unwedges the case
  that hurt today is a smell.
- **Software development is the first domain, not the definition.** The mental
  model (durable goal → plan → sandboxed execute → verify gate → evaluate →
  iterate) is domain-agnostic. Keep domain specifics (code, PRs, repos,
  Playwright) at the edges — worker skills, gates, prompts — so the loop could
  someday drive a second domain without rewiring layers 1–4.

```
devclaw/
├── server/          MCP surface — tools.py (@mcp.tool), http.py (routes/SSE), lifecycle.py (auth+serve)
├── goal/            durable goal layer — service, tick, planner, evaluator, store, engine, merge, notify
├── engine/          execution — sandcastle.py (docker run --rm, prod), claude_sdk.py, host.py, stub.py
├── delivery/        commit → branch → push → PR; deploy.py (Tailscale); repo.py (gh repo create)
├── quality/         gates past green tests — the self-contained fail-closed gate (own prompts/ + README), pre-PR adversarial review, eval_judge, evals
├── loom/            engine-agnostic substrate — limits, test_integrity, trace
├── prompts/         system prompts as .md files (load_prompt(slug)); the 3 gate prompts live in quality/prompts/
├── program_plan.py · cognition.py · llm_call.py · state_store/ · task_queue.py · project_registry.py · cli.py · …
runner/runner.py   the in-sandbox worker harness — drives the ACP agent via acp_client.py; line-delimited JSON on stdout
.sandcastle/Dockerfile       per-task sandbox image
docs/                        architecture + flows + env + runbooks (start at docs/INDEX.md)
tests/                       pytest — fully stubbed (no docker, no claude)
evals/                       stub e2e suite + real-pipeline harnesses
```

## Run the tests

```bash
pip install -e ".[dev]"
pytest        # ~1870 tests, all stubbed — no docker, no claude
```

Engine modes (`DEVCLAW_ENGINE`): **unset** = the worker runner in a per-task docker
sandbox (production); `host` = the runner on the host, no sandbox (dev/CI); `stub` =
deterministic, no docker/no claude (the mode the test suite and `evals/run_all.py`
use). For the real pipeline (a logged-in `claude` + docker), follow
[`docs/runbooks/live-shakedown.md`](./docs/runbooks/live-shakedown.md).

## Conventions

- **Conventional-commit messages** (`fix(queue): …`, `feat(cognition): …`).
- **Every behavior-change PR adds a named regression test** — the T0 fixes each
  shipped with one (`test_integrity_gate.py`, `test_delivery.py`, `test_goal_tick.py`, …).
- **Branch per change**; open a PR, don't push to `main`.
- **Keep `docs/` honest.** If a change makes a doc wrong, fix the doc in the same PR
  and update its currency tag in [`docs/INDEX.md`](./docs/INDEX.md). A stale doc that
  looks current is worse than no doc.

## The dev harness (`.claude/`)

This repo carries a Claude-Code project harness for developing devclaw itself
(distinct from layer 5's model-agnostic `.agent/skills/`, which is product):
`rules/` (testing · git-workflow · cognition-prompts · speckit-workflow —
auto-loaded, the operational detail this file deliberately doesn't carry;
speckit-workflow is the anti-drift pipeline since 2026-08-13: every
behavior-changing change starts `/speckit-specify` → `/speckit-clarify` →
plan → tasks → implement in `.specify/`, no implementation before clarify,
with the constitution (`.specify/memory/constitution.md`) as the invariant
statement specs are checked against; `docs/proposals/` + `docs/decisions/`
are frozen history), `commands/ship` (the pre-PR ritual as `/ship`),
`hooks/` (docs-reminder + a main-branch guard that blocks commit/push on main —
escape hatch: prefix `DEVCLAW_ALLOW_MAIN=1`), and `skills/` (docs-audit,
live-shakedown).

## Where to look next

- [`docs/INDEX.md`](./docs/INDEX.md) — every doc, one-line purpose, currency tag. **Read this before trusting any other doc.**
- [`docs/architecture.md`](./docs/architecture.md) — the mental model + the locked 5-layer contracts and invariants.
- [`docs/flows/task-execution.md`](./docs/flows/task-execution.md) — the temporal trace of one task, every hop.
- [`docs/reference/env-vars.md`](./docs/reference/env-vars.md) — every env var, grouped.
