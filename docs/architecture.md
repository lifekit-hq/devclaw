# devclaw architecture

> **The system doc.** Part I is the mental model — read it when you've lost the
> thread. Part II is the **locked contract** (layer boundaries, invariants,
> testability): changes that violate it are architectural changes, not feature
> changes. The code is the territory — when this doc and the code disagree,
> trust the code, then fix this doc. Historical rationale (why this engine, why
> Pro OAuth) lives in git history (`git log -- docs/decisions`), not here.

## The one paragraph

devclaw is a **software-development agentic loop**. You hand it a durable *goal*
with verifiable completion criteria; a self-executing heartbeat carries it —
**plan → sandboxed execution → verify gate → evaluate → iterate** — with hard
brakes (retry caps, a no-progress watchdog, `stalled`/`needs_human` verdicts) so
it never optimises into the void. It sits **behind MCP** and is driven by an
**OpenClaw "waiter" agent** that turns chat into tool calls; **devclaw never
talks to the user directly**. Cognition is always `claude` over a Pro/Max
**OAuth** session — **no API key, no metered billing, ever**.

## The vocabulary (canonical names, not homegrown ones)

Ruled 2026-08-18; this table is the **normative** statement of the terms
(spec 010 FR-007). Established software-engineering paradigms are used by their
canonical names rather than re-derived under local vocabulary, so a reader who
knows the paradigm already knows the contract.

| Concept | Canonical name | In devclaw |
|---|---|---|
| The ask | **Work item** (Kanban) | a GitHub issue |
| Admission to work | **Definition of Ready** | the `devclaw-ready` grade (specs 006/009) |
| Completion judgement | **Definition of Done** | the done-gate |
| The milestone-level objective | **Saga** / long-running process | a goal — authored from five named slots, never prose (spec 012 US2: objective · done_when · out_of_scope · invariants · established) |
| The execution atom | **Unit of Work** (Fowler) | one sandbox run → one atomic, verified, PR-able change-set |
| The plan | **Task graph (DAG)** | `tasks.md`; `[P]` marks topological independence — parallelism is *data in the plan*, never executor control flow |
| Parallel safety | **Hermetic action with declared I/O** (Bazel) | **not enforced** — no gate constrains an increment to a declared file scope (#762); sandbox + worktree isolation stop mechanical collisions only |
| Concurrency default | **Single-writer / actor-per-project** | at most one goal actively dispatching per project |
| Integration | **Merge queue** (Bors) | none — every increment integrates sequentially on the goal branch; the one merge is merge-on-close (spec 025) |

### A saga is authored against a schema

A goal is filed from five named slots, not a paragraph: what is being achieved
(`objective`), what completion means (`done_when`), what is deliberately
excluded (`out_of_scope`), what must still hold afterwards (`invariants`), and
what is already settled and must not be re-derived (`established`). Filing one
with a slot unfilled is rejected at creation naming that slot — an EMPTY LIST
declares a slot empty, and only silence is refused, because silence and "there
are none" produce different prompts and only one of them is a decision.

Since spec 019 a goal that works tracked issues is a **pointer, not an
essay**: `create_goal(issues=[...])` records ordered issue numbers on the
project's repository, and the dispatch boundary fetches each issue's LIVE
state into the worker brief — a creation-time copy is unrepresentable, a
closed or readiness-revoked issue drops out of scope loudly, and when every
referenced issue is closed the goal proposes done without spending a worker
session. `done_when` may then be omitted: the completion contract defaults
to the issues' acceptance sections, read live at each done-gate round. The
doorway enforces the discipline mechanically — references must be graded
`devclaw-ready`, one issue is held by at most one live goal, and the goal's
own free text is budget-capped (`DEVCLAW_GOAL_TEXT_BUDGET`): the knowledge
lives in the issue, the goal carries ordering and scope glue. Goals without
references (bench, greenfield) keep the full prose lane above unchanged.

Every slot earns its place by changing what a worker does (spec 012 FR-009);
the framing is re-sent in full with every unit of work — a fresh sandbox has no
memory, so a pointer would be a request while a slot is a fact — which is why
each slot is size-bounded (`goal/saga_framing.py`, `goal/prompt_budget.py`).

Goals authored before this schema carry an ABSENT slot rather than an empty
one, and their brief renders exactly as it did; devclaw does not put words in
an author's mouth by inventing an empty declaration for them.

### Single writer per project

At most one goal dispatches increments into a given project at a time; the rest
queue and start automatically. Two independent plans on one repository cannot be
reconciled — sandbox and worktree isolation stop *mechanical* collisions, but
nothing stops two planners that don't know about each other from drifting, and
the drift only surfaces at integration (#553 was one symptom: two goals
allocating the same `specs/009-…` directory).

The hold is **derived, not stored** (spec 010 FR-005, amended 2026-08-22): the
holder of a project is the first goal on it that can actually act this sweep,
by age with in-flight work outranking it, tie-broken on goal id — a pure
function of rows the CAS'd transition discipline already governs
(`devclaw/goal/project_hold.py`). There is no lock row, no acquire and no
release, so a holder that dies cannot leave a lock nobody clears, and no heal
machinery is needed for a state that cannot occur. "Can actually act" is the
**runnable-head rule** (owner ruling 2026-09-01, generalizing spec 025 FR-015's
blocked skip-over): a blocked goal, a goal owing only its merge, and an idle
goal with no unread steering and no due cadence are all skipped as candidates —
head-of-line blocking is a bug, not a policy (2026-08-31: one cadence-idle head
stranded 7 runnable successors for a night). Two holds are NOT skipped because
the goal is finishing work it already owns and re-drives it ahead of the hold
gate in the same sweep: a `mechanical:ci` hold and a held done proposal
(`pending_done_proposal`) keep the lane (2026-09-06: dropping a ci-held head
handed its lane to a successor for the very sweep the hold cleared, and two
goals ran on one directory). The single-writer invariant is
untouched: at most one goal dispatches per project, a successor mid-task keeps
the lane against a newly-runnable elder (in-flight outranks age), and the elder
reclaims it at the next sweep where nothing is in flight. Goal-less direct
dispatches (`dispatch_task`, kinds `implement_feature`/`fix_bug`/…) are exempt because
they are operator-present, and say loudly that a goal holds the project.

**One goal, one checkout** (2026-09-06). The lane serializes *plans*; it never
made two goals' *directories* disjoint, and on 2026-09-06 two goals on one
project workspace captured each other's tips as change baselines. A goal's
tasks now run in `<project workspace>/.goals/<goal_id>` — a full clone seeded
locally from the project checkout (`engine/workspace.py`
`ensure_goal_checkout`, hardlinked objects) with `origin` pointed at the real
remote; the task row's `workspace_dir` is that path. The project checkout stays
the goal's identity (manifest-at-base reads, project docs, trends, deploy,
doctor, the toolchain cache key) and is what direct tasks run in; its pristine
clean keeps `.goals/`, onboarding boilerplate revision 2 ignores `.goals/` in
the repo, and `.git/info/exclude` covers the checkout until that PR merges. A
terminal goal's checkout is removed by a zero-token sweep at the end of each
heartbeat, derived from the store like the lane itself.

