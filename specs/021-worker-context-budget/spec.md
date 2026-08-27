# Feature Specification: Worker Context-Budget Invariant (two-axis overflow class fix)

**Feature Branch**: `021-worker-context-budget`

**Created**: 2026-08-26

**Status**: Draft

**Input**: User description: "Worker context-budget invariant — the two-axis class fix for 'Prompt is too long' (devclaw#707, folds in the worker-side lane of #668). Establish structurally that no worker session's input scales with the size of the ask or the size of the repo. Axis A (chunked delivery): one worker session executes one small chunk; the plan, task list, and done-ness live as durable artifacts on the goal branch (workspace-as-memory); sessions are disposable and the next dispatch continues from the workspace, not from conversation history. Axis B (read-side diet): the build session resolves reads through distilled workspace artifacts — the repo brief (project_docs), scout-session output, per-area maps — instead of raw exploration; read amplification never meets write work in one context window. Includes the runner-side usage_update tripwire (~75% threshold → land-what's-coherent nudge over ACP) as graceful degradation plus the measurement of when A+B are done."

## The problem (why this is a class, not an instance)

"Prompt is too long" is the instance's #1 recurring mechanism-wedge (devclaw#707):
the worker conversation overflows the model context mid-run, the engine correctly
fails fast without an in-place retry (the overflow is deterministic), and the goal
loop re-dispatches fresh. Recovery works; prevention is absent. Each hit costs
~1 hour of wall clock plus a full worker session (wedged nights 2026-08-13/14/18,
daytime hit 2026-08-26 on lkc-elements-pilot).

Root cause: a worker session's input size scales with **two independent axes** —
the size of the **ask** (accumulated increments, failure history, a whole
multi-increment issue carried as one conversation) and the size of the **repo**
(raw file exploration needed to understand it). Overflow is guaranteed whenever
either axis grows past the window. Any fix addressing only one axis — or moving
the ceiling — leaves the class alive.

**The invariant this spec establishes**: no worker session's input scales with
the size of the ask (Axis A) or the size of the repo (Axis B). Durable state
lives in the workspace on the goal branch, never in any conversation.

## Clarifications

### Session 2026-08-26

- Q: When a new ask arrives, who decides whether it is a single-chunk or multi-chunk job — and therefore whether a chunk-plan artifact gets created? (FR-002/FR-005) → A: The worker classifies in-session during the planning step it already runs; the host carries no classification cognition.
- Q: What actually makes a worker session stop after one chunk — is the one-chunk limit an instruction the worker follows, or a limit the harness enforces? (FR-001) → A: Hard enforcement — the runner reads the chunk-plan artifact (a typed contract; runner reads, never writes) and terminates/settles the session when the chunk's declared scope is delivered. The skill instruction still exists, but the brake is mechanical.
- Q: Which record is authoritative for "which chunks are done" — the worker's marking in the artifact, or the host's settled-delivery record? (FR-002/FR-003) → A: The host's settle record. Each chunk session settles as a task with a verdict the host already owns; "chunks done" is derived mechanically from settled-done deliveries mapped to chunk ids. The artifact carries the plan, ordering, and distilled context — worker notes, never authoritative bookkeeping — so artifact-vs-reality divergence is structurally impossible. (Answered by Denys's standing delegation to the recommended option.)
- Q: Does a missing or corrupt chunk-plan artifact on a continuation fail loud, or self-heal by re-planning? (FR-004) → A: Fail loud and blocking, per the spec-019 load-bearing-input class — a silent re-plan could quietly diverge from what prior chunks built. (Recommended default confirmed under the same delegation.)

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A big ask survives by chunked delivery (Priority: P1)

Denys (via companion mode or a goal pointer) hands devclaw an ask the size of a
whole graded issue — e.g. "implement lifekit-common#10 in full: extract a
package, scaffold a new project, deliver a pilot, document conventions." Today
that ask rides one worker conversation and can overflow. After this story: the
worker's first session on a multi-increment ask produces a durable chunk plan in
the workspace (committed on the goal branch); every subsequent session executes
exactly one chunk, starting from a clean context plus the workspace state; the
goal loop's existing chaining dispatches the next chunk until the plan is spent.
The conversation never carries the arc — the branch does.

**Why this priority**: this is the class fix itself. Without it, every other
story is damage control around a structural defect.

