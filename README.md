# devclaw

**An autonomous software-development loop you supervise instead of operate.**

Coding agents are excellent at *tasks* and unreliable at *goals*. Prompting one task at a time makes you the project manager of your own tooling; pointing an agent at a big objective and walking away produces the opposite failure — overnight loops that drift, ship green-tests-but-broken work, or burn the night retrying a doomed step. devclaw is the layer between those two failure modes: you hand it a **durable goal with verifiable completion criteria**, and a self-executing loop carries it — plan → sandboxed execution → verification gate → evaluate → iterate — across days and many PRs, with hard brakes (retry caps, a no-progress watchdog, `stalled`/`needs_human` verdicts) so it never optimizes into the void. When it genuinely needs a human decision it blocks loudly with the exact question; everything else it carries alone. The prompt is a component inside the loop, not the point of control.

![devclaw operator console — portfolio overview](./docs/assets/console-overview.png)

**"Done" is never the agent's word for it.** A worker proposing *done* only triggers a read-only repository review, judged by a separate grounded evaluator against the goal's own `done_when` criteria — and verification **fails closed**: a gate crash is a failure, not an approval. The same honesty runs the other way: a worker facing a provably futile contract refuses to burn quota re-attempting it and blocks with an explanation and concrete options instead —

![devclaw operator console — a blocked goal stating exactly what it needs](./docs/assets/console-goal-detail.png)

*Both screenshots are the live operator console (`/console`) driving real repositories — not a mockup.*

**Measured, not vibes.** The first pass-rate probe — real docker sandbox, real `claude`, one production .NET repo (`lifekit-dashboard`) — shipped **5/5 tickets gate-verified**: four net-new API features and one hardening fix, delivered as PRs, **+19 net-new tests, zero existing tests deleted/skipped/weakened, zero regressions**. That is a single-repo, n=5, gate-verified-**at-ship** measurement — the precursor to the formal **v0.1 proof** (10 tickets across ≥2 repos, scored *merged-without-rework* ≥6/10), whose verdict is still **pending** (see [`ROADMAP.md`](./ROADMAP.md) and `evals/`). Honest scope: small-to-medium machine-verifiable backend tasks; UI and ambiguous specs still need a human.

