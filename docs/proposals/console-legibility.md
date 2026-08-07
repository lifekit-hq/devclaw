# Proposal — console legibility: brief-by-default tracing + state honesty

- **Status:** **P1 LOCKED (direction)** — 2026-07-29. Captured from a live dogfood
  walkthrough of the console with Denys (this session — he clicked through tasks,
  goal activity, the timeline, evals, the projects page, and the problems page and
  narrated what was missing or wrong). Same-day clarify: the P1-relevant `[OPEN]`s
  (§6 `[OPEN-1..3]`) are resolved — Denys locked on the recommended answers while
  heading to sleep; `[OPEN-4/5]` are deferred-with-owner to the P2/P3 slices they
  belong to (the clarify decides the P1 boundary, not the whole arc). Locking
  commits direction only; tranche scheduling stays Denys's call, and a locked line
  is reopenable — edit here, don't silently diverge. The state-honesty *correctness*
  half remains out of lock scope (already executing via the `console-state-honesty`
  self-goal). **Amended + re-locked 2026-08-07** (see § Amendment 2026-08-07): a
  second walkthrough sharpened Thread A #1 into a **routed task drill-in** (P1-A,
  buildable) and added **stream-tab consolidation** (P4 — direction LOCKED, shape
  `[OPEN-6..8]`); PR #474 shipped the Thread A #2 payload-render no-brainer.
- **Date opened:** 2026-07-29 · **Authors:** Denys + Claude
- **Scope note / what's already moving:** the **state-honesty *correctness* fixes**
  (the project count mismatch + the Problems-page windowing/resolution) are
  bug-fixes (spec-lifecycle exempt) and are **already in flight** as the
  `console-state-honesty` `one_shot` self-goal filed the same day — they are
  **out of this proposal's lock scope**. This proposal owns the parts that are
  new *mechanisms*: the tracing-altitude spine (Thread A) and project lifecycle
  (Thread B, P2).
- **Relates to / does not restate:** the invariants in
  [`CLAUDE.md`](../../CLAUDE.md). One is a **headline constraint** here, not a
  footnote (§2): the **zero-token idle guard** — any "brief narrative" for a *live
  or idle* task/goal must be generatable **without an LLM call on the tick path**,
  which is why §3 leans mechanical-rollup over summary-cognition for the default
  view. Builds on the console operator surface —
  [ADR 0008](../decisions/0008-console-operator-surface-p1.md) (drill-down spine +
  layer trace), [0009](../decisions/0009-console-operator-surface-p2.md)
  (health/problems), [0010](../decisions/0010-console-p3-structured-blocks.md)
  (structured blocks); this is effectively the next console P-series. The
  Problems-page resolution lifecycle overlaps `observation-resolution.md` and
  `issue-driven-pipelines.md` (N1: problems→Issues); deep-trace retention ties to
  #259 (observability retention).

## The problem — the console shows shallow labels, not what happened

Walking the live console, the same failure recurs at every level: **it renders
identifiers and class names, but won't let you see the thing behind them.**

Seven concrete observations, which cluster into two threads:

### Thread A — traceability & altitude (progressive disclosure)
1. **A task is a dead ID.** A standalone `dispatch_task` one-shot shows ID +
   workspace + "created/running" and nothing else — no way to click in and watch
   it execute.
2. **Activity events throw away their payload.** The goal activity feed renders
   the wrapper class name `ACPToolCallEvent` repeatedly; the useful content —
   `title` (the command / file), `status`, `tool_call_id`, the result — is
   present in the event and **not shown**, and even the previews are truncated.
3. **Timeline stages aren't drillable.** The goal timeline shows
   `investigating → firming → executing` as bare labels; each stage *ran*
   something traceable, but you can't click a stage to see *how it proceeded* at
   the depth a task gives you.
