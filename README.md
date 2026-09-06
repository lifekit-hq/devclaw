# devclaw

**An autonomous software-development loop you supervise instead of operate.**

Coding agents are excellent at *tasks* and unreliable at *goals*. Prompting one task at a time makes you the project manager of your own tooling; pointing an agent at a big objective and walking away produces the opposite failure - overnight loops that drift, ship green-tests-but-broken work, or burn the night retrying a doomed step. devclaw is the layer between those two failure modes: you hand it a **durable goal with verifiable completion criteria**, and a self-executing loop carries it - plan → sandboxed execution → verification gate → evaluate → iterate - across days and many PRs, with hard brakes (retry caps, a no-progress watchdog, `stalled` / `needs_human` verdicts) so it never optimizes into the void. When it needs a human decision it blocks loudly with the exact question; everything else it carries alone.

It sits behind MCP. An [OpenClaw](https://openclaw.ai) waiter agent translates chat into tool calls; devclaw never talks to the user. Cognition is always `claude` over a Pro/Max OAuth session - **no `ANTHROPIC_API_KEY`, no metered billing**: a stray key is stripped at every host and sandbox call site rather than honored.

![devclaw operator console - portfolio overview](./docs/assets/console-overview.png)

![devclaw operator console - a blocked goal stating exactly what it needs](./docs/assets/console-goal-detail.png)

*Both screenshots are the live operator console (`/console`) driving real repositories.*

## Run it

Prerequisites: Python 3.11+, docker, a logged-in `claude` CLI, and a `GITHUB_TOKEN` with push + PR access for delivery. The per-task sandbox receives a disposable copy of the OAuth identity pair (`.credentials.json` + `.claude.json`) and nothing else from `~/.claude`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
npm install -g @agentclientprotocol/claude-agent-acp
docker build -t devclaw-sandbox:latest -f .sandcastle/Dockerfile .

DEVCLAW_TRANSPORT=stdio devclaw-mcp                    # MCP over stdio (local dev)
DEVCLAW_TRANSPORT=http DEVCLAW_PORT=8000 devclaw-mcp   # MCP at /mcp, the operator console at /console
```

`devclaw-mcp` is the server; `devclaw` is the control-plane CLI (`devclaw projects …`, `devclaw trace …`, `devclaw doctor`, `devclaw schedule …`). Configuration is environment-only: copy [`.env.example`](./.env.example) to `.env` (shell and systemd env win over it). Every variable, with its default, is in [`docs/reference/env-vars.md`](./docs/reference/env-vars.md) - pinned to `devclaw/config.py` by a sync test.

| `DEVCLAW_ENGINE` | Engine | Isolation | Use |
|---|---|---|---|
| *(unset)* | the worker runner in a per-task **docker sandbox** | full | production |
| `host` | the worker runner on the host, no container | **none** - the agent has the host filesystem | dev / CI where docker is unavailable |
| `stub` | deterministic stub, no sandbox, no `claude` | n/a | the test suite and `evals/` |

Tests are fully stubbed - no docker, no `claude`:

```bash
pip install -e ".[dev]"
pytest          # the tripwire suite (parallel by default)
ruff check .    # pyflakes + syntax errors; CI gates it
mypy            # zero-error baseline; CI gates it
```

The real pipeline (a logged-in `claude` driven over ACP inside the docker sandbox) is exercised by the layered runbook in [`docs/runbooks/live-shakedown.md`](./docs/runbooks/live-shakedown.md); the post-deploy invariant check is the read-only `doctor` verb ([`docs/runbooks/doctor.md`](./docs/runbooks/doctor.md)).

## How it works

Five layers sit below the user, and only the last one is an agent harness in the technical sense. The canonical statement, with per-layer contracts and the locked invariants, is [`docs/architecture.md`](./docs/architecture.md).

| Layer | Code | Role |
|---|---|---|
| **MCP surface** | `devclaw/server/` | tools, HTTP routes, the console SPA, auth - pure protocol |
| **GoalService + heartbeat** | `devclaw/goal/` | the goal state machine and the ~15-minute tick |
| **Cognition callers** | `devclaw/goal/evaluator.py`, `intake_readiness.py`, `admission_lint.py` | one-shot `claude --print` prompts: done-gate evaluation, intake readiness, admission lint |
| **TaskQueue + engine** | `devclaw/task_queue.py`, `devclaw/queue/`, `devclaw/engine/` | dispatch, concurrency, `docker run --rm` per task, the settle and gate path |
| **Worker harness** | `runner/runner.py` (inside the sandbox) | the agent turn-loop: drives `claude-code` over ACP, applies skills and hooks, runs the fast `verify_cmd` pre-check |

**One heartbeat, per goal:**

1. **Cheap check** (zero tokens) - poll the in-flight action with a SQLite read. An idle goal and a still-running goal cost no `claude` calls; this is a tested invariant.
2. **Evidence** (zero tokens) - on a finished action, record what it actually shipped. "What did the agent change?" is answered once, mechanically, from the `pre_run_sha..post_run_sha` span every gate reads.
3. **Advance** (zero tokens) - build a mechanical brief and dispatch the next increment. The worker plans in-sandbox with speckit; the host never plans.
4. **Gates** - the project's own CI rollup on the delivered head is the verdict of record; the in-sandbox `verify_cmd` is only a pre-check. Verification fails closed: a gate crash is a failure, not an approval.
5. **Done-gate** - the worker's `done` is a proposal. It triggers a read-only repository review judged against the goal's `done_when` contract, and the goal closes only if the evaluator confirms `achieved`. A confirmed close squash-merges the goal's cumulative PR; a goal that cannot merge parks for the owner instead of closing.

**The issue is the contract.** A goal points at graded GitHub issues; the ask and acceptance criteria are fetched live per dispatch, and an admission lint refuses a clause the sandbox cannot satisfy before anything is dispatched. A human-gated block carries a typed Problem with bounded options and a default; `decide` and `correct_implementation` resolve it, recording a Decision.

**Surfaces.** The MCP tools, grouped: tasks (`dispatch_task`, `get_status`, `list_tasks`, `get_events`, `cancel_task`); goals (`create_goal`, `get_goal`, `list_goals`, `steer_goal`, `resume_goal`, `tail_goal`, `get_trace`, `cancel_goal`, `set_goal_strictness`, `set_goal_verify_cmd`); problems (`list_problems`, `decide`, `correct_implementation`); projects (`register_project`, `list_projects`, `project_status`, `update_project`, `link_goal`, `delete_project`, `onboard`, `create_repo`, `delete_repo`); intake (`file_intake`, `regrade_intake`, `grade_backlog`); operations (`doctor`, `get_scorecard_metrics`, `set_run_schedule`, `get_run_schedule`, `set_operator_hold`, `set_quiet_mode`, `set_max_concurrent`, `set_max_host_cognition`, `clear_usage_pause`, `list_suppressed_pings`); deploy (`deploy_project`, `deploy_status`, `list_deploys`, `stop_deploy`). The same control plane is reachable from the `devclaw` CLI and the React console under `console/` (`npm --prefix console run build`). Tool signatures live in `devclaw/server/tools/`; the waiter's menu is described in [`docs/runbooks/vps-waiter-deploy.md`](./docs/runbooks/vps-waiter-deploy.md).

## Status

Every capability claim carries its evidence tier where it is made: **production** = exercised live on the lifekit repositories; **experimental** = built, not load-bearing; **paper** = specified, not built.

| Capability | Tier | Evidence |
|---|---|---|
| Durable goal loop: in-sandbox speckit planning, fail-closed gates, grounded done-gate, merge-on-close | **production** | drives the lifekit repos daily; merge-on-close proven live 2026-08-29 (spec 025) |
| Per-task docker sandbox, OAuth-only cognition, key stripping | **production** | `devclaw/engine/sandcastle.py`; the strip is a tripwire test |
| Pause-and-resume on usage limits and expired auth; mechanical blocks self-heal; no-progress watchdog | **production** | `devclaw/loom/limits.py`, spec 004; zero tokens while paused |
| Issue-as-contract intake, readiness grading, admission lint, typed Problems | **production** | specs 019, 024, 031; `devclaw/intake.py` |
| Operator console and CLI | **production** | `console/`, `devclaw/cli.py` |
| Event-driven triggers (GitHub webhooks wake the loop; the heartbeat is the fallback) | **production** | spec 023; [`docs/runbooks/webhooks.md`](./docs/runbooks/webhooks.md) |
| Self-deploy of devclaw's own merged main, probe + one auto-rollback | **production** | spec 005; [`docs/runbooks/devclaw-self-deploy.md`](./docs/runbooks/devclaw-self-deploy.md) |
| Scorecard ratchet (first-pass rate, decided-merge rate, wedge-free window) | **production, informational** | spec 018; the gate reports and never blocks |
| Browser-E2E gate and adversarial diff review | **production, dial-able** | consulted under `strict`; under the default `trust` the done-gate carries the review (spec 001) |
| Live validation lane (`validate_product`, `qa` goals, deploy-triggered smoke) | **experimental** | spec 015; armed per project through the manifest's `validation` key, off by default |
| Deploy hosting over Tailscale | **experimental** | built, never run end-to-end in production; the launcher hosts Python or static repos only ([#401](https://github.com/lifekit-hq/devclaw/issues/401)) |
| Swapping `claude-code` for another ACP agent | **paper** | the seam exists (`DEVCLAW_ACP_COMMAND`, plain-markdown skills, bash hooks) and only `claude-code` has ever run through it |
| Autonomous issue self-dispatch | **paper** | spec 007, parked |
| Worker file memory across increments | **paper** | spec 034, drafted and deliberately not armed |

**Measured, not vibes.** The ratchet that defines "finished" is a first-pass rate of at least `DEVCLAW_RATCHET_FIRST_PASS`, a decided-merge rate of at least `DEVCLAW_RATCHET_DECIDED_MERGE`, and a wedge-free window of `DEVCLAW_RATCHET_WINDOW_DAYS`, read from `get_scorecard_metrics`. The latest read is [`docs/audits/2026-09-05-scorecard.html`](./docs/audits/2026-09-05-scorecard.html): decided-merge passes, first-pass fails, wedge-free fails. Engineering health is tracked the same way in [`docs/audits/eng-health.md`](./docs/audits/eng-health.md). Honest scope: small-to-medium machine-verifiable backend tasks; UI work and ambiguous specs still need a human.

## What this is NOT

- **Not a chatbot.** It is a backend service the OpenClaw waiter calls.
- **Not a general assistant.** It executes software-development goals, nothing else.
- **Not a rebuild of Claude Code.** `claude-code` + `claude-agent-acp` is the agent harness inside the sandbox; devclaw is the orchestration above it, and it never encodes knowledge about a project's code - it supplies facts and tools and reads the mechanical verdict.
- **Not novel reasoning.** The intelligence is Claude's, used twice: as a one-shot reasoning API for done-gate evaluation, and as the interactive worker inside the sandbox. devclaw is the state machine, scheduler, persistence and gates that let one goal span days.
- **Not infallible.** Autonomous means "does not need the next prompt", not "cannot ship broken work". The done-gate is Claude judging Claude's output; what bounds that circle is mechanical evidence - the project's CI on the exact delivered head, the materialized change span, the browser gate - and the first-pass rate above is the number that says how well it works today.
- **Not a workstation sandbox.** The sandbox exists so an unattended run cannot reach the host; it is not a product for running agents on a developer machine.

## Docs

- [`docs/INDEX.md`](./docs/INDEX.md) - every doc, its purpose, and a currency tag saying whether it was verified against the code. **Read this before trusting any other doc.**
- [`docs/architecture.md`](./docs/architecture.md) - the mental model and the locked five-layer contract.
- [`docs/flows/`](./docs/flows/) - one task's journey, how dispatches become PRs, the issue pipeline end to end.
- [`docs/reference/`](./docs/reference/) - env vars, the `devclaw.json` project manifest, the intake shape.
- [`docs/runbooks/`](./docs/runbooks/) - live shakedown, doctor, webhooks, VPS and self-deploy.
- [`CLAUDE.md`](./CLAUDE.md) - the working contract an agent reads before touching this repo; [`AGENTS.md`](./AGENTS.md) and [`ARCHITECTURE.md`](./ARCHITECTURE.md) are the machine-maintained onboarding set devclaw writes into every repo it drives, including its own.
- [`specs/`](./specs/) - the speckit specs; their `Status` headers are the direction record.
- [`CHANGELOG.md`](./CHANGELOG.md) - generated by release-please from conventional commits.

## License

[MIT](./LICENSE). Copyright 2026 Denys Sychov.