---

# Part I — the mental model

## The five layers (and the two chains)

The system is five layers below the user. Only layer 5 is an agent harness in
the technical sense — the rest is orchestration.

| # | Layer | Lives in | Owns |
|---|---|---|---|
| 1 | **MCP surface** | `devclaw/server/` | tools, auth, console, transport — pure protocol |
| 2 | **GoalService + heartbeat** | `devclaw/goal/` | the goal state machine + the ~15-min tick |
| 3 | **Cognition callers** | `devclaw/goal/evaluator.py`, `devclaw/goal/summary.py`, `devclaw/goal/triage.py`, `devclaw/goal/admission_lint.py`, `devclaw/intake_readiness.py` | one-shot `claude --print` prompt/parse calls (planning cognition relocated into the worker's speckit run — spec 008 shrink) |
| 4 | **TaskQueue + engine** | `task_queue.py` (+ its `devclaw/queue/` mixins), `devclaw/engine/` | dispatch, concurrency, the container launcher, the settle/gate path |
| 5 | **Worker harness** | `runner/runner.py` (inside the sandbox) | the in-sandbox agent turn-loop, skills, hooks, `verify_cmd` |

There are exactly **two paths through the stack**, and they never cross layers:

- **Cognition:** `1 → 2 → 3`. The heartbeat asks a one-shot `claude` call "what
  next?" and gets structured output back. No container, no dispatch.
- **Execution:** `1 → 2 → 4 → 5`. The heartbeat dispatches an *action* into the
  task queue, which launches a per-task docker sandbox, which runs the worker
  harness.

The chain is strict. Layer 1 must **not** dispatch tasks. Layer 2 must **not**
spawn containers itself — it goes through the engine (layer 4). No layer reaches
through another, and none of them cache another's state.

## The heartbeat is the whole machine

`devclaw/goal/tick.py` is the beating heart: one `tick_goal()` per goal, every
~15 minutes. Everything else is plumbing around it. The tick is a small state
machine over the goal lifecycle:

```
executing → (done-gate) → done
     │            │
  dispatch     grounded eval of done_when/spec; closes ONLY
  actions,     if the evaluator says "achieved"
  settle them
```

The worker plans via speckit in-sandbox; there is no host-side planning
phase.

Since spec 032 (2026-09-03) the done-gate is *preceded* by a mechanical read
of the delivered PR's CI rollup for its exact head (`goal/remote_checks.py`,
one bounded `gh` read, zero cognition): red steers the failing checks back
as the next correction without spending a gate round, pending or unreadable
holds the goal on `mechanical:ci` and re-reads on the heartbeat cadence, no
CI definition or a CI that cannot execute raises a typed Problem, and only a
green fact dispatches the review and runs the evaluator. Merge-on-close
re-reads and requires the *same* green head — a head that moved re-holds and
re-opens the gate. The project's own verification environment is the verdict
of record; the in-sandbox `verify_cmd` is a fast pre-check whose pass is
never evidence.