**Independent Test**: seed a goal whose ask decomposes into ≥3 chunks (stub
engine, fake agent); observe that each dispatched session receives only its
chunk plus the workspace pointers (input size bounded, not growing with chunk
index), that chunk done-ness is recorded in the workspace artifact, and that the
goal completes via chaining with no session ever receiving the full-arc history.

**Acceptance Scenarios**:

1. **Given** a goal whose ask requires multiple chunks, **When** the first
   worker session runs, **Then** a chunk-plan artifact exists on the goal branch
   (plan, ordered chunk list, per-chunk distilled context) and the session
   ends after completing at most one chunk with a normal delivery.
2. **Given** a goal mid-arc (some chunks done), **When** the next session is
   dispatched, **Then** its prompt/input carries the chunk to execute and
   workspace pointers only — not the prior sessions' conversation history — and
   its input size is bounded regardless of how many chunks precede it.
3. **Given** all chunks in the plan are done, **When** the worker proposes done,
   **Then** the goal-level done-gate evaluates as today (done stays a proposal,
   gated on grounded evaluation).
4. **Given** a small single-chunk ask, **When** it is dispatched, **Then** it
   completes in one session with no added planning ceremony or extra sessions
   relative to today's behavior.
5. **Given** a continuation dispatch whose chunk-plan artifact is missing or
   corrupt, **Then** the goal blocks loud (owner-visible, actionable reason) —
   the plan is a load-bearing input, never silently re-derived (spec 019 class).

---

### User Story 2 - Sessions land instead of crashing (runner budget tripwire) (Priority: P2)

A worker session approaches its context ceiling — because a chunk was mis-sized,
or on an instance where Story 1 hasn't been exercised yet. Today it runs into
the wall at 100% and the whole session's work is lost. After this story: the
runner watches the agent's reported context usage; past a threshold (default
~75%) it injects one instruction over the agent protocol — finish the current
coherent piece, verify, commit, and report an increment now. The session settles
as a normal (possibly partial) delivery through the standard gates. Every firing
is recorded and visible to the operator, which doubles as the measurement of
whether chunk sizing is working.

**Why this priority**: it is the graceful-degradation backstop that makes every
mis-sized chunk survivable, it ships independently and smallest, and its firing
log is the instrument that tells us when Axis A/B are actually done.

**Independent Test**: with the fake agent emitting synthetic usage updates,
drive usage past the threshold and observe the runner inject the land-now
instruction exactly once, the session settle as a normal delivery, and the
firing recorded in the task's event/problem surface. Below threshold: no
injection, byte-identical behavior to today.

**Acceptance Scenarios**:

1. **Given** a session whose reported context usage crosses the threshold,
   **When** the next agent turn boundary arrives, **Then** the runner injects
   the land-now instruction once, and the session ends with a normal delivery
   that passes the standard verify path (no special-cased gate leniency).
2. **Given** a session that stays under the threshold, **Then** no instruction
   is injected and runner behavior is unchanged.
3. **Given** a tripwire firing, **Then** it is recorded on the task (event +
   problems/trend surface) so recurrences are countable per goal and per repo.
4. **Given** an agent that does not report context usage, **Then** the tripwire
   is inert and says so once, loudly, in the task record (bounded coverage is
   named, never silent).

---

### User Story 3 - Read-side diet: exploration never meets build work (Priority: P3)

A worker session on a file-heavy repo currently spends a large share of its
window on raw exploration (reading files to understand the repo) before any
work happens — and that read cost recurs in every session. After this story:
the exploration cost is paid where it can be amortized — the planning/scout
session distills what it learned into durable workspace artifacts (the repo
brief, per-area notes, a relevant-files map for each chunk) — and build
sessions are instructed (via the worker skill) to resolve reads through those
artifacts first, pulling raw files only for the surfaces their chunk touches.
Context stays PULLED by the worker (the demolition doctrine); what changes is
that the environment offers a cheaper thing to pull.

**Why this priority**: it fixes the second axis (repo-size scaling). Without
it, a single chunk that requires understanding a large surface can still
overflow. It builds on Story 1's artifacts, hence P3.

**Independent Test**: seed a workspace with a distilled brief + per-chunk file
map; observe (fake agent transcript) that the build session's read set is
bounded by the chunk's declared surface rather than repo-wide exploration, and
that a chunk whose declared surface is absent from the artifacts triggers
scout-then-build rather than silent full exploration.

**Acceptance Scenarios**:

1. **Given** a planning session on a multi-chunk ask, **Then** the chunk plan
   includes, per chunk, the distilled context a build session needs (relevant
   files/areas, constraints learned during planning).
2. **Given** a build session for a chunk with distilled context present,
   **Then** its input and read pattern are bounded by the chunk's declared
   surface (verifiable in the fake-agent regression by transcript inspection).
3. **Given** a chunk whose distilled context is missing or stale relative to
   the workspace, **Then** the session pays the scout cost explicitly (and may
   refresh the artifact) rather than silently degrading to unbounded
   exploration.

---

### Edge Cases

- **A single chunk itself overflows** (mis-sized chunk, tripwire fires or the
  session still dies): the failure context must mark the chunk as oversized so
  the next planning pass re-slices it — never an identical retry of the same
  chunk (the #707 no-futile-retry rule, applied per chunk). Repeated re-slicing
  of the same chunk hits the existing circuit-breaker/park machinery rather
  than looping.
- **Steering mid-arc**: `steer_goal` appends an input as today; the next
  session reads it from the dispatch context and the planning artifact absorbs
  any re-slicing it implies. Steering never patches the plan artifact directly
  (single-writer: the worker owns it).
- **Dispatch-cap accounting**: chunked delivery multiplies sessions per goal;
  successful chunk dispatches must remain progress-refunded against the cap
  (existing chaining behavior) so a healthy multi-chunk goal never parks on
  the cap while making progress.
- **Concurrent lane**: one-worker-per-project serialization is unchanged; the
  chunk plan is only ever written by the goal's own worker sessions.
- **Workspace reset/goal-branch rebuild**: the chunk plan lives on the goal
  branch, so delivery/branch machinery must treat it as ordinary committed
  work-product (it rides `pre_run_sha..post_run_sha` like everything else —
  ONE definition of the change, spec 013).
- **Tick path**: chunk planning happens inside dispatched worker sessions
  only; nothing in this spec adds cognition to the heartbeat (zero-token idle
  guard untouched).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A worker session MUST execute at most one chunk of a
  multi-chunk ask, and the limit is HARNESS-ENFORCED: the runner reads the
  chunk-plan artifact (a typed contract — the runner reads it, never writes
  it) and terminates/settles the session when the chunk's declared scope is
  delivered. The worker skill instructs the same stop, but the brake is
  mechanical, not instructional (a worker that routes around the instruction
  is still stopped — the #358 class).
- **FR-002**: The worker itself classifies the ask during its existing
  in-sandbox planning step — the host performs no size classification. On a
  multi-chunk ask, the first session MUST produce a durable chunk-plan
  artifact in the workspace (committed on the goal branch) recording the
  plan, the ordered chunks, and per-chunk distilled context; the worker is
  its only writer. Which chunks are DONE is not artifact state: it is derived
  mechanically from the host's settled-delivery records (settled-done task →
  chunk id), so the artifact can never disagree with reality.
- **FR-003**: A continuation dispatch MUST derive its input from the workspace
  artifact, the host-derived chunk done-ness (FR-002), and the goal's standing
  inputs (objective, steering, failure context for THIS chunk) — never from
  accumulated prior-session conversation history — and that input MUST NOT
  grow with the number of completed chunks.
- **FR-004**: A missing or corrupt chunk-plan artifact on a continuation MUST
  block the goal loudly with an actionable, owner-visible reason (load-bearing
  input, spec 019 class) — never a silent re-plan.
- **FR-005**: A single-chunk ask MUST NOT incur added ceremony: no separate
  planning session, no extra dispatches, behavior equivalent to today.
- **FR-006**: The runner MUST observe the agent's reported context usage and,
  past a configurable threshold (default ~75%), inject exactly one
  protocol-level instruction to land the current coherent piece as a verified
  increment; the resulting settle rides the standard gate path unchanged.
- **FR-007**: Tripwire firings MUST be recorded per task and surfaced through
  the existing problems/trend read surfaces so per-goal and per-repo
  recurrence is countable; an agent that reports no usage renders the tripwire
  inert with one loud note in the task record.
- **FR-008**: An overflow or tripwire firing attributable to one chunk MUST
  mark that chunk oversized in the failure context so the next session
  re-slices it; an identical retry of an unchanged oversized chunk MUST NOT be
  dispatched, and repeated re-slicing of the same chunk falls into the
  existing circuit-breaker/park path.
- **FR-009**: Build sessions MUST be instructed (worker skill, plain markdown)
  to resolve reads through the distilled workspace artifacts first, paying
  raw-exploration cost only for the chunk's declared surface, and to refresh a
  stale artifact explicitly rather than silently bypassing it.
- **FR-010**: All standing invariants hold untouched: zero-token idle, gates
  fail closed and "done" stays a proposal, single-writer-to-state, ONE
  mechanical definition of the change (spec 013), and the model-agnostic
  worker seam — every worker-facing mechanism in this spec is expressed as
  plain-markdown skill text, workspace files, or agent-protocol messages;
  no vendor-specific wiring.

### Key Entities

- **Chunk plan artifact**: the durable workspace record of a multi-chunk ask —
  plan, ordered chunks, per-chunk distilled context (relevant surface,
  constraints learned). Written only by worker sessions; read (never written)
  by the runner for session-stop enforcement and by continuation dispatches;
  a typed contract with a validated schema (an unparseable artifact is the
  FR-004 corrupt case); committed on the goal branch. Chunk done-ness is NOT
  stored here — it is derived from the host's settled deliveries (see
  FR-002), keeping one authoritative record.
