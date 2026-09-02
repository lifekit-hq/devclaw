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
(the `start_program` alias, and the whole program/DAG dispatch lane behind it,
were retired by spec 022 US3). It sits **behind MCP** and is driven by an **OpenClaw waiter agent** that
translates chat into tool calls; devclaw never talks to the user. Cognition is
always `claude` over Pro/Max **OAuth — no API key, no metered billing**.

## The layer map — where a change belongs

The system is 5 layers below the user (canonical detail: [`docs/architecture.md`](./docs/architecture.md)).
Only layer 5 is an agent harness in the technical sense.

| # | Layer | Code | Put a change here if it's about… |
|---|---|---|---|
| 1 | **MCP surface** | `devclaw/server/` | a tool/endpoint, auth, console, transport — pure protocol |
| 2 | **GoalService + heartbeat** | `devclaw/goal/` | goal state machine, lifecycle (`executing` only since the 008 shrink), the ~15-min tick |
| 3 | **Cognition callers** | `devclaw/goal/evaluator.py`; `devclaw/goal/summary.py`; `devclaw/goal/triage.py`; `devclaw/intake_readiness.py` | a one-shot `claude --print` prompt/parse (done-gate evaluation, owner summary, self-triage, intake readiness — planning cognition was relocated into the worker's speckit run, spec 008 shrink; the scope-grill porch died with the prose lane, 2026-08-29 prune) |
| 4 | **TaskQueue + engine** | `devclaw/task_queue.py` (+ its `devclaw/queue/` mixins), `devclaw/engine/` | dispatch, concurrency, the container launcher, the settle/gate path |
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
  fake-agent regression tests enforce this). Worker-kind instructions have
  exactly ONE home — `runner/skills/`, baked to `/opt/devclaw/skills/` in the
  image and pointed at in-repo by the host engine. A second copy is not a
  fallback, it is a silent fork: an edit lands in the copy production never
  reads while the canonical skill says something else (#610). A missing bundle
  fails LOUD (`skills_missing`), never substitutes text (#613).
- **Zero-token idle guard.** An idle goal and an in-flight-still-running goal cost
  **~0 `claude` calls** — the heartbeat is mechanism; cognition runs only when there's
  real work. Ordered on purpose in `devclaw/goal/tick.py` (the cheap SQLite/timestamp
  checks run *before* any LLM call). Adding a tick-path LLM call that fires on idle
  breaks the quota guarantee (the test asserts `FakeClaude.calls == 0` on idle paths).
- **One definition of the change.** "What did the agent change?" is answered
  ONCE, mechanically, by `devclaw/task_change.py`: when the run ends the host
  stages everything left in the workspace and commits it, and every consumer —
  each gate, the change-size projection, the advisory checks, and delivery —
  reads that `pre_run_sha..post_run_sha` range. Never re-derive it. Two
  components used to compute it independently and silently disagreed: delivery
  shipped 4 files / +179 while the gates judged 1 / +32, because the gates saw
  only what the agent chose to record and what made it record was a sentence in
  a worker skill (#630). A span that cannot be determined fails the always-hard
  `materialize` gate CLOSED; an empty span is an explicit no-change outcome, not
  a silently-passing gate.
- **Single writer to state.** Only the **TaskQueue** mutates task rows; `StateStore` is
  an append-only event log, views are projections. Goal state is owned by `GoalStore`
  and (as of Tranche 1) lives in SQLite in the same `devclaw.db` — `goal_status`,
  `goal_steering`, `goal_log`, `goal_deliveries`, `goal_phase_history`, plus
  goal-transcending `project_docs` (the repo brief, keyed by workspace path).
  (`goal_docs` was dropped by the #616 cutoff: every kind it held died with the
  host-cognition chain in the 008 shrink.)
  `STATUS.md`/`log.md`/`inbox.md`/`deliveries.md`/`RUN_SUMMARY.md`
  are generated **views** — human- and rollback-readable, never read back for
  decisions. That last clause was aspiration until #617: the store parsed those
  views back into rows on eight read paths, which made whoever last touched a
  markdown file a second writer the CAS below does not cover. The one-shot
  pre-#617 ingest ran on the production DB and was deleted (2026-08-29 prune);
  `tests/test_views_never_read_back.py` holds the line structurally. Mutation is NOT heartbeat-exclusive: `steer_goal`/`resume_goal`/`cancel_goal` write from
  the MCP-tool call path too, concurrently with the heartbeat — `GoalStore.transition()`
  is the CAS'd choke point (`devclaw/goal/transitions.py`'s `LEGAL` table) that makes
  that safe: a stale-snapshot write raises `TransitionConflict` and is abandoned rather
  than silently clobbering the other writer. No upstream layer caches either.
- **"Done" is a proposal, gated on grounded evaluation.** The planner's `done` triggers
  a read-only `review_repository` against the firmed `done_when` + `stub_acceptable`; the
  goal closes **only if the evaluator confirms `achieved`**. Never gate completion on
  counting PRs or backlog items. Since spec 025 (merge-on-close, ruled 2026-08-29) a
  confirmed-achieved close also **squash-merges the goal's cumulative PR** — the one
  deliberate reversal of the #641 "a human merges" doctrine, at exactly one seam: a goal
  that cannot merge parks `mechanical:merge_failed` (with ONE bounded, pipeline-dispatched
  conflict-resolution self-heal first) instead of closing, nothing merges mid-flight
  (#486 intact), and a parked goal releases its project lane to the queued successor
  (skip-over, reversing spec 010 FR-008).
- **`done_when` is repository behavior, never delivery ceremony.** How the work ships,
  how many PRs it takes, which branch it lands on, whether/who merges it, and which
  issues or PRs get closed are NOT completion criteria — the evaluator drops them at
  decomposition (`devclaw/prompts/goal-evaluator.md` step 1a) and names the drop in its
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
  source; the goal-level done-gate re-catches its findings and owns the
  close-and-merge (spec 025), with the human reviewing merged work post-merge and
  revert as the remedy; under `strict` it is consulted and fails closed exactly as before.
  The browser-E2E gate stays dial-able — under `trust` a surviving finding
  advises-and-ships (loud + surfaced in the PR, post-merge human review is the
  backstop) instead of wedging; under `strict` it fails closed. The done-gate's verdict is owned by the
  ``done_when`` contract in both modes; its *structural* axis rides the same
  dial (under ``trust`` reported concerns advise-and-ship as follow-ups on the
  close, under ``strict`` they hold it open), and a done-gate that refuses to
  close the same goal 3 rounds in a row **with the satisfied-clause count
  flat** parks it for the owner (``donegate_churn``) instead of re-advancing
  forever — a round that beats the best count seen restarts the counter
  (`goal_status.donegate_progress`), so a converging goal is never parked as
  churn. The
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
├── server/          MCP surface — tools/ (@mcp.tool, split by domain), http.py + routes/ (routes/SSE), lifecycle.py (auth+serve)
├── goal/            durable goal layer — the facade, the heartbeat tick, the done-gate evaluator, the store, dispatch, merge, notify
├── engine/          execution — sandcastle.py (docker run --rm, prod), host.py, stub.py
├── delivery/        commit → branch → push → PR; deploy.py (Tailscale); repo.py (gh repo create)
├── quality/         gates past green tests — the self-contained fail-closed gate (own prompts/ + README), pre-PR adversarial review, browser_gate, reachability
├── loom/            engine-agnostic substrate — limits, test_integrity, trace
├── prompts/         system prompts as .md files (load_prompt(slug)); the 3 gate prompts live in quality/prompts/
├── task_change.py   ONE mechanical answer to "what did the agent change?" (spec 013)
├── config.py        the single doorway for DEVCLAW_* env config (one home, one default, one parse)
├── queue/           TaskQueue's mixin modules — settle.py (execute/settle path), admission.py (memory + breaker brakes)
├── cognition.py · llm_call.py · state_store/ · task_queue.py · project_registry.py · cli.py · …
runner/runner.py   the in-sandbox worker harness — drives the ACP agent via acp_client.py; line-delimited JSON on stdout
.sandcastle/Dockerfile       per-task sandbox image
docs/                        architecture + flows + env + runbooks (start at docs/INDEX.md)
tests/                       pytest — fully stubbed (no docker, no claude)
evals/                       stub e2e suite + real-pipeline harnesses
```

## Run the tests

```bash
pip install -e ".[dev]"
pytest        # ~1150 tripwire tests, all stubbed — no docker, no claude; ~23s (-n auto)
ruff check .  # pyflakes + syntax errors only; CI gates it
mypy          # type check (config in pyproject [tool.mypy]); CI gates it too
```

Engine modes (`DEVCLAW_ENGINE`): **unset** = the worker runner in a per-task docker
sandbox (production); `host` = the runner on the host, no sandbox (dev/CI); `stub` =
deterministic, no docker/no claude (the mode the test suite and `evals/run_all.py`
use). For the real pipeline (a logged-in `claude` + docker), follow
[`docs/runbooks/live-shakedown.md`](./docs/runbooks/live-shakedown.md).

## Conventions

- **Conventional-commit messages** (`fix(queue): …`, `feat(cognition): …`).
- **The suite is a tripwire net, not a coverage instrument** (ruled 2026-08-29,
  tests-to-tripwires prune): a PR ships a test ONLY when it touches an
  autonomous-operation invariant — zero-token idle, fail-closed gates,
  CAS/single-writer, OAuth strip + sandbox fence, pause/brake machinery, the
  materialize span, doctor seeded-faults, structural guards. Ordinary behavior
  changes ship NO test; the live instance + done-gate + post-merge review are
  their regression surface, and cognition quality is measured by evals.
  **The ratchet is symmetric** (2026-08-27): a PR that removes behavior removes
  that behavior's tests in the same PR; never mint an instance-test — extend
  the class test. Net-LOC is reported on every `/ship` — informational, never
  a gate.
- **A PR that changes persisted state shape or in-repo boilerplate ships its
  doctor check** (spec 016 FR-014) — the deployed-instance sibling of the
  named-regression-test rule: the stubbed suite guards the code, doctor guards
  the running instance, and drift the suite structurally cannot see (#641's
  class) gets a named check + seeded-fault test in the same PR. Checks live in
  `devclaw/doctor/checks_instance.py` / `checks_project.py`.
- **Branch per change**; open a PR, don't push to `main`.
- **`ruff check .` clean before the PR** — a narrow correctness gate (`F` + `E9`),
  not a style one. CI runs it alongside the suite.
- **Keep `docs/` honest.** If a change makes a doc wrong, fix the doc in the same PR
  and update its currency tag in [`docs/INDEX.md`](./docs/INDEX.md). A stale doc that
  looks current is worse than no doc.
- **Repo gold standard** ([REPO-STANDARD.md](https://github.com/lifekit-hq/.github/blob/main/REPO-STANDARD.md),
  adopted 2026-08-29, #695): squash-only merges, delete-branch-on-merge, the
  `P1`/`P2`/`needs-refinement`/`devclaw-ready` labels, plus the conventions above.
  Deliberate divergences: branches are `<type>/<slug>` with no issue number
  (speckit-driven work isn't always issue-backed); no husky-style pre-commit hook —
  the same gates run in CI plus the `/ship` ritual and `.claude/hooks`; milestones
  aren't used — the speckit spec is the unit of direction and issues carry priority
  labels; PR bodies state verification via the `/ship` ritual (suite result + the
  named regression test) rather than a titled Validation section; the root
  carries `AGENTS.md`/`ARCHITECTURE.md` — devclaw's own machine-maintained
  onboarding doc set, which the standard explicitly accepts.

## The dev harness (`.claude/`)

This repo carries a Claude-Code project harness for developing devclaw itself
(distinct from layer 5's model-agnostic `.agent/skills/`, which is product):
`.claude/rules/` (testing · git-workflow · cognition-prompts · speckit-workflow —
auto-loaded, the operational detail this file deliberately doesn't carry;
speckit-workflow is the anti-drift pipeline since 2026-08-13: every
behavior-changing change starts `/speckit-specify` → `/speckit-clarify` →
plan → tasks → implement, specs landing in `specs/` and the machinery in `.specify/`, no implementation before clarify,
with the constitution (`.specify/memory/constitution.md`) as the invariant
statement specs are checked against; `docs/proposals/` + `docs/decisions/`
are frozen history),
`.claude/commands/ship.md` (the pre-PR ritual as `/ship`),
`.claude/hooks/` (docs-reminder + a main-branch guard that blocks commit/push on main —
escape hatch: prefix `DEVCLAW_ALLOW_MAIN=1`), and `.claude/skills/` (docs-audit,
live-shakedown).

## Where to look next

- [`docs/INDEX.md`](./docs/INDEX.md) — every doc, one-line purpose, currency tag. **Read this before trusting any other doc.**
- [`docs/architecture.md`](./docs/architecture.md) — the mental model + the locked 5-layer contracts and invariants.
- [`docs/flows/task-execution.md`](./docs/flows/task-execution.md) — the temporal trace of one task, every hop.
- [`docs/reference/env-vars.md`](./docs/reference/env-vars.md) — every env var, grouped.