Since spec 035 (2026-09-05) the done-gate's rubric is **pinned per contract
revision**: the first judged round of a revision decomposes `done_when` into
clauses (its own evaluator output — no extra cognition call) and persists
them with mechanical ids (`goal_contract_pins`, one row per goal+revision,
history retained); every later round judges exactly that list — evidence
fresh, rubric fixed. A verdict that adds, drops, duplicates, or invents a
clause id is a malformed round: it fails closed (#186) and does NOT charge
the churn brake (a formatting failure is a mechanism failure, never a
judgment). A corrupt pin re-decomposes loudly with the recovery recorded;
the only re-pin trigger is an amended contract (a new revision digest) —
there is no operator verb for it. Round accounting rides the pin (US2): the
satisfied set, each clause's evidence, and any Decision that settled it are
folded in per round, so `donegate_progress` counts against a stable
denominator — and a round may flip a previously-satisfied clause only by
citing what changed (`flip_cause`: the repo change since that evidence, or
its named defect); a causeless flip is a malformed round, closing the other
half of the fs-479 incident (satisfied at 11:06, sole failure at 12:24, no
repo change between).

Two properties make the heartbeat cheap and safe, and both are load-bearing:

1. **Zero-token idle guard.** An idle goal, or one whose work is still in
   flight, costs **~0 `claude` calls**. The cheap SQLite/timestamp checks run
   *before* any LLM call — this ordering is deliberate and tested
   (`FakeClaude.calls == 0` on idle paths). Adding a tick-path LLM call that
   fires on idle breaks the quota guarantee.
2. **Per-goal tick lock + CAS.** Only one tick runs per goal at a time, and
   every state write goes through `GoalStore.transition()` — a compare-and-swap
   against the `LEGAL` table in `goal/transitions.py`. A stale-snapshot write
   raises `TransitionConflict` and is abandoned, not silently clobbered. This is
   what lets `steer_goal`/`resume_goal`/`cancel_goal` (from the MCP path) write
   **concurrently** with the heartbeat without corruption.

`tick.py` is a thin spine plus five modules split by concern: `tick_context`
(primitives), `tick_guards` (watchdog + block/auto-heal), `tick_dispatch`
(engine-launch paths), `tick_donegate` (the done-gate), `tick_settle` (settle &
recover). The spine keeps a re-export facade so the split is invisible to
callers.

## One task's journey

When the tick decides to *do* something (not just think):

1. **Branch selection** (`tick_dispatch._dispatch_action`) — a
   `DeliveryStrategy` (`goal/delivery_strategy.py`) decides the branch:
   executing goals accumulate every increment's commits on one shared
   `goal/<id>` branch (one cumulative PR); legacy goals with no recorded
   lifecycle deliver each action as its own branch + PR.
2. **Prepare the workspace** — `prepare_workspace()` proves the goal's own
   checkout (`<project>/.goals/<goal_id>`, one goal, one checkout) is
   placeable on the chosen branch (a bad `repo_url` or unreachable origin
   blocks legibly here). The branch itself rides on the action
   (`Action.branch` → the task row's `target_branch`): the **queue places it
   again at run start**, immediately before it captures the change baseline,
   because a pending task may wait behind other work on the same directory
   and whatever moved HEAD meanwhile must never become its base (2026-09-06).
3. **Atomic dispatch** — the task-row creation + the `DISPATCH_ACTION`
   transition + the log line commit as **one** SQLite transaction. A crash or
   CAS conflict rolls the whole unit back, so "task dispatched but the in-flight
   ref was lost" is structurally impossible.
4. **Run in a sandbox** — `TaskQueue` claims the row and launches a per-task
   `docker run --rm` (`engine/sandcastle.py`); the worker harness runs the agent
   turn loop and writes line-delimited JSON back on stdout.
5. **The gates decide, not the agent** — after the agent finishes, the settle
   path runs an ORDERED pipeline of pure verdict producers
   (`quality/gate_pipeline.py`; the gates in `quality/task_gates.py`; assembled
   in `queue/settle.py`). The first non-ok verdict short-circuits the chain and
   is fed back through the retry loop; a gate never mutates a row. The agent's
   self-report is never trusted, and a crash *in* a gate is a failure, never an
   approval (#186). The order:
   - `verify` — the runner's `verify_cmd` exit. Reads no diff, so a failure
     short-circuits before any git call. A fast pre-check only: its pass is
     never evidence (spec 032).
   - `materialize` (spec 013) — the moment the run ends the host stages
     everything left in the workspace and commits it; the change is the range
     `pre_run_sha..post_run_sha` (`task_change.py`), and *every* consumer —
     each gate below, the change-size projection, the advisory checks, and
     delivery — reads that one object. A span that cannot be determined fails
     closed; an empty span is an explicit no-change outcome (the task settles
     done, publishes nothing, the goal counts no progress). A retry keeps the
     workspace and iterates on its own output.
   - `change_class` (spec 032 US3) — every path in the span carries a class:
     product, gate input (CI workflows, `AGENTS.md`, test-runner/build
     configuration, install scripts, toolchain pins) or environment
     declaration (`devclaw.json`, `.devcontainer/`). A gate-input edit or a
     committed binary fails without a retry; a ticket that is *about* those
     files declares the path in scope. Gate inputs are never evidence for a
     `done_when` clause.
   - `test_integrity` — the diff is scanned for deleted, skipped or gutted
     tests; a scanner crash fails closed; a removed test whose name still
     exists elsewhere in the tree is credited as a move.
   - `review` — the adversarial pre-PR diff review, consulted **only under
     `strict`**; under `trust` (the default) it is not in the chain at all —
     zero `claude` calls, no crash surface — and the done-gate re-catches its
     findings (spec 001).
   - `browser` — the browser-E2E gate (`quality/browser_gate.py`): an
     app-surface web-UI change must carry a passing real-browser Playwright
     run, proven via the runner's parsed `browser_report` counts, never a
     `verify_cmd` string-match. A library-only diff (`*/src/lib/*`, or
     `devclaw.json` `surface: library`) is exempt; a grounded reachability
     judge may clear a block when the changed UI is not rendered in the app;
     `flexible` mode waves through a project with no browser suite.

   **Consequence is one pure function (ADR 0007, `quality/gate_policy.py`).**
   `ALWAYS_HARD` = `verify`, `materialize`, `change_class`, `test_integrity`,
   `delivery_trust`, `done_gate` — they block under both dials. `DIAL_ABLE` =
   `browser`, `review`, `slice` — they block under `strict` and, under `trust`,
   a finding that survives every retry **advises-and-ships**: recorded loud in
   the log + problems catalog and surfaced in the PR body, with the validation
   lane (spec 015) as the backstop. An unrecognized dial value is `strict`. An
   *unreviewable* case in a consulted gate (crash, quota, worker-block) fails
   closed regardless of the dial.

   Three gates in that policy run at layer 2, not in the task pipeline:
   `slice` (`goal/tick_settle.py`) — a delivered increment that advanced more
   than one `[US<n>]` story-slice of `tasks.md` is a build-ahead, parked for a
   re-slice under `strict`, advised under `trust`; `delivery_trust` — the
   delivered PR's CI rollup read as a mechanical fact for the exact head
   (`goal/remote_checks.py`, consumed by `tick_donegate` before the done-check
   review and again by `merge_on_close`); and `done_gate` — `done_when` clause
   grading, where an unmet clause holds the goal open under either dial while
   the review's *structural* concerns ride the dial (advise as follow-ups
   under `trust`, hold under `strict`). A done-gate that refuses to close the
   same goal 3 rounds in a row with the satisfied-clause count flat parks it
   (`donegate_churn`, carrying a typed Problem — spec 031); a round that beats
   the best count seen restarts the counter (`goal_status.donegate_progress`).
   The declared-scope gate (spec 010 FR-103) was retired 2026-08-31 — spec 022
   US3 had deleted the `[P]` lane that produced its trigger, so it was never
   consulted on a real increment (#762). A worker never spawns a worker: the
   sandbox carries no devclaw MCP surface it could ask through.
6. **Deliver, then settle** — for `deliver=True` tasks the change becomes a
   branch/PR *before* `done` is observable, so a poller never reads "done
   without a PR". A delivery that can't push/PR settles `failed`, never a silent
   success.
7. **Settle atomically** — settlement row + delivery row + log + the
   `ACTION_SETTLED` transition, as one unit (`tick_settle`). Nothing on the
   settle path merges (#641/#486); the ONE merge in the system is
   **merge-on-close** (spec 025): a confirmed-achieved done-gate close
   squash-merges the goal's cumulative PR (`goal/merge_on_close.py`), with
   one bounded conflict-resolution self-heal, and a goal that cannot merge
   parks `mechanical:merge_failed` instead of closing.

The full temporal trace of one task, every hop, lives in
[`flows/task-execution.md`](./flows/task-execution.md); how dispatches become
PRs in [`flows/delivery.md`](./flows/delivery.md).

## Where state lives

**SQLite (`devclaw.db`) is the single source of truth.** Since Tranche 1 the
goal layer lives in the same DB as the task queue: `goal_status`,
`goal_steering`, `goal_log`, `goal_deliveries`, `goal_phase_history`,
`goal_contract_pins`, `goal_decisions`, plus the goal-transcending `project_docs` (the repo
brief workers accumulate, keyed by normalized workspace path — it survives
goal cancel+refile on purpose). The familiar files — `STATUS.md`, `log.md`, `inbox.md`,
`deliveries.md` — are **generated views**: human- and rollback-readable,
**never read back for decisions**. Only `goal.yaml` and `spec.md` stay plain
files.

The views are write-only (#617): steering enters through the `steer_goal`
verb alone, and `tests/test_views_never_read_back.py` holds the line
structurally — a production module may not even NAME a view file unless it is
a listed writer.

**Single writer.** Only the `TaskQueue` mutates task rows; `StateStore` is an
append-only event log and its views are projections. Goal state is owned by
`GoalStore` and mutated only through the CAS'd `transition()` — which is
exactly why the views above may not be read back: re-ingesting one makes
whoever last touched a markdown file a second writer that the CAS does not
cover.

**Continuous-eval — the `eval_outcomes` projection (ADR 0006).** Every task
settle is an evaluation sample for free: `StateStore.mark_done` /
`mark_failed` / `mark_task_cancelled` materialize one `eval_outcomes` row
**inside the settle's own commit** (same single writer, exactly-once — the
insert fires only when the settle UPDATE actually moved a row, made structural
by a partial unique index on `task_id`). `failure_class` is **mechanical
string bucketing** of the settle-path marker texts (`review_rejected`,
`verify_failed`, `timeout`, `rate_limited`, `blocked:worker`, … —
`state_store/rows.derive_failure_class`), never an LLM call — zero extra
tokens per settle. Basket runs (`evals/measure_passrate.py`) land in the SAME
table as `source='basket'` rows via `devclaw evals ingest <file-or-dir>`,
idempotent on (source, report_ref, ticket).

**Continuous-eval — the run-cycle window-close report (ADR 0006 decision 3).** When
the run cycle (22:00–05:00 `Europe/London` by default — nightly, but the window is
just a recurring cycle) closes, the goal heartbeat — the *scheduled-edge owner* —
fires a mechanical, **zero-LLM** report:
`GoalService._maybe_emit_cycle_report` computes the most-recent closed window
(pure clock math), checks a `cycle_reports` row doesn't already exist for that
`cycle_key` (the PK is the once-per-cycle idempotency guard — it fires on the
first wakeup after close and is a no-op the rest of the day), then assembles the
cycle's slice from existing rows (`eval_outcomes` + the `problems` catalog,
`goal/cycle_report.py`) and pushes it through the existing notifier. A cycle is
**clean** iff **zero mechanism-wedges** fired: wedge = `mechanical:*` blocks,
cognition-timeout-terminal, and engine/gate **crash** classes; a genuine
`needs_answer` and a **self-healed quota/auth pause** are surfaced but stay
clean (a gate *verdict* is the gate doing its job, not a wedge). The write goes
through the store (`StateStore.record_cycle_report`, single writer); no notifier
→ `sent_at` NULL (log-only, never an error).

**Self-observability — the `problems` catalog (capture/dedup layer).** Beside
`traces`, a `problems` table turns "devclaw fails/stalls N times a day" into a
ranked, countable set. `StateStore.record_problem(...)` — the **single writer**
to that table, pure mechanism (no LLM, no subprocess, safe off the zero-token
idle path because it fires only on a real failure) — UPSERTs one row per
**distinct** problem keyed on a fingerprint (`category | kind |
normalize(message)`), where `normalize()` strips the variable bits (uuids,
paths, goal/task ids, numbers, timestamps) so the same root cause collapses.
Recurrence increments `count` (and `recovered_count` vs `terminal_count`) rather
than appending a row, so the table stays **bounded** — it holds distinct
problems, not occurrences. It is wired at the failure choke points and captures
failures **even when devclaw recovered from them**: a block entry
(`GoalStore.transition()`/`force_block`, terminal), a task settling `failed`
(`StateStore.mark_failed`, terminal), a usage-limit pause
(`StateStore.set_global_pause`, **recovered** — it auto-resumes), and the
error-bearing trace events centralized in `PersistentTracer`
(cognition/subprocess errors, a review-gate-blocked delivery). A fourth feeder
joined these on a different axis (spec 027, `devclaw/goal/health_drift.py`): a
periodic, zero-LLM **environmental** probe records threshold breaches — disk
headroom across every instance-critical filesystem (the workspace volume, the
SQLite home and the docker root, enumerated by `_disk_surfaces` and grouped per
device), docker toolchain volumes no registered project accounts for, and
workspace directories that outlived their goals. Unlike the three above it
fires on **degradation rather than failure**, so the catalog surfaces "this box
is filling up" before it becomes "the task died". It records through the same
`record_problem` choke point, so it dedupes and ages identically; an
undeterminable probe records nothing (unknown is never an alarm and never a
false all-clear). Mechanical auto-heals do **not** re-record — the original
block entry already counted it.
This is the **capture + dedup + count** layer; the `list_problems` MCP tool
(reading `StateStore.list_problems`, most-frequent first, optional `category`
filter) is the read surface over it. **The catalog is a GATHERER, not a backlog
(N1/#371).** The single canonical store of *intent* — "what to do about a
failure" — is **GitHub Issues**; SQLite stays canonical for execution *state*;
this table is the mechanical feeder between them. The self-improving loop
(`goal/self_issue.py`) files a *recurring* problem as an Issue and links it back
(`issue_number`/`issue_state`) — since spec 014 the actual issue creation goes
through **`devclaw/issue_doorway.py`**, the single writer of machine-found
problems as GitHub issues: one versioned, machine-parseable body schema
(source, fingerprint, evidence, expected-vs-actual, severity, proposed
done-when), idempotent by fingerprint via the `machine_issues` ledger, fail-loud
on any filing error, zero LLM. Every future machine producer (the post-deploy
smoke, the spec-015 validator) files through it, and a structural guard holds
`gh issue create` to that module plus the human doorway (`devclaw/intake.py`).
Since spec 015 two machine producers feed that doorway: the **live-validation
loop** — a `validate_product` task kind whose agent-less runner branch boots
the repo-declared `devclaw.json` `validation` contract hermetically in the
sandbox, runs the accumulated acceptance suites, and hands a mechanical
`validation_report` back to the host (`devclaw/validation_loop.py`), which
files one finding per failing scenario; and the **read-only post-deploy prod
smoke**. Validation runs attach to a per-repo `qa`-mode goal that never plans
feature work, never terminates, never holds the project single-writer slot,
and idles at zero cognition; the launch trigger is the owner's `deploy_project`
button-press (a periodic cadence exists but ships OFF). A validation run never
opens a PR, never commits (the workspace is restored after the run), and is
never a gate — it emits intake, not verdicts. Every problem read surface — the `list_problems`
tool and the console `/problems.json` — carries that linkage plus a derived
`lifecycle` (`identified → filed → resolved`, one home: `problems.problem_lifecycle`)
so it points at the canonical Issue rather than inviting independent triage. See
`devclaw/state_store/problems.py` and the tool in `devclaw/server/tools/observability.py`.

**Self-triage — the propose-only interceptor (slice 1, 2026-07-18).** The first
consumer of that catalog. Before an **eligible** owner ping fires (an allowlist,
`tick_context.TRIAGE_ELIGIBLE` — slice 1 registers exactly one key, `db_size`,
the DB-size alarm), a bounded layer-3 triage cognition step (`goal/triage.py`,
prompt `prompts/self-triage.md`) dedupes the problem against `list_problems` and
drafts a **proposed** resolution, so the owner receives "problem + proposed fix +
how to approve" instead of a bare "there's a problem" — an approver, not the sole
diagnostician. It is **propose-only** (never auto-acts) and **fails toward the
owner**: it runs only when a real ping fires (never idle — the zero-token guard
holds), and any triage failure delivers the original raw ping unchanged. The
caller returns parsed output only; layer 2 (`tick_context.triaged_notify`)
renders + delivers. Auto-resolve on top is a deliberate follow-up. See
`goal/triage.py`.

---

# Part II — the locked contract

## Layer contracts

### Layer 1 — MCP surface

- **Public surface:** every `@mcp.tool` decorator in `devclaw/server/tools/`.
  HTTP endpoints in `devclaw/server/http.py`.
- **Allowed to call:** layer 2 (`goals.create_goal(...)`, `goals.get_goal(...)`,
  etc.), the project registry, `delivery/repo.py` (the `create_repo`/`delete_repo`
  gh provisioning pair — pure gh subprocess, no engine/queue involvement),
  `intake.py` (`file_intake`, the single-intake-doorway stage 1 — same
  pure-gh-subprocess class: validates the intake shape, files a labeled issue
  on a registered project's repo, can only create issues, never dispatch; see
  [`reference/intake-shape.md`](./reference/intake-shape.md) — plus the
  readiness-grading verbs `regrade_intake`/`grade_backlog` (spec 009: any open
  issue, any format; grade labels only, still never dispatch)), and —
  for the **direct-task intake**
  (`dispatch_task`, the v1 task-runner path re-surfaced by ADR 0011, git
  history: `git log -- docs/decisions`) —
  `TaskQueue.submit`. The direct intake is a sanctioned second front door onto
  the same queue → engine → gates → delivery machinery, NOT a goal bypass: it
  carries no goal, so there is no `GoalService` to bypass.
- **Forbidden:** touching goal state directly (must go through `GoalStore`),
  spawning engines/containers, or reaching queue *internals* — layer 1 talks
  to `submit`/read surfaces only; execution, gating, and settle stay layer 4's.
- **Tested by:** structural guards — `tests/test_route_shadowing.py` (no HTTP
  route shadows a more specific one) and `tests/test_tools_reexport_complete.py`
  (every registered tool resolves via `getattr(tools, name)`); tool behaviour
  with the layers below stubbed in `tests/test_dispatch_task.py`.

### Layer 2 — Orchestrator (GoalService + heartbeat)

- **Public surface:** `GoalService` methods (`create_goal`, `get_goal`,
  `steer_goal`, `resume_goal`, `evaluate_goal`, `cancel_goal`, …). Plus the
  heartbeat sweep, `GoalService.tick_all`.
- **Internal state:** `GoalStore`, backed by the goal-state tables inside the
  SAME `StateStore`/`devclaw.db` the task queue uses (see "Where state lives").
- **Allowed to call:** layer 3 (cognition callers) and layer 4 (via the
  in-process engine).
- **Forbidden:** spawning sandbox containers directly (must go through
  `TaskQueue` + `Engine`); calling `claude` directly (must go through a
  cognition caller); mutating `goal_status`'s phase/lifecycle/in_flight outside
  `GoalStore.transition()` (the CAS'd choke point).
- **The execution dial (ADR 0003, git history: `git log -- docs/decisions`):** a goal carries `mode:
  long_lived | one_shot` in `goal.yaml`. Both modes ride ONE execution path
  since the spec 008 shrink — the speckit advance loop (the worker owns the
  plan in `specs/*/`), the done-gate proposal after each settled advance, and
  the same gates. The dial is re-evaluation cadence only; a one_shot goal's
  first advance fires immediately and the done-gate's corrections chain
  work-present advances until achieved.
- **Tested by:** `tests/test_goal_*.py` (e.g. `test_goal_tick.py`,
  `test_goal_engine.py`) — single ticks with stubbed cognition + stubbed
  engine. The SQLite substrate: `tests/test_goal_state.py`,
  `tests/test_goal_transitions.py` (the `LEGAL` table + CAS in isolation).

### Layer 3 — Cognition callers

- **Public surface:** each module exposes a `default_caller()` factory and a
  per-purpose `build_prompt()` + `parse_response()` pair. The protocol lives in
  `devclaw/cognition.py` (`Cognition` protocol).
- **Internal state:** none. Pure functions over (prompt-template + goal-state +
  a workspace snapshot collected at the call site) → (subprocess) → parsed
  output.
- **Allowed to call:** `claude --print` via subprocess (today); any LLM via the
  `Cognition` protocol. Snapshot collectors additionally shell out to `git`
  (read-only, best-effort, never-raises) — see the grounded-cognition
  invariant below.
- **Forbidden:** writing to the goal store directly (return parsed output, let
  layer 2 persist it); reaching into the task queue.
- **Tested by:** `tests/test_goal_evaluator.py` — prompt rendering + response
  parsing in isolation, LLM call stubbed.

### Layer 4 — TaskQueue + Engine

- **Public surface:** `Engine` protocol (`devclaw/engine/__init__.py`) — one
  async callable: `(EngineRequest) → EngineResult`. `TaskQueue` lifecycle
  methods (`submit`, `cancel`, on-settle callbacks).
- **Engine implementations:** `sandcastle.py` (production, docker per task),
  `host.py` (host-side, no sandbox — dev/CI only), `stub.py` (deterministic,
  no LLM).
- **The agent and its isolation are orthogonal layers** (ADR 0001, git
  history: `git log -- docs/decisions`): the agent is what reasons and edits
  code; the box is the isolation boundary. devclaw owns the box — it issues
  `docker run --rm` itself in `engine/sandcastle.py` and hosts the agent
  inside it, rather than delegating container lifecycle, mounts, the read-only
  `~/.claude` allowlist, the kernel-side fence (`--pids-limit`, `--cap-drop
  ALL`, `no-new-privileges`; network stays host, root stays writable) and
  teardown to the agent runtime. Swapping the agent inside leaves the box
  unchanged.
- **Allowed to call:** docker socket (sandcastle only), the workspace
  filesystem.
- **Forbidden:** reading the goal store (the orchestrator passes everything the
  engine needs in `EngineRequest`); writing event lines that aren't valid
  protocol.
- **Tested by:** queue lifecycle in `tests/test_durability.py`,
  `tests/test_task_retry.py`, `tests/test_task_timeout.py`,
  `tests/test_rate_limit_pause.py`; engine/sandbox behavior in
  `tests/test_workspace_breaker.py`, `tests/test_sandbox_isolation.py`,
  `tests/test_container_hygiene.py`, `tests/test_stub_engine.py`. The stub
  engine also drives all higher-layer tests so they need no docker / no claude.

### Layer 5 — Worker harness

- **Public surface:** the `runner.py` JSON-line stdout protocol (`event:` lines
  + a single terminating `result:` line). Layer 4 (sandcastle) consumes this.
- **Behavior:** concatenates the always-on **doctrine** skills from
  `/opt/devclaw/skills/` per kind (`_common` + the `_writes-code/*` tier for
  code-writing kinds + the `<kind>/*` tier) into the brief; the sibling
  `craft/` dir (self-selected how-to guides — `frontend-design`, `playwright`)
  is **not** concatenated — `_common` points the agent to `ls`/`cat` it for the
  guide a task needs (progressive disclosure). Drops `/workspace/.mcp.json` for
  Playwright MCP, fires pre/post hooks (universal + per-repo), runs the agent
  loop, runs `verify_cmd`, emits `result:`.
- **Allowed to depend on:** the configured ACP agent (`DEVCLAW_ACP_COMMAND`,
  default `claude-agent-acp` + `claude-code`), MCP servers, the per-task
  `/workspace` git checkout.
- **Forbidden:** importing anything from the devclaw Python package (different
  container; cross-process boundary). Writing files outside `/workspace`. Using
  claude-code-specific harness features (skills/hooks `settings.json`) — see
  the model-agnostic invariants.
- **Tested by:** `tests/test_runner_skills.py`, `tests/test_runner_io.py` —
  import the module file directly and exercise pure functions with the agent
  call stubbed.

## Invariants

### Layer separation

1. **No cross-layer reach-through.** The chain is strict: `1 → 2 → 3`
   (cognition) or `1 → 2 → 4 → 5` (execution).
2. **Single source of truth per state.** Goal state in `GoalStore`
   (SQLite-backed), task state in the same `StateStore` (plus historical
   `programs` rows from the lane spec 022 retired). Each owned by
   layer 2; no caching in upstream layers; generated `.md`/`.yaml` files are
   views, never read back for decisions.
3. **Engines are pure async callables.** An engine may not assume which
   orchestrator called it: `EngineRequest` in, `EngineResult` out, no
   back-channel.
4. **Cognition callers are stateless.** Every call gets the full prompt + state
   it needs as input. No process-level memory between calls.

### Grounded cognition

Every host-side cognition caller that reasons about the target repository —
the done-gate evaluator and the pre-PR review gate — is fed a **read-only git snapshot of the
goal's actual workspace** (`task_git._review_repo_context_sync`: remote, branch,
HEAD, key-file probes, tracked layout), and its prompt forbids inferring repo
facts from the host process, cwd, or remembered repositories. Collection is
best-effort and never raises (grounding can't fail a step), runs only where
cognition already runs (the zero-token idle guard is untouched), and adds no
LLM calls. Rationale: host-side `claude` inherits devclaw's own checkout as
ambient context — ungrounded, it can substitute the wrong codebase (the #227
wrong-codebase review bug and its siblings, fixed 2026-07-13).

### OAuth and billing

`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` are **actively stripped** — both
keys `env.pop`'d — at all **five** first-party call sites, the authoritative
enforcement list: `devclaw/cognition.py`, `devclaw/llm_call.py`,
`devclaw/engine/host.py`, `devclaw/engine/sandcastle.py`, and
`runner/runner.py`. A stray key must never silently flip an
autonomous run onto metered billing.

### Model-agnostic worker layer

The worker harness (layer 5) is the *only* place model-coupling is allowed.

1. **Skills are plain markdown.** No model-specific frontmatter, no native
   `Skill(...)` invocations.
2. **Hooks are bash `.sh` files**, not harness-native config (`settings.json`).
3. **Tools cross via MCP**, not vendor wiring.
4. **Per-repo discovery is `ls` + `cat`** — no agent-specific catalog API.

Swapping `claude-code` for another ACP-speaking agent should change only the
runner's agent-drive seam — the payload/env-selectable agent command
(`acp_command` / `DEVCLAW_ACP_COMMAND`) its `AcpClient` spawns (spec 011;
enforced by the fake-agent regression tests).

### Persistence

1. **Goals are durable.** Phase/lifecycle/in_flight changes go through
   `GoalStore.transition()` — CAS'd against the `LEGAL` table inside a
   `StateStore` transaction. NOT heartbeat-exclusive: `steer_goal`,
   `resume_goal`, and `cancel_goal` write from the MCP-tool path concurrently
   with the heartbeat; the CAS is what makes that safe. Views are written
   atomically (tmp-file + `os.replace`) after each transaction commits. There
   is **no** `update_goal`/field-patch surface: a wrong contract is
   cancel + recreate. The **one** blessed exception (ADR 0007) is
   `set_goal_strictness` — a narrow single-field verb that flips only the gate
   strictness *dial* (`GoalStore.set_strictness`, atomic goal.yaml rewrite, also
   reachable via MCP/HTTP/console). It is allowed because strictness changes the
   *consequence of a gate verdict*, not the objective/done_when/backlog — it is a
   mode toggle, not a contract patch; it does not go through `transition()`
   (strictness is a goal.yaml fact, not a phase field).
2. **Tasks are append-only events.** `StateStore`'s `events` table is an
   append-only log; state views are projections. (Goal-state tables:
   `goal_status` is mutable single-row-per-key, CAS'd;
   `goal_steering`/`goal_log`/`goal_deliveries`/`goal_phase_history` are
   append-only.)
3. **Hooks may write best-effort.** Pre/post-run hooks may write scratch files;
   nothing durable beyond `hook_warnings` in the runner result.

### "Done" is a proposal

The planner's `done` triggers a read-only review against the firmed
`done_when`; the goal closes **only if the evaluator confirms `achieved`** —
never on counting PRs or backlog items. The owner notification says
"(verified)" only when a repo review actually grounded the close; an
artifact-only close (per-project `verify_done` off) is labeled as such.

Clause decomposition admits **repository behavior only**. Delivery mechanics —
how the work ships, PR count, target branch, merge state, issue/PR closure and
labelling — are dropped at step 1a of `prompts/goal-evaluator.md` and named in
the verdict's rationale, so they never become clauses and never hold a goal
open. This narrows what may BE a criterion; it relaxes nothing about how an
admitted criterion is judged, and an unmet behavior clause still fails closed
under either strictness dial.

### Loud failure over silent degradation

Verification fails closed (#186); an unreviewable change fails closed *and
fast*, not forever (#223); broken delivery fails, never "done without a PR"
(#183); lost/corrupt state blocks legibly with an owner ping (#185/#188); a
usage-limit hit *pauses-and-resumes* (one account-wide `paused_until` gates
queue and heartbeat, zero tokens while paused, auto-resumes on cap reset —
#189/#190/#191).

Blocks carry a structured `blocked_kind`, and the two re-checkable mechanical
kinds **auto-heal** (zero LLM, damped by a persisted per-goal `heal_attempts`
budget): `mechanical:corrupt_doc` once the contract file parses again (the
tick's contract probe is the recheck — free, every tick; cap 3), and
`mechanical:prep` via a `git ls-remote` recheck on a persisted exponential
backoff (`next_heal_at`, 30 min → 6 h; cap 5 — between windows a blocked goal
stays a zero-subprocess tick). Past its cap a goal parks for a human with one
plain ping. `needs_answer`, `bug`, `mechanical:lost_ref`, and
`mechanical:dispatch_cap` blocks stay human-gated on purpose; recovery verbs
are `resume_goal` (blocker cleared, same contract) and `steer_goal` (direction
change) — both restore the heal budget. Since spec 031 (2026-09-02) a
human-gated block **carries a typed Problem** — what is wrong, the `done_when`
clause, why the loop cannot decide it, bounded options, a default, a timebox —
raised through one seam (`devclaw/goal/problems.py`) in the same transaction
as the block, and resolved by exactly two verbs, `correct_implementation` and
`decide`, each recording a Decision (`goal_decisions`) and unblocking with the
steer's budget-restoring shape. A timed-out Problem takes its default and
informs; under `strict` a default that would close the goal parks instead.
`steer_goal` is refused while a Problem is open. At creation the `done_when`
admission lint (`devclaw/goal/admission_lint.py`) refuses sandbox-impossible
clauses, rewrites baseline-less absolutes, and raises an undecided design
choice as a Problem before any dispatch — its one cognition call runs at
creation, never on the tick, and fails closed: a judge reply outside the
protocol, or a caller that raises, refuses creation with nothing persisted.

## Testability (one stub at every seam)

| Seam | Stub | Where |
|---|---|---|
| LLM call (cognition) | `StubCognition` | `devclaw/cognition.py` |
| Engine | `stub_engine` | `devclaw/engine/stub.py` |
| Notifier | `NullNotifier` | `devclaw/goal/notify.py` |
| Sandbox docker | (stub engine covers the seam above it) | — |
| Worker harness | (no stub yet — runner.py exercised by module import) | gap |

Anything that needs a real `claude` call or real `docker run` is an integration
test, not a unit test. The full `pytest` run is unit-only — see
[`runbooks/live-shakedown.md`](./runbooks/live-shakedown.md) for the real
pipeline.

## Replaceability proofs

| Component | Implementations today | Proof |
|---|---|---|
| Engine (layer 4) | 3 (sandcastle, host, stub) | ✅ strong |
| Notifier | 2 (`HttpNotifier`, `NullNotifier`) | ✅ ok |
| Cognition | 2 (Claude subprocess, Stub) | ⚠ weak — only stub-vs-real |
| Worker harness (layer 5) | 1 shipped (claude-agent-acp + claude-code); command is a config seam (`DEVCLAW_ACP_COMMAND`, payload-threaded, shlex-split, tested) | ⚠ seam proven, no second implementation exercised — `acp_env`/auth mounts/model ids/limit classifiers still claude-shaped |

Closing the worker-harness replaceability gap is the highest-value next muscle.

## How to add new functionality

Before adding new code, ask in order:

1. **Does this fit an existing layer?** → Put it there. Most things do.
2. **Is it a new SURFACE on an existing layer?** → Extend the layer's protocol,
   write the conformance test, implement.
3. **Is it cross-layer machinery (skills, hooks)?** → Worker harness (layer 5),
   and it must be model-agnostic.
4. **Is it a NEW LAYER?** → **Stop.** Probably not. Re-read the contracts.
   Talk to Denys before proposing.

## The code map

```
devclaw/
├── server/          layer 1 — MCP tools, HTTP/SSE routes, auth+serve
├── goal/            layer 2/3 — the heartbeat + cognition callers
│   ├── tick.py + tick_{context,guards,dispatch,donegate,settle}.py   the loop
│   ├── store/       GoalStore package (base · status[CAS] · content)
│   ├── evaluator.py · transitions.py                                cognition + the LEGAL table
│   └── delivery_strategy.py · mergeability.py · merge_on_close.py · engine.py  dispatch seams
├── engine/          layer 4 — sandcastle.py (docker run --rm), host.py, stub.py
├── delivery/        commit → branch → push → PR; deploy.py; repo.py
├── quality/         gates past green tests — pre-PR review, browser_gate, reachability
├── loom/            engine-agnostic substrate — limits, test_integrity, trace
├── state_store/     StateStore package (rows · control · problems · observability · evals · core) — the append-only log
├── task_queue.py + queue/ + task_{git,notify}.py    layer 4 — dispatch, concurrency, settle (queue/ = the settle/admission mixins)
└── prompts/         system prompts as .md files (load_prompt(slug)); gate prompts live in quality/prompts/
runner/runner.py    layer 5 — the in-sandbox harness
```

## Where to look next

- **What runs when** → "The heartbeat is the whole machine" above, then
  `goal/tick.py`'s `_tick_goal_impl`.
- **How one task flows end to end** → [`flows/task-execution.md`](./flows/task-execution.md).
- **How a dispatch becomes a PR** → [`flows/delivery.md`](./flows/delivery.md).
- **Every env var** → [`reference/env-vars.md`](./reference/env-vars.md).
- **Why this engine shape** → the orthogonality paragraph under Layer 4 (ADR 0001, git history: `git log -- docs/decisions`).
- **Every doc, with a currency tag** → [`INDEX.md`](./INDEX.md) — read it before
  trusting any other doc.