- **Context-usage signal**: the agent's own report of window consumption
  (already emitted over the agent protocol), consumed by the runner for the
  tripwire and recorded for measurement.
- **Distilled read artifacts**: the repo brief (already persisted per project)
  plus per-chunk context in the plan artifact — the cheaper thing the
  environment offers a session to pull instead of raw exploration.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The #707 problems-catalog fingerprint ("Prompt is too long")
  stops accruing: zero terminal context-overflow task failures across 14
  consecutive run-nights that include at least 3 multi-chunk asks.
- **SC-002**: An ask of lifekit-common#10's shape (multi-increment issue on a
  file-heavy repo) completes end-to-end with zero operator involvement and
  zero overflow failures.
- **SC-003**: When the tripwire fires, ≥90% of those sessions settle as a
  passing delivered increment rather than a failed/lost session.
- **SC-004**: Small single-chunk asks show no regression: same session count
  and no added wall-clock ceremony versus pre-spec behavior.
- **SC-005**: Peak reported context usage per session stays below the
  tripwire threshold in ≥90% of sessions on multi-chunk goals (i.e., sizing
  works and the tripwire trends toward silence — its firing rate is the
  ratchet metric).

## Assumptions

- The agent already reports context usage over the agent protocol during
  normal operation (observed live on the current worker); agents that do not
  are handled by FR-007's inert-but-loud rule.
- The tripwire threshold is an instance-level configuration knob with a ~75%
  default, routed through the single config doorway; per-project overrides are
  out of scope until evidence demands them.
- The chunk plan builds on the worker's existing in-sandbox planning artifacts
  (the speckit-shaped plan/tasks the worker already produces) rather than
  inventing a parallel format — the delta is durability, per-chunk done-ness,
  and the one-chunk-per-session execution contract.
- Chunk sizing remains the worker's judgment, exercised during planning; this
  spec makes mis-sizing cheap (tripwire + re-slice) rather than attempting a
  mechanical token predictor at the dispatch boundary.
- Quota math: more, smaller sessions per goal is acceptable; one overflow
  today already wastes more than the marginal cost of additional sessions.

## Rejected Alternatives (direction memory)

- **Ceiling-movers — bigger context tier or mid-task agent compaction.**
  Rejected: both keep the conversation-size coupling and merely move the wall;
  compaction loses judgment silently mid-task and leans on vendor-specific
  behavior (violates the model-agnostic seam); larger tiers collide with the
  OAuth-only posture. Ruled the "workaround class" by Denys, 2026-08-26
  (devclaw#707 comment).
- **Stateless micro-turn worker** (fresh one-shot cognition call per turn,
  conversation-as-file): the limit case of Axis A — and the demolished
  host-cognition shape reborn one layer down. Recorded as the drift signal: if
  chunk sizing keeps failing and the design trends here, stop and re-read the
  demolition spine before continuing.
- **Mechanical dispatch-boundary token prediction** (ask-size × repo-weight
  projection): kept out of scope; prediction is unreliable, and the
  tripwire + re-slice loop achieves the same protection from observed reality
  instead of estimates. Revisit only if SC-005 fails while chunking is
  otherwise healthy.
- **Read-delegation via vendor sub-agents as wiring**: admissible only as
  plain-markdown skill suggestion ("use your sub-agent facility if present"),
  never as harness wiring — the seam stays model-agnostic.
