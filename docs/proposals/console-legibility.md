# Proposal — console legibility: brief-by-default tracing + state honesty

- **Status:** **DRAFT** — 2026-07-29. Captured from a live dogfood walkthrough of
  the console with Denys (this session — he clicked through tasks, goal activity,
  the timeline, evals, the projects page, and the problems page and narrated what
  was missing or wrong). The `[OPEN]`s in §6 must be resolved (or deferred-with-
  owner) before any *lockable* slice flips to LOCKED.
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

## §6 — `[OPEN]` items (mandatory clarify before LOCK of P1)

- **`[OPEN-1]` Who writes the "brief" for the live view?** Mechanical rollup
  (zero-token, groups event `title`s) vs the existing summary cognition (richer,
  but LLM). *Recommendation: mechanical rollup for anything live/idle (invariant-
  forced); summary-cognition only to enrich settled deliveries. This is the lock's
  central decision.*
- **`[OPEN-2]` Default granularity.** What's the "bigger picture" unit shown by
  default — a rolling "current activity" line, per-phase, per-delivery? How much
  collapses before the operator has to expand.
- **`[OPEN-3]` Deep-trace retention.** The raw ACP stream is verbose; how long is
  it kept / is it capped (ties to #259 observability retention). Progressive
  disclosure is only honest if the deep layer is actually there when clicked.
- **`[OPEN-4]` (P2) "Active" definition + archive semantics.** Has-a-non-terminal-
  goal vs activity-within-N-days vs explicit archive; and is archive a soft flag
  or does it unlink the driving goal. (Interacts with the stale project↔goal link
  on cancel+refile.)
- **`[OPEN-5]` (P3) Eval "brief" fields.** What "brief + informative" means for one
  eval run — which fields are the front page vs the drill-down.

## The hard rule (spec-lifecycle)

The **tracing-altitude spine (P1)** and **project lifecycle (P2)** are new
mechanisms (a new rollup/summarization surface; a new lifecycle + verb) — they
**start from a LOCKED slice, no code before lock**. The state-honesty
*correctness* fixes are the bug-fix exception and are already executing via the
self-goal; if that goal's PR turns out to need a mechanism (not just a fix), it
comes back here first.
