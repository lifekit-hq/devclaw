# CLAUDE.md — devclaw harness contract

The first doc an agent reads before touching this repo. [`README.md`](./README.md)
is the narrative; this file is the working contract. When the two disagree, the
code wins — cross-check before you trust either.

## What devclaw is

**Some Python for what is determined, a model for what is reasoned, and one
line between them** (spec 046, ruled by Denys 2026-09-10/13). You hand devclaw
a GitHub issue; a session — Claude Code in a per-session docker sandbox — reads
the issue as the contract, plans the repository's own way
(`.devclaw/workflow.md`, derived on the first run), lands one
reviewable increment on the goal's branch, and hands back one exit line. The
host reads the world (the PR, its CI, the threads) every ~15 minutes and spawns
a session only when the world moved. Done is never the agent's word: a proposal
gets a fresh read-only review session judged mechanically, and a confirmed
close squash-merges the goal's one PR. It sits behind MCP; an OpenClaw waiter
translates chat into tool calls. Cognition is `claude` over Pro/Max OAuth — no
API key, no metered billing.

## The north star

**devclaw runs 24/7 on planned work, nights come out clean, and it stops on
facts instead of waiting for the owner.** It fails three ways — *stopped when it
shouldn't*, *ran and produced garbage*, *ran but needed the owner* — and every
change names the one it moves. **The owner acts only on decisions**: a
`decide`, or a comment mentioning the bot on the issue or PR. Any other owner
action a change adds is a defect. The checklist is `.claude/rules/north-star.md`.

## The eight pillars (spec 046 — the direction memory)

1. **Two actors, one line.** Python decides what is determined; the model
   reasons everything else. The goal layer fits one afternoon of reading —
   `tests/test_goal_layer_stays_readable.py` fails the build past 1,500 lines.
2. **Software owns five things.** Safety (sandbox, credential strip). Money
   (quota pause, sessions-per-day). State (single writer, one transaction).
   Verdict (CI green on the delivered head + one done-gate). Protocol (issue in,
   PR out, `decide`, `cancel`). Python outside these needs a written reason.
3. **The issue is the contract.** Its `## Done when` / `## Acceptance` section
   is what the done-gate judges, read live. The host never grades or rewrites it.
4. **The session is the engineer.** One prompt (`devclaw/prompts/session.md`),
   the REPOSITORY's own planning harness — devclaw bakes none and names none.
   The first run on a repo derives `.devclaw/workflow.md` from it; every later
   session reads it. Recovery = read the repo, the PR, the CI and continue.
   Exits: `DELIVERED` / `DONE` / `BLOCKED` / `NOTHING`.
5. **The host holds no derived state.** Goals, decisions, the quota pause, and
   the last world fingerprint each session was given. No failure kinds, budgets,
   holds or counters — a fingerprint is an observation, a hold was a judgment.
6. **Zero tokens when nothing changed.** A session spawns only when the
   fingerprint moved (`tests/test_goal_tick.py` asserts zero sessions on idle).
7. **Done is never the agent's word.** The review session's JSON is validated
   mechanically: every clause satisfied with evidence, or not achieved.
8. **The owner acts only on decisions.** A failed night becomes a fact in the
   prompt or a line in a skill — never a host mechanism, never an owner verb.

## The layer map

| Layer | Code | A change belongs here if it is about… |
|---|---|---|
| MCP surface | `devclaw/server/` | a tool, a route, auth, the console — pure protocol |
| Goal layer | `devclaw/goal/` | the tick rule, the world fingerprint, the two prompts, the done-gate, GitHub reads/verbs |
| Queue + engine | `devclaw/task_queue.py`, `devclaw/queue/`, `devclaw/engine/`, `devclaw/gates.py`, `devclaw/delivery/` | one session: place the branch, run, classify the exit, the four gates, deliver |
| Worker harness | `runner/runner.py` (inside the sandbox) | the ACP turn-loop, the skill bundle, the in-session verify loop |

The import order is a declared fact: `[tool.importlinter]` in `pyproject.toml`
states it and `lint-imports` fails a new upward edge in CI. Only the goal layer
reads GitHub as state.

## Load-bearing invariants — DO NOT VIOLATE