> **DevClaw is the chef.** The waiter — an [OpenClaw](https://openclaw.ai) chat agent, the user-facing assistant — takes orders and translates chat into structured MCP tool calls; devclaw cooks. It owns the **craft of software development as a service**: durable goals, sandbox execution (via [OpenHands](https://github.com/All-Hands-AI/OpenHands), the open-source coding-agent SDK), pre-PR adversarial review, gate verification, and grounded direction evaluation — planning happens in-sandbox, where the worker scopes each advance with speckit (`specs/*/` artifacts committed to the repo). (An experimental Tailscale deploy path exists but is not yet load-bearing — it can't yet host the owner's stack; see #401.) devclaw never talks to the user directly.

Cognition is always `claude` over a Pro/Max OAuth session — **no `ANTHROPIC_API_KEY`, no metered billing** for autonomous runs.

It is **not** a chatbot and **not** a rebuild of OpenHands. OpenHands owns the agent loop (tool use, code edits, git). DevClaw owns everything *around* it: durable goals + direction evaluation, state, isolation, observability, and delivery. (Durable deploy is an experimental path, not yet in production — #401.)

```
Denys
  │  (chat / voice / Telegram)
  ▼
OpenClaw waiter agent          ← translates chat ↔ MCP, doesn't decide
  │
  ▼
DevClaw (the chef — this repo, FastMCP)
  ├── goal/    durable goals → heartbeat tick → advance-dispatch + evaluate
  ├── server/  FastMCP stdio + streamable-HTTP, the /console SPA + SSE, auth
  ├── loom/    reusable orchestration core (failure classification, test integrity)
  ├── advance_brief.py · review_gate.py · delivery.py · deploy.py · …
  └── sandcastle_runner — `docker run --rm` per task; RO ~/.claude mount; destroyed on exit
        │
        ▼
  OpenHands (Python SDK) — agent loop, runs `claude` via ACP (Pro OAuth)
```

### Layered view — where the agent harness actually lives

> The canonical layer reference, with per-layer contracts and invariants, is **[`docs/architecture.md`](./docs/architecture.md)**. This README section is the high-level summary. Architectural changes are judged against the doc.

Five distinct layers below the user, and only one of them is an agent harness in the technical sense (a turn-loop hosting tool calls).

| Layer | What it is | Harness? |
|---|---|---|
| **MCP surface** (`devclaw.server`) | HTTP/stdio protocol exposing tools (`create_goal`, `get_goal`, `steer_goal`, …) | No — protocol |
| **GoalService + heartbeat** (`devclaw.goal`) | State machine + scheduler; owns the lifecycle (goals are `executing` from birth — spec 008); ticks every ~15 min; reads goal state (SQLite, since Tranche 1 — `.md`/`.yaml` files are generated views) and decides the next move per goal | No — orchestrator |
| **Cognition callers** (evaluator, summarizer, scope grill) | One-shot `claude --print` invocations with baked prompts + goal state; return structured output the loop parses | Borderline — Claude as a reasoning API, not an interactive agent |
| **TaskQueue + sandcastle engine** (`devclaw.engine`) | Receives "do task X" → `docker run devclaw-sandbox(-dotnet):local <payload>`; streams stdout events back | No — container launcher |
| **Worker harness** (`runner.py` → `claude-agent-acp` → `claude-code` CLI + MCP servers, e.g. Playwright MCP) | The actual agent turn-loop. Tool calls (Read/Edit/Bash/browser), edits the repo, commits, exits | **Yes — the only true harness in the stack** |

DevClaw is mostly **plumbing + prompts** around that one worker harness. The reasoning is Claude's, borrowed via (a) one-shot cognition calls the loop makes for evaluation, and (b) the worker harness running interactively inside the sandbox — which also owns planning (speckit `specs/*/` artifacts, spec 008). The state machine, persistence, lifecycle, and gates are the real engineering — they let one goal span days, many PRs, many evaluator passes without the owner at the desk.

### Skills + hooks — two layers, one mechanism

The worker harness reads two complementary layers of doctrine each task:

| Layer | Lives in | Owned by | Purpose |
|---|---|---|---|
| **Universal** | `/opt/devclaw/skills/` + `/opt/devclaw/hooks/` (baked into the sandbox image from `openhands-runner/skills/` and `openhands-runner/hooks/` in this repo) | DevClaw | Cross-repo doctrine — quality bar, verify-gate coverage, commit hygiene. The runner prepends per-task-kind skill bundles to the goal; universal hooks run mechanical pre/post checks. |
| **Per-repo** | `<repo>/.agent/skills/` + `<repo>/.agent/hooks/` (alongside `AGENTS.md`) | The project | Project-specific notes — auth flow, migration commands, deploy steps. Agent-discovered (the universal `_common` skill tells it to `ls .agent/skills/`); per-repo hooks fire after universal ones with a `[name:repo]` tag. |

Same pattern as `AGENTS.md`: universal devclaw doctrine + per-repo project facts. The universal layer stays consistent across every cascade; the per-repo layer evolves at the project's own pace.

The universal layer is itself split by **nature**, not by kind:

- **Doctrine — always-on.** `_common.md`, the `_writes-code/*` tier (quality bar, verify-gate coverage, verify-iterate, repo-gate conflict, commit hygiene), and each `<kind>/*` tier. The runner concatenates these into the brief every task whether or not the agent thinks they apply — they're non-negotiable.
- **Craft — self-selected.** How-to references in `openhands-runner/skills/craft/` (e.g. `frontend-design`, `playwright`) baked to `/opt/devclaw/skills/craft/`. These are **not** concatenated; `_common` points the agent at the dir and it `ls`/`cat`s only the guides a task calls for (progressive disclosure). Same discovery mechanism as per-repo `.agent/skills/` — no tagging or conditional-loading logic, plain `ls` + read.

#### Model-agnostic invariants

The skill/hook system is deliberately neutral about which agent runs inside the sandbox. Today it's `claude-code` + `claude-agent-acp`; tomorrow it could be `codex`, `gemini-cli`, an open-source agent, anything that can read files and call tools. Keeping that true is an invariant: skills stay plain markdown (no model-specific frontmatter, no native `Skill(…)` calls), hooks stay bash `.sh` files invoked by `runner.py` (never a `settings.json`), cross-tool capability rides MCP rather than vendor wiring, and per-repo discovery is `ls .agent/skills/` + `cat` — any agent with file-read can consume all of it. Canonical statement + do-not-violate detail: [`docs/architecture.md`](./docs/architecture.md) §Invariants.

The day we swap claude-code for another harness, the entire skill/hook system survives — and the agent command is already a config seam, not a code change: set `DEVCLAW_ACP_COMMAND` (default `claude-agent-acp`; the runner shlex-splits it) to point at any ACP-speaking agent. The residual claude-coupling is the plumbing around the call — the `acp_env` vars, the `~/.claude` auth mounts, `DEVCLAW_EXEC_MODEL`'s claude model ids, the auth/rate-limit classifiers — plus baking the alternate binary into the sandbox image (see `docs/reference/env-vars.md`).

## The split

| Concern | Owner |
|---|---|
| Conversation with Denys | **OpenClaw waiter agent** (system prompt + tool calls) |
| Agent loop, sandbox coding, git | **OpenHands** |
| Direction eval, review gate, done-gate | DevClaw |
| Planning (speckit, in-sandbox) | The worker |
| Task/program state | DevClaw state store (SQLite) |
| Per-task isolation | DevClaw sandcastle runner (`docker run`) |
| Durable hosting / handoff | DevClaw deploy (Tailscale) — **experimental**, not yet hosting the owner's stack (#401) |
| Interface to the waiter | DevClaw FastMCP server |

The full rationale — including why OpenHands and sandbox isolation are **orthogonal** layers (the agent vs. the box it runs in), and why this calls `docker run` directly instead of depending on `@ai-hero/sandcastle` — lives in [`docs/decisions/0001-openhands-engine.md`](./docs/decisions/0001-openhands-engine.md).

## Layout

```
devclaw/
├── server/             # MCP server (FastMCP) — split by job:
│   ├── __init__.py     #   re-exports + load-order
│   ├── _state.py       #   FastMCP instance + long-lived services + env
│   ├── tools.py        #   every @mcp.tool decorator (the chef's menu)
│   ├── http.py         #   every @mcp.custom_route (console, SSE, /traces.json)
│   └── lifecycle.py    #   main() + serve loops + bearer-token auth middleware
├── goal/               # the durable goal layer (folded-in goalclaw):
│   ├── service.py      #   GoalService — the facade the server wires up
│   ├── tick.py         #   one heartbeat: check → advance-dispatch → done-gate (zero per-tick planner)
│   ├── evaluator.py    #   direction evaluation, grounded in deliveries.md
│   ├── store/          #   GoalStore — goal.yaml (facts) + SQLite status/steering/log/deliveries/docs (base · status · content)
│   ├── engine.py       #   in-process dispatch into the task queue
│   ├── merge.py · notify.py · summary.py · models.py
├── engine/             # everything that EXECUTES the work:
│   ├── __init__.py     #   the Engine protocol (one async callable)
│   ├── sandcastle.py   #   docker run --rm per task; events stream from the runner (production)
│   ├── claude_sdk.py   #   spike backend: claude --print inside the same sandbox
│   ├── host.py         #   host-side runner (no sandbox; testing only)
│   ├── stub.py         #   deterministic engine for tests + offline harness
│   ├── runner_io.py    #   shared stdout/event-stream parser
│   └── workspace.py    #   per-action pristine git checkout (devclaw owns it)
├── delivery/           # how shipped changes REACH the owner:
│   ├── __init__.py     #   engineer-authored commit → branch → push → PR
│   ├── deploy.py       #   Tailscale deploy hosting — experimental; launcher supports Python/static only (#401)
│   └── repo.py         #   gh repo creation + teardown (create_repo / delete_repo)
├── quality/            # gates that judge the work past the green test gate:
│   ├── __init__.py     #   pre-PR adversarial diff review (claude)
│   ├── eval_judge.py   #   failure-mode classifier across eval runs
│   └── evals.py        #   eval scoring (pure, used by harnesses)
├── prompts/            # every system prompt as a .md file (load_prompt(slug))
├── loom/               # reusable orchestration core (engine-agnostic substrate):
│   ├── limits.py       #   usage-/rate-limit failure classifier (pure)
│   ├── test_integrity.py # gate guard: flags deleted/weakened tests in a diff (pure)
│   └── trace.py        #   run-trace recorder (cognition, ticks, dispatches, deliveries)
├── advance_brief.py    # the mechanical (zero-LLM) brief each advance dispatch carries — the worker plans in-sandbox
├── cognition.py        # the LLM seam — Cognition protocol + Claude/Stub impls
├── elicitation.py      # scope-grill cognition (called via the scope_grill MCP tool)
├── state_store/       # SQLite: programs, tasks, append-only events (rows · control · core)
├── task_queue.py       # async task lifecycle, concurrency, on-settle hook → goal poke
├── project_registry.py # control plane: repos → driving goals → live status rollup
└── cli.py              # devclaw projects/trace/scorecard/schedule/cognition … (terminal face of the control plane)
openhands-runner/runner.py  # OpenHands SDK inside the sandbox; emits event/result lines
.sandcastle/Dockerfile      # per-task sandbox image
tests/                      # pytest — stubbed engine; no docker, no claude
docs/architecture.md        # the system doc — read before touching the runner/store/sandbox
```

DevClaw is all Python. The only language boundary left is the process boundary: `openhands-runner/runner.py` runs the OpenHands SDK *inside* the sandbox container, isolated from the long-running host process — it talks to the host over a line-delimited JSON protocol on stdout.

## MCP tools (the chef's menu)

| Tool | Does |
|---|---|
| `dispatch_task(kind, project_id, goal, …)` | One-shot task; `kind` ∈ `implement_feature` / `fix_bug` / `review_repository`. `project_id` names a registered project — devclaw resolves its workspace/repo from the registry (never a raw path), rejects an unknown project, and preflights the workspace before dispatch: a real git checkout runs; an absent one is auto-cloned from the project's `repo_url`; anything else is rejected loud (#520 P1 + #523 P2) |
| `implement_feature(project_id, goal, …)` | Deprecated alias — forwards to `dispatch_task(kind="implement_feature")` |
| `fix_bug(project_id, description, …)` | Deprecated alias — forwards to `dispatch_task(kind="fix_bug")` |
| `review_repository(project_id, …)` | Deprecated alias — forwards to `dispatch_task(kind="review_repository")` (read-only) |
| `onboard(project_id, …)` | Analyze a repo and write the draft onboarding doc set — a thin `AGENTS.md` pointer (marker-delimited), `README.md`, `ARCHITECTURE.md`, plus `.devcontainer/Dockerfile` when absent |
| `create_repo(name, …)` | Stand up a fresh GitHub repo for a from-scratch goal |
| `delete_repo(name, confirm)` | Tear down a repo **devclaw itself created** (create_repo records provenance in a managed-repo ledger; anything else — e.g. a pre-existing human-owned repo — is refused). Irreversible, so `confirm` must also echo the exact `owner/name`, no registered project may still reference it, and the gh token needs the `delete_repo` scope |
| `start_program(project_id, goal, …)` | DEPRECATED sugar for `create_goal(mode='one_shot')` — files a one-shot goal that rides the same speckit advance loop with a plan-once cadence |
| `get_program(program_id)` / `list_programs()` | Program status + task DAG |
| `get_status(task_id)` / `list_tasks(...)` / `get_events(...)` | Task history + replayable event feed (live SSE over HTTP) |
| `get_scorecard_metrics(window_hours?)` | Rolling scorecard over the last N hours (default 1 week): merge rate, evaluator-verdict distribution, steer rate, first-pass hit rate, workspace breaks — a cheap SQLite read, callable from Telegram/dashboards |
| `review_trends(scope?)` | Tail of the cross-session trend detector's `trends.md` — `harness_self` (devclaw's own self-observability) or a workspace path for that project's trends |
| `cancel_task(task_id)` / `cancel_program(program_id)` | Abort in-flight work — tears down the sandbox |

Async by default: a tool call returns a `task_id` immediately and the work runs in the background. Pass a `notify_url` to get a callback on completion/block instead of polling.

### Durable goals (the goal layer)

**One primitive, one dial** (ADR 0003): a goal and a program are the same thing — a goal — differing only in *re-evaluation cadence*, selected by `create_goal(mode=…)`:

- **`long_lived`** (default) — the **drip**: each heartbeat dispatches the next advance, judges *direction* periodically (not just shipped PRs), and stays steerable mid-flight. For fog-of-war objectives where the path reveals itself as work lands.
- **`one_shot`** — the **sprint**: the same advance loop, but done is proposed as soon as an advance session lands — for work that's fully specified up front. The proposal is still gated on the grounded done-gate review.

In both modes the **worker plans in-sandbox** — speckit `specs/*/` artifacts committed to the repo (spec 008); the host dispatches a mechanical, zero-LLM advance brief. Both modes share every gate, the delivery contract, and the close discipline. `start_program` survives as a deprecated alias for `create_goal(mode='one_shot')`; raw queue programs survive underneath only for explicitly pre-planned DAG submissions — no host code produces plans anymore.

| Tool | Does |
|---|---|
| `scope_grill(idea, transcript?)` | One turn of the pre-goal scope interview with the waiter: given a rough idea + the transcript so far, returns the next question (with a reasoned default) or the final agreed spec — the input `create_goal` deserves |
| `create_goal(goal_id, objective, project_id, done_when, backlog, mode, …)` | Register a goal DevClaw drives — `mode='long_lived'` (default, per-tick cadence) or `'one_shot'` (same advance loop, done proposed once an advance lands). `project_id` resolves the workspace + repo from the registry (#520) |
| `verify_goal(objective, project_id, …)` | Pre-flight check — same admission validations as `create_goal`, no side effects; previews reject/warn conditions |
| `get_goal(goal_id)` | Objective, phase, what's in flight, the latest direction verdict, recent log |
| `list_goals()` | All goals + phase + direction |
| `steer_goal(goal_id, message)` | Correct/redirect — recorded as steering, honored on the next tick |
| `resume_goal(goal_id)` | Recovery verb: unblock a blocked goal whose blocker was cleared out-of-band and re-plan on the next tick — same contract, no steering recorded (direction changes go through `steer_goal`) |
| `evaluate_goal(goal_id)` | Force an on-demand, artifact-grounded direction evaluation now (not just on the periodic cadence) |
| `tail_goal(goal_id, …)` | Deep read-only feed: deliveries tail (what each action actually shipped) + recent events |
| `get_trace(goal_id, since_id?, limit?, kind?)` | Durable trace feed for a goal — every cognition call, dispatch, settle as replayable events (the audit trail under `tail_goal`'s narrative) |
| `cancel_goal(goal_id)` | Permanently stop a goal — terminal `cancelled`, tears down any in-flight action |

**Lifecycle:** goals are born `executing` — both `create_goal` modes stamp `lifecycle="executing"`. The old `investigating → firming` phases were removed with the host planning chain (spec 008): the worker scopes work in-sandbox with speckit, so there is nothing for the host to investigate or firm. Legacy rows carrying an older lifecycle heal loudly to `executing` on their first tick.

**Stub policy** (`Goal.stub_acceptable: list[str]`) — the done-gate refuses any clause that ships as a stub *unless* the owner has explicitly listed that clause in `stub_acceptable`. Mechanical, not vibe-based: an unauthorised stub flips its clause to unsatisfied at gate time.

**How a goal is driven (per heartbeat):**
1. **Cheap check** (0 tokens) — poll the in-flight action via a local SQLite read.
2. **Per-delivery evidence** (0 tokens) — on a finished action, read the *full* task result (agent output + gate verdict) and append a grounded note to `deliveries.md`.
3. **Advance dispatch** (0 tokens) — build the mechanical advance brief (objective + steering + settle detail) and dispatch the next advance in-process; the worker plans the actual work in-sandbox.
4. **Direction evaluation** (periodic LLM call) — every `DEVCLAW_GOAL_EVAL_EVERY` deliveries, judge whether the *delivered work* is achieving the objective; corrections are fed back as steering, a hard verdict blocks.
5. **Done-gate** — the worker's `done` is only a *proposal*; it triggers a read-only `review_repository` against the goal's `done_when` + `stub_acceptable`, and the goal closes **only if the evaluator confirms `achieved`** from that review. "Done" is gated on grounded evaluation, not on counting PRs.

The zero-token idle guard is load-bearing: an idle goal and an in-flight-still-running goal cost **0 `claude` calls** (the heartbeat is mechanism; cognition runs only when there's real work). Canonical: [`docs/architecture.md`](./docs/architecture.md) §Invariants.

### Dry-run cognition (debug — pure, no side effects)

Each runs ONE cognition pass exactly as the goal layer would — same prompts, same
parsers — but files nothing, dispatches nothing, and touches no goal state. For
previewing what the loop *would* think before committing a goal.

| Tool | Does |
|---|---|
| `dry_evaluate(objective, done_when, review_report, …)` | The direction/done evaluator against a supplied review report |

### The project registry (control plane)

The single source of truth for **"which repos is devclaw working on, and what's the status of each"** — one entity above the tasks/programs/goals primitives, drivable from chat, API, *and* CLI. A `Project` is a thin record (repo · workspace · status · the goal(s) driving it); it links goals **by id** and joins their live status on read, so it never caches phase and never rots.

| Tool | Does |
|---|---|
| `register_project(project_id, name, …)` | Register a repo in the portfolio (slug id; optional repo_url / workspace_dir) |
| `list_projects(status?)` | Every project + a live rollup: each linked goal's phase/direction + derived health |
| `project_status(project_id)` | Full status of one project (facts + live goal status) |
| `update_project(project_id, …)` | Update facts — pause/archive, fix repo/workspace |
| `link_goal(project_id, goal_id, unlink?)` | Attach/detach a durable goal (by id; status joined live) |
| `delete_project(project_id)` | Hard-delete a project record (goals untouched) |

Same control plane from a terminal (talks to the same stores; no server needed):

```bash
devclaw projects list                 # or: python -m devclaw.cli projects list
devclaw projects show todo-fullstack-demo
devclaw projects register todo "Todo App" --repo-url git@github.com:me/todo.git
devclaw projects link todo-fullstack-demo todo-quality-audit
```

…and a portfolio view at **`/console/projects`** on the web console.

The traces telemetry table (every cognition call, dispatch, delivery,
notification, trend check the heartbeat emits) is readable the same ways — no
hand-written sqlite against a DB snapshot to answer "what happened overnight":

```bash
devclaw trace list --since 24h --errors-only   # every event, filtered in SQL (--goal/--kind/--role/--limit/--json)
devclaw trace report --since 24h               # deterministic day-report: tasks by status + error class, cognition
                                               # latency p50/p90/max + timeouts by role, retry storms, OWNER pings,
                                               # trend-check volume — pure SQL aggregation, no LLM
```

…plus `GET /traces.json?since=24h&errors_only=1&…` on the HTTP server (same
filters; default 200 rows, max 1000, newest-first) and the goal-scoped
`get_trace` MCP tool.

### The operator console (`/console`)

The human surface for supervising the fleet — a React SPA (Vite + TypeScript, `devclaw/server/console/`) served by the same HTTP server; `/` redirects to it. Three levels, mirroring the screenshots above:

- **Overview** — portfolio at a glance: projects / running / **needs-you** counts, the blocked-goals feed, recent activity, and the dispatch/off-hours state in the corner.
- **Projects** — every repository devclaw is driving, with live goal rollups (status joined live from the goal store, never cached).
- **Goal detail** — objective, phase pills, lifecycle timeline (`executing → verifying → done`), tabs for tasks / pull requests / activity / schedule, and the block banner with one-tap verbs: **Resume** (blocker cleared, same contract), **Steer** (change direction), **Cancel**.

The console reads generated views and JSON projections — it never mutates state outside the same MCP-tool verbs the waiter uses. Rebuild with `npm --prefix devclaw/server/console run build`.

### Deploy hosting (experimental — not yet in production)

The *intended* handoff for an `achieved` goal is a running product the owner opens, not a diff to read. **Status:** the plumbing below is built and the auto-fire path is wired, but it has never run end-to-end in production (`list_deploys` returns `[]`), and the in-container launcher currently recognizes only Python-FastAPI (`backend/requirements.txt`) or static (`frontend/`) repos — the owner's real stack (.NET 9 / Angular 21 / Postgres) is not yet hostable. Making the deploy contract repo-declared (Dockerfile/compose) instead of launcher-inferred is tracked in **#401**.

| Tool | Does |
|---|---|
| `deploy_project(workspace_dir, slug)` | Durable deploy → stable Tailscale `https://<node>.<tailnet>.ts.net:<port>/` URL that survives reboots. Auto-fires when a goal reaches `achieved`. |
| `deploy_status(slug)` / `list_deploys()` | Status of one deploy (exists / running / ready + stable URL) / list them all |
| `stop_deploy(slug)` | Stop a deploy, tear down its Tailscale serve, free its VPS resources |

Tailscale wiring is best-effort + graceful-degradation: `deploy_project` attempts `tailscale serve` and, if devclaw's container can't reach tailscaled, returns the one-time serve command (which then persists across reboots). Mounting the tailscaled socket into the devclaw-mcp container makes it fully automatic with no code change.

### Reliability & quality

Built to run unattended, and to ship code worth merging:

- **Survives usage limits.** A quota / rate-limit pause is *classified*, not treated as a failure: WIP is preserved, one account-wide pause gates both the task queue and the goal heartbeat, and both auto-resume when the cap resets — zero tokens while paused, the owner pinged once.
- **Mechanical blocks self-heal.** Every block carries a structured kind; the re-checkable mechanical ones (a corrupt contract file, an unreachable repo) auto-heal at zero LLM cost, damped so a flapping condition can't burn quota. Question / bug blocks stay human-gated: `resume_goal` re-attempts the same contract once you've fixed the blocker; `steer_goal` changes direction.
- **No-progress watchdog.** An executing goal that ships nothing for a bounded wall-clock window pings the owner once — a zero-token check that complements the per-task timeout.
- **In-house quality gate (no third-party QC).** The engineer is briefed to *audit before extending*, and the verify gate runs a **test-integrity** check that fails on deleted / skipped / weakened tests, closing the "go green by gutting the tests" path.
- **Pre-PR review gate — green means *reviewed*.** A green gate proves behaviour, not quality; it can't see a dead-code line or a frontend change it never exercised. So before the PR opens, a separate `claude` pass *reads the diff* against the ticket + quality bar and returns `approve` / `request_changes`, feeding located issues back through the retry loop (then escalating).

Canonical statement of these as fail-closed invariants — with the enforcing call sites and issue history: [`docs/architecture.md`](./docs/architecture.md) §Invariants.

## Auth (the design constraint)

DevClaw inherits a `claude` OAuth session — it never uses an API key. `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are **actively stripped** at every host- and sandbox-side call site so a stray key can't silently switch autonomous runs onto metered billing. All you need is a logged-in `claude` CLI: the cognition callers shell out to it, and the per-task sandbox bind-mounts an explicit allowlist under `~/.claude` **read-only** (the credential token + `.claude.json` identity by default; nothing else). Canonical enforcement list: [`docs/architecture.md`](./docs/architecture.md) §Invariants.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
pip install -r openhands-runner/requirements.txt   # only inside the sandbox image
npm install -g @agentclientprotocol/claude-agent-acp

DEVCLAW_TRANSPORT=stdio devclaw-mcp        # local dev (MCP over stdio)
# or HTTP for a long-running service:
DEVCLAW_TRANSPORT=http DEVCLAW_PORT=8000 devclaw-mcp
#   → MCP at /mcp, the operator console at /console (/ redirects there; the
#     legacy /dashboard · /goals · /projects pages 302 to it), SSE at /programs/:id/events
```

(`devclaw-mcp` is the console script for the server; `devclaw` is the control-plane CLI; `python -m devclaw.server` / `python -m devclaw.cli` work too.)

### Engine modes (`DEVCLAW_ENGINE`)

| Value | Engine | Isolation | Use |
|---|---|---|---|
| *(unset)* | OpenHands in a per-task **docker sandbox** | ✅ full | production |
| `host` | OpenHands **on the host** (no container) | ⚠ **none** — agent has full filesystem access | dev/CI/validation where docker is unavailable |
| `stub` | deterministic stub (no OpenHands, no claude) | n/a | harness validation (`evals/`) |

### Environment variables

Copy [`.env.example`](./.env.example) to `.env` (gitignored) and uncomment what you need — devclaw loads it on startup, and shell/systemd env always wins over it. Every var organized by purpose (transport, state, sandbox, goals, model tiering, deploy, review gate) lives in [`docs/reference/env-vars.md`](./docs/reference/env-vars.md). The most common ones to know:

| Var | Default | Purpose |
|---|---|---|
| `DEVCLAW_TRANSPORT` | `stdio` | `stdio` or `http` |
| `DEVCLAW_PORT` | `8000` | HTTP port |
| `DEVCLAW_DB` | `./devclaw.db` | SQLite path for state |
| `DEVCLAW_GOALS_DIR` | `~/memory/goals` | one folder per durable goal |
| `DEVCLAW_ENGINE` | *(unset)* | engine mode: unset = OpenHands sandbox, `host` / `stub` / `claude_sdk` |
| `DEVCLAW_EXEC_MODEL` | `claude-sonnet-4-6` | the in-sandbox coding agent's model (full id) |
| `GITHUB_TOKEN` / `GH_TOKEN` | — | repo push + PR access for `open_pr` delivery |

For the full table (~60 vars), see [`docs/reference/env-vars.md`](./docs/reference/env-vars.md).

## Tests

```bash
pip install -e ".[dev]"
pytest          # state store + queue/DAG + goal layer + gates, all stubbed — no docker, no claude
```

To validate the **real** pipeline (a logged-in `claude` driving OpenHands in a docker sandbox), follow the layered runbook in [`docs/runbooks/live-shakedown.md`](./docs/runbooks/live-shakedown.md).

## Status

DevClaw is the live runtime. As of mid-2026 it serves as the chef behind an OpenClaw waiter agent, driving real repositories daily: the durable goal layer carries the load, the goal↔program unification (ADR 0003, "one primitive, one dial") is landed, and the operator console is the deployed human surface. The Tailscale deploy path is built but **not yet exercised in production** (#401). Earlier drift (the spec-kit elicitation flow, the preview hosting module) was removed rather than maintained.

## What this is NOT

- **Not a chatbot.** It's a backend service the OpenClaw waiter calls.
- **Not a general assistant.** It executes software-development goals, nothing else.
- **Not a rebuild of OpenHands or Claude Code.** OpenHands is the wrapper, `claude-code` + `claude-agent-acp` is the agent harness inside the sandbox; DevClaw is the orchestration above it.
- **Not novel reasoning.** The intelligence is Claude's, used twice: as a one-shot reasoning API for direction/done evaluation, and as the interactive worker harness inside the sandbox (which also plans, via speckit). DevClaw is the state machine + scheduler + persistence + prompts that make one goal span days.
- **Not infallible.** Autonomous means "doesn't need the next prompt," not "can't ship broken work." Today's done-gate is Claude judging Claude's output; that's structurally circular and has shipped green-tests-but-broken-UI cascades. The in-progress E2E test layer exists to break that circle with mechanical browser evidence before the evaluator weighs in.

## License

[MIT](./LICENSE). Copyright 2026 Denys Sychov.