4. **Altitude is wrong — and the fix is NOT "show more raw."** The raw
   grep/read/cd tool-call stream is noise to a human operator. The right default
   is the **bigger picture in plain language** (*"investigated the repo,"*
   *"fixing SelectComponent search + the DataTable empty-columns crash,"* *"ran
   the build — 2 tests failed"*); the raw trace should be **stored and one click
   away**, never the front page. Same for the **evals page** — brief + informative
   (what was tested, pass/fail, the one-line why), not complex logs.

### Thread B — state honesty & consistency
5. **"Active" is undefined.** Every project reads "active," including ones dead
   for weeks. There is no lifecycle rule and no deactivate/archive verb.
6. **Surfaces disagree.** The overview counts N projects; the projects page lists
   M (observed 3 vs 6) — because each derives "how many" from a different place.
7. **The Problems page is a demoralizing lie.** "~99 problems / 0 resolved" reads
   as a pile that only grows — because it shows every root-cause fingerprint ever
   seen (lifetime, no recency window) and never reflects resolution (a problem
   whose GitHub Issue is closed is not shown resolved). It is the literal
   embodiment of the "issues coming and coming" dread — a legibility bug, not a
   backlog problem. *(#5 lifecycle + #6 count + #7 problems = the state-honesty
   thread; the #6/#7 correctness half is the in-flight self-goal.)*

## The unifying principle

> **Brief, plain-language narrative by default at every level — task, goal
> timeline, activity, evals — with the deep raw trace stored and one click away;
> and every status/count the console shows must be true and consistent, derived
> from one source with a defined lifecycle.**

Two axes: **altitude** (Thread A — progressive disclosure) and **honesty**
(Thread B — one source of truth, defined states). The data almost always already
exists (ACP event payloads, per-phase traces via `get_trace`/layer-trace, the
`problem_lifecycle` derivation, the registry rollup) — the gap is *surfacing and
truthful derivation*, rarely new instrumentation.

## The design

### Thread A — the tracing-altitude spine
- **A "brief" layer** renders a phase/task/delivery as a short narrative line(s);
  the ACP event stream and per-phase trace become the **expandable detail**
  underneath (progressive disclosure — one component pattern reused for the task
  panel, the activity feed, and the clickable timeline stage).
- **Who writes the brief** is the load-bearing `[OPEN-1]`. Because a live/idle
  task's brief must not add tick-path cognition (zero-token idle guard), the
  default should be a **mechanical rollup** — collapse a run of tool calls into
  *"read 6 files · ran the build · edited 2 components"* from the event `title`s,
  zero tokens. The existing per-delivery **summary** cognition (`goal/summary.py`)
  can enrich a *settled* delivery (post-hoc, already paid for), but must not gate
  the live view.
- **Render the payload, then summarize it:** the immediate, no-brainer win inside
  this is to stop rendering `ACPToolCallEvent` and instead show its `title` /
  `status` (the mechanical-rollup is the layer above that).

### Thread B — state honesty
- **One source for "how many projects"** (reconcile overview vs projects page) and
  a **defined "active" rule** + an **archive/deactivate** verb so dead projects
  leave the active view. *(The count reconciliation ships via the self-goal; the
  lifecycle/archive verb is P2 here.)*
- **Problems page** windowed by `last_seen` + resolution synced from closed
  Issues. *(Ships via the self-goal; captured here for completeness.)*

## Sizing — slice, firm the lockable P1 only

- **Already in flight (out of lock scope):** the state-honesty *correctness* slice
  — project-count reconciliation + Problems-page windowing/resolution — via the
  `console-state-honesty` self-goal (bug-fix exempt).
- **P1 (the lockable keystone — firm + size this):** the **tracing-altitude spine**
  — the brief-by-default + expand-to-deep pattern applied to the task-detail
  panel, the activity feed (render payload → mechanical rollup), and the clickable
  timeline stage. Highest visible-outcome value ("watch it work"), and it carries
  the one real design decision (`[OPEN-1]`). One standalone increment: even the
  task panel alone is a shippable result.
- **P2 (named, unsized):** project lifecycle — the defined "active" rule + the
  archive/deactivate verb (the new-capability half of Thread B).
- **P3 (named, unsized):** evals-page depth — brief + informative run view over
  `eval_outcomes`, deep logs on drill-down.

## §6 — `[OPEN]` items — resolutions (P1 clarify)

- **`[OPEN-1]` Who writes the "brief" for the live view? — RESOLVED.** **Mechanical
  rollup** for anything live/idle: collapse a run of ACP events into a plain line
  from their `title`s (*"read 6 files · ran the build · edited 2 components"*),
  **zero tokens, no tick-path cognition** (the zero-token idle guard forces this —
  it's not just the cheaper option, it's the only invariant-legal one for a live
  view). The existing per-delivery **summary cognition** (`goal/summary.py`) may
  enrich a *settled* delivery post-hoc (already paid for), but must never gate the
  live/idle view. This is the lock's central decision.
- **`[OPEN-2]` Default granularity — RESOLVED.** Default unit = **the brief per
  timeline phase / per delivery**, plus a single rolling **"current activity"**
  rollup line while a task is `in_flight`. Everything else (the raw ACP event
  list, the per-phase trace) is **expand-on-click**, never shown by default.
- **`[OPEN-3]` Deep-trace retention — RESOLVED (deferral-with-owner to #259).** P1
  renders the deep layer from **whatever the event store already holds** — it adds
  no new retention policy. Capping/retention of the verbose ACP stream is
  **#259's** job (observability retention); P1 just must degrade gracefully when a
  very old trace has already aged out (show "trace no longer retained", not a
  broken expand).
- **`[OPEN-4]` (P2) "Active" definition + archive semantics — DEFERRED** to the P2
  slice (not needed for the P1 lock). Has-a-non-terminal-goal vs
  activity-within-N-days vs explicit archive; soft flag vs unlinking the driving
  goal. (Interacts with the stale project↔goal link on cancel+refile.)
- **`[OPEN-5]` (P3) Eval "brief" fields — DEFERRED** to the P3 slice. What "brief +
  informative" means for one eval run — which fields are the front page vs the
  drill-down.

## The hard rule (spec-lifecycle)

The **tracing-altitude spine (P1)** and **project lifecycle (P2)** are new
mechanisms (a new rollup/summarization surface; a new lifecycle + verb) — they
**start from a LOCKED slice, no code before lock**. The state-honesty
*correctness* fixes are the bug-fix exception and are already executing via the
self-goal; if that goal's PR turns out to need a mechanism (not just a fix), it
comes back here first.

## Amendment 2026-08-07 — task-as-drill-in (a route) + stream-tab consolidation

From a **second live walkthrough** (Denys watched the `compounding-test-2026-08-07`
experiment run: he saw the in-flight task on the goal page, wanted to *click it and
trace it live*, and noted that the goal-detail tabs read as redundant). Two things
sharpen Thread A; the direction is **LOCKED**, one new slice's *shape* stays `[OPEN]`.

**Progress since the 2026-07-29 lock (Thread A #2 partially shipped):** PR **#474**
landed the "render the payload, not `ACPToolCallEvent`" no-brainer (§ The design →
Thread A) — the worker-event decoder now extracts the tool `title` + output, so the
Execution trace reads like turns instead of raw JSON. The *mechanical-rollup brief*
layer above it (`[OPEN-1]`'s resolution) is still unbuilt. (#474 also made the goal
tabs URL-addressable and fixed the timeline "skipped-phase" honesty; #475 is
delivery, tangential.)

**N-A — Thread A #1 sharpened: the task drill-in is a ROUTE, not just a panel.**
The 2026-07-29 lock framed "a task is a dead ID" as a task-detail *panel*. The
concrete shape Denys wants: a task is a **clickable navigation target** at
`/console/goals/:id/tasks/:taskId` — click a task row (or an in-flight-task chip)
→ its own page showing the worker **Execution** trace (which already exists — it's
today's "Execution" tab, currently reachable only via an in-page task-switcher) +
its PR + gate result + status. **Live** if the task is running (the same view
auto-refreshing), **frozen "history"** if it settled. The running-vs-finished
distinction Denys named falls out of this for free — same view, live vs static.

**N-B — NEW observation: stream-tab sprawl on the goal-detail page.** The goal
page carries **8 tabs** (Timeline · Tasks · Pull requests · Activity · Trace ·
Cognition · Execution · Schedule); **five of them (Tasks/Activity/Trace/Cognition/
Execution) are the same *shape* — a chronological event list — at different
altitudes/actors**, and the UI doesn't make the distinction legible, so they read
as duplicates. Precisely: Activity = goal-level event feed; Trace = goal-level,
lower altitude (cognition + dispatch per tick); Cognition = the control-plane
`claude` calls + prompts; **Execution = the *worker's* turns — that one is
task-level, mis-filed as a goal tab**; Tasks = the plan items.

**The model that resolves both (the amendment's spine):** **the task is the
drill-in unit.** A goal is a stream of tasks (+ the goal-level cognition between
them). So the target IA:
- **Goal page** → Overview/Plan (phase + milestones + the **clickable** task list)
  · **one** altitude-tagged activity feed (collapse Activity + Trace + Cognition
  into a single stream, each entry tagged by altitude and click-through to detail)
  · Pull requests · Schedule. ~4 tabs, down from 8.
- **Task page** (N-A) → that task's Execution (worker turns) + its cognition + its
  PR + gate, live or historical.

This collapses the confusing overlap into: *goal-level = one feed; task-level = one
drill-in page.* It subsumes the "Execution as a goal tab" mis-filing and directly
answers "click the task and trace it, running or finished."

**Sizing (amends § Sizing):**
- **P1-A — SHIPPED 2026-08-07 (#479): task drill-in as a route.** The existing
  worker Execution trace now has its own route `/console/goals/:id/tasks/:taskId`
  (`TaskDetail.tsx` reusing the `TaskTrace` component), and the Tasks-tab rows
  navigate to it (`MilestoneTasks` rows are `rowlink`s keyed on `parentGoalId`).
  Live while the task runs (the goal is polled so status/PR flip in place and the
  trace grows without a reload flash), frozen history once it settles. The data +
  render component already existed — this wired them to a URL.
- **P1-B — SHIPPED 2026-08-07 (this PR): PLAN.md-as-spine.** Surface the goal's
  worker-owned `PLAN.md` (the durable plan the cognition-demolition moved into a
  repo file) as a new **Plan** tab on the goal page, so the operator can *read and
  evaluate the plan itself* — the plan-as-spine Denys described. Clarify resolved:
  **where the server reads PLAN.md** → the goal's **live delivery branch**
  (`origin/goal/<id>`, best-effort short fetch), falling back to the checked-out
  ref then the working-tree file — so an *in-flight* plan shows, not only a merged
  one. Read-only, human-initiated (off the tick path → no zero-token concern),
  never mutates goal state. Backend `_read_plan_md` + `GET /goals/{id}/plan.json`;
  frontend `Plan.tsx` (dependency-free markdown render).
- **P1-C — SHIPPED 2026-08-07 (this PR): plan-as-spine landing.** The **Plan** tab
  becomes the goal's default/landing tab (was Timeline), and renders the plan as a
  spine top-to-bottom: PLAN.md, then an **"Execution — tasks against this plan"**
  section with the live milestone/task stream beneath it, each task row drilling
  into its own execution trace (P1-A route). So clicking a goal lands on
  *here's the plan → here's what's executing against it → click any task* — the
  top-down view Denys described. Reuses `Plan` + `MilestoneTasks`, no new backend.
  NOTE: still an *achievable* spine (plan-then-live-tasks), NOT per-bullet linkage
  (PLAN.md is freeform markdown with no structured task↔bullet link) — that finer
  mapping stays unsized/unbuilt.
- **P4 (NEW — named, direction LOCKED, shape `[OPEN]`): stream-tab consolidation.**
  The Activity/Trace/Cognition collapse into one altitude-tagged feed. Direction is
  locked (kill the sprawl); the *shape* is not — resolve these at its clarify step
  **before** its tranche:
  - **`[OPEN-6]`** Which streams merge, and does **Trace** (the layer-altitude view,
    ADR 0008) survive as its own thing or fold into the unified feed? (Risk:
    collapsing altitudes badly *loses* the layer-trace signal 0008 added on purpose.)
  - **`[OPEN-7]`** The unified feed's altitude tags + default filter (does it default
    to goal-altitude with cognition/worker-detail behind a toggle, or interleave all?).
  - **`[OPEN-8]`** Where the **goal-level** cognition (done-gate evaluator, decomposer,
    direction-eval — which are NOT task-scoped) lives once Execution moves to the task
    page: in the unified goal feed, or a thin "goal cognition" strip.

**Status of this amendment:** **LOCKED (direction)** — 2026-08-07. Denys asked to
"lock this behaviour for now, continue later." P1-A (task-route) **SHIPPED
2026-08-07**; **P4 (tab consolidation) is direction-locked but must not start code until
`[OPEN-6..8]` are answered at its clarify step** (behavior/IA change → the hard rule
applies). A locked line is reopenable — edit here, don't silently diverge.