- **OAuth only.** `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are stripped at
  the engine (`devclaw/engine/sandcastle.py`, `devclaw/engine/host.py`) and the
  runner. Every credential that crosses a hop lives in ONE registry,
  `devclaw/credentials.py`; the runner never names a credential.
- **Model-agnostic worker layer.** Skills are plain markdown in `runner/skills/`
  (one home, baked to `/opt/devclaw/skills/`); hooks are bash; the agent command
  is the one swap seam (`DEVCLAW_ACP_COMMAND`). A missing bundle fails LOUD.
- **The sandbox runs what CI runs before the session ends.** `.devclaw/verify`
  is derived by the session from the project's workflows on its first run; the
  runner hands a red run back to the same session (`DEVCLAW_VERIFY_ROUNDS`).
  A red CI on a delivered head is therefore an environment gap: the goal stops,
  never retries (ruled 2026-09-13).
- **The first run on a repository leaves a manifest, in git.** `.devclaw/verify`
  (how it verifies) and `.devclaw/workflow.md` (how it plans, builds and ships)
  are derived from the repository itself and must be TRACKED when the session
  ends — the runner reads the git index, not the working tree, so a `.gitignore`
  that swallows `.devclaw/` fails the gate instead of silently discarding the
  manifest (ruled 2026-09-14). devclaw bakes no planning scaffold and names no
  planning tool; a repo with no harness gets an explicit `none`.
- **One definition of the change.** `devclaw/task_change.py` materializes the
  span once (`pre_run_sha..post_run_sha` less what the base branch carries) and
  every gate and delivery read it. An undeterminable span fails closed.
- **Single writer to state.** Only the TaskQueue mutates task rows; the goal
  layer writes goals, decisions and `last_seen` and submits tasks inside one
  store transaction. GitHub carries everything else: devclaw's own records on a
  thread are marked `<!-- devclaw:<kind> … -->` and are never instructions.
- **No retries.** A red CI or a done-gate refusal stops the goal with the fact
  posted on the thread; a blocked goal releases its project lane; only a newer
  comment mentioning the bot (or `decide`) wakes it. `INTERRUPTED` (timeout,
  quota, red local verify) resumes — that is the session's own unfinished work.
- **Usage limits pause-and-resume.** A quota, auth or provider-outage failure
  pauses the account, snapshots the work and requeues (`devclaw/loom/limits.py`).
  Only agent- or harness-origin text is classified; a session's own words never.
- **Fail loud, never silent.** A gate crash is a failure; a delivery that cannot
  push fails; an unreadable review verdict never passes.

## Run the tests

```bash
pip install -e ".[dev]"
pytest         # the tripwire suite, all stubbed — no docker, no claude
ruff check .   # pyflakes + syntax errors; CI gates it
mypy           # zero-error baseline; CI gates it
lint-imports   # the layer order as a contract; CI gates it
```

```
devclaw/
├── server/          MCP surface — tools/ (@mcp.tool), routes/, lifecycle.py (auth + serve), http.py
├── goal/            tick.py (the rule) · world.py (the fingerprint) · prompts.py · donegate.py · github.py · service.py
├── prompts/         session.md + done-gate.md — the two prompts the host writes
├── queue/           settle.py (one session, executed and settled) · admission.py (memory + breaker)
├── engine/          sandcastle.py (docker run --rm), host.py, stub.py, workspace.py (one goal, one checkout)
├── delivery/        commit → push → the goal's one PR
├── gates.py         verify · materialize · change_class · integrity — always hard, fail closed
├── task_change.py   ONE mechanical answer to "what did the session change?"
├── state_store/     tasks · events · goals · decisions · meta (the control flags)
├── loom/            limits (the pause classifier), test_integrity, untrusted (the prompt fence)
├── credentials.py · probes.py · config.py · task_queue.py · project_registry.py · doctor/ · cli.py
runner/runner.py     the in-sandbox worker harness — drives the ACP agent; skills/ is the one home of worker instructions
.sandcastle/Dockerfile   the sandbox image (bakes runner/, skills/, the speckit scaffold)
tests/               pytest — fully stubbed
```

## Conventions

- **Speckit first** for behaviour-changing work (`.claude/rules/speckit-workflow.md`);
  every spec names its north-star case. Docs-only and test-only changes need no artifact.
- **Conventional commits**; branch per change; squash merges; the PR's own CI
  green before merge (`.claude/hooks/merge-verdict-guard.py`).
- **The suite is a tripwire net**, not a coverage instrument
  (`.claude/rules/testing.md`): a PR ships a test only when it touches an
  autonomous-operation invariant, and a PR that removes behaviour removes its tests.
- **Keep `docs/` honest**: a diff that makes a doc wrong fixes it in the same PR
  and updates its currency tag in `docs/INDEX.md`.

## The dev harness (`.claude/`)

`.claude/rules/` (auto-loaded: testing · git-workflow · cognition-prompts ·
speckit-workflow · north-star), `.claude/commands/ship.md` (the pre-PR ritual
as `/ship`), `.claude/hooks/` (the main-branch guard, the north-star-case guard
at `gh pr create`, the merge-verdict guard at `gh pr merge`, the docs
reminder), and `.claude/skills/` (north-star, root-cause, docs-audit, the
vendored speckit-* commands).

## Where to look next

- [`specs/046-devclaw-v2/spec.md`](./specs/046-devclaw-v2/spec.md) — the design: pillars, the tick rule, the prompts, twenty use cases.
- [`docs/INDEX.md`](./docs/INDEX.md) — every doc, one-line purpose, currency tag.
- [`docs/architecture.md`](./docs/architecture.md) — the mental model and the locked contracts.

## Memory (vault)

Durable knowledge about this project lives in the vault, not in provider memory: `~/memory/projects/devclaw/` - `plan.md` (facts), `STATUS.md` (in-flight, only while parked), `log.md` (dated events). Read `plan.md` + `STATUS.md` when starting work here; verify live state live (`gh`, `docker ps`). Write durable decisions and gotchas back to those pages in the same session (contract: `~/memory/README.md`). Claude Code auto-memory is disabled by policy (`CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`); Codex/other agents follow the same pointer via `AGENTS.md`.
