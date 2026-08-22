# Research: Saga & Unit-of-Work Prompt Contract — US1 (increment feed-forward)

**Scope**: US1 only (FR-001..FR-006, FR-009, FR-009a/b as they bear on the
feed-forward section, FR-013, FR-014). US2 (saga authoring slots) and US3
(expected increment count) are out of this slice per the spec's priority rules.

## R1. Where the increment prompt is composed today

**Decision**: The feed-forward lands in `goal/tick.py::_advance_brief` — the
single generator of the worker's per-increment prompt on the one execution
path (spec 008 shrink: `_handle_long_lived_advance` serves both modes).

**Rationale**: `_advance_brief` already carries the saga-level framing
(objective, `done_when`) plus two per-increment context sections (steering,
failure context) behind shared marker constants in `devclaw/advance_brief.py`
(the #547/#550 never-drift contract between generator and detectors). Adding a
third marked section follows the exact established shape. The worker return
contract (`runner/runner.py`) is untouched — FR-006 holds by construction.

**Alternatives considered**:
- *Injecting at the dispatch site (`tick_dispatch._dispatch_action`) like the
  repo brief prefix*: rejected — the repo brief is cross-goal, workspace-scoped
  context and rides as a prefix precisely because it applies to any dispatch on
  the repo. The feed-forward is goal-scoped saga state; it belongs in the brief
  the goal layer composes, next to steering/failure context, where the
  display-stripping contract (#550) already governs it.
- *A file the worker reads in-sandbox (STATUS.md / deliveries.md)*: rejected by
  the spec itself (FR-009a, ruled 2026-08-22) — a pointer is a request while a
  slot is a fact; and constitution IV forbids reading markdown views back for
  decisions.

## R2. Source of delivery outcomes — and the trust boundary that shapes it

**Decision**: Join `goal_deliveries` rows (objective + devclaw-generated
outcome lines) with `goal_settlements` rows (terminal status) by `ref_id`, both
read through the store; never `deliveries.md`. **Feed forward only
devclaw-CONTROLLED facts — never the worker's own `Agent summary:` prose.**

**Rationale**: The code read corrected an initial assumption. The delivery
`body` is NOT devclaw's controlled settle header — that header
(`tool=… id=… status=…`) is built in `tick_settle` as `finished_detail` and is
never persisted to the row. What IS persisted is
`engine._task_detail(...)`, whose sections are:

| Section | Origin | Trustworthy as evidence? |
|---|---|---|
| `PR: <url>` | `task.pr_url` — devclaw | YES |
| `Agent summary:\n…` | the worker's own free text | **NO — #358** |
| `Verify gate \`cmd\`: PASSED/FAILED` | `verify.passed` — devclaw | YES |
| `Gate output (tail):` | the gate's own stdout | yes, but bulky |
| `Error:\n…` | `task.error` — devclaw | YES |

plus `instruction` (the #550-re-stamped display objective — devclaw) and,
in `goal_settlements`, the terminal `status` (done/failed — devclaw).

Feeding the `Agent summary:` block forward would make one worker's unverified
self-report the next worker's premise. That is precisely the channel the
dispatch-time ref re-stamp already closes ("prior workers' unverified hints must
not ride that channel as evidence" — `tick_dispatch`, invariant-guard finding),
and the standing doctrine that a worker's done-claim is never trusted on faith
(#358). The renderer therefore EXTRACTS the controlled fields and drops the
prose. This also keeps entries compact, serving FR-009b.

The spec's US1 assumption still holds: no new storage and no new reasoning —
only existing rows fed forward.

**Alternatives considered**:
- *Passing the raw delivery body through*: rejected — carries the worker's
  self-report as evidence (#358) and is unbounded per entry.
- *Reusing `recent_deliveries()` (the joined text tail)*: rejected — it is the
  done-gate's 24 KB grounding tail of full bodies, including the agent prose;
  wrong content and wrong size for a re-sent-every-increment section. It stays
  untouched for the done-gate.
- *Deriving failure from `Error:` presence instead of joining settlements*:
  rejected — the terminal status is the authoritative, devclaw-owned signal;
  inferring it from the shape of a text blob is the kind of parse that rots.
- *A new table / new columns*: rejected — nothing new to store.

## R3. Compact rendering + size bound (FR-009b)

**Decision**: A new pure, never-raises renderer module
`devclaw/goal/prior_increments.py`: per settled increment it emits one compact
entry — timestamp, the increment's objective (`instruction`), and the
controlled settle header line (which carries status, sandbox-gate verdict, and
PR url) — newest last. The assembled section is bounded via the existing
`prompt_budget.cap_section` with its own keep constant
(`PRIOR_INCREMENTS_KEEP = 6_000` chars) and a loud tail-keep truncation marker
naming that older increments were elided (constitution VI: bounded coverage
says so out loud). A malformed/unreadable block degrades to an explicit
"1 increment's record was unreadable" line, never an exception (the snapshot
collectors' best-effort convention).

**Rationale**: The #422/#431 class (row-bounded but not size-bounded sections)
already has a shared fix — `prompt_budget` — and the cognition-prompts rule is
to reuse it, not re-derive it. 6 KB keeps the re-sent-per-increment cost far
below the log/deliveries budgets while holding tens of compact entries; SC-006
is measured across the whole saga, and compactness is what makes FR-009a's
re-send affordable.

**Alternatives considered**:
- *Full delivery bodies in the brief*: rejected — unbounded growth is the exact
  failure mode FR-009b names; the worker can read the repo itself for detail.
- *LLM-summarized feed-forward*: rejected — FR-013 forbids new reasoning on the
  recurring cycle; constitution III. Mechanism only.

## R4. Position statement (FR-002) and the empty case (FR-004)

**Decision**: The section always opens with a position line derived from the
delivery-row count: "This is increment N+1 of this goal; N prior increment(s)
have settled." With zero rows it states explicitly: "No prior increment has
settled in this goal — this is the first." The renderer therefore ALWAYS
returns non-blank on the advance path; `_advance_brief` takes it as a blank-
safe optional kwarg (`prior_increments: str = ""`) so every existing call site
and test stub remains byte-unaffected (the cognition-prompts blank-safe rule),
and the advance path always passes a rendered value.

**Rationale**: FR-004 requires the absence to be stated, never omitted — so the
blank-safe omission convention applies to the *kwarg*, not to the advance
path's rendering. The count is a cheap mechanical derivation (row count), which
answers the spec assumption that a position slot must not be a redundant
restatement: position here is computed from settlements, not copied from the
task graph.

**Alternatives considered**:
- *Reading tasks.md position from the workspace*: rejected — requires a
  checkout probe on the tick path and duplicates what the settled-row count
  already proves; the task graph stays the worker's artifact.

## R5. Failed and rejected prior increments (FR-005, edge cases)

**Decision**: Failed settles render with their status and gate verdict verbatim
from the settle header (`status=failed`, `gate=FAILED`), and the section's
fixed preamble carries one imperative line: build only on increments whose
record shows they shipped; a failed or gate-failed increment's work must not be
assumed present. The existing `failure_context` section (the immediately-
preceding failure, verbatim, 800 chars) is kept unchanged — it is the ADAPT
signal for the *next* attempt; the feed-forward is the durable saga history.
Their coexistence is deliberate, not duplication: one is depth on the last
failure, the other is breadth over all settles.

**Alternatives considered**:
- *Folding failure_context into the feed-forward*: rejected — the failure
  context carries the terminal reason at up-to-800-chars depth, which the
  compact per-increment entry deliberately does not; merging would either
  bloat every entry or lose the ADAPT depth.

## R6. Display half (#547/#550)

**Decision**: A new shared marker constant `PRIOR_INCREMENTS_MARKER` in
`devclaw/advance_brief.py`, and `display_goal` learns to annotate briefs
carrying the section (`+prior increments`), exactly as it does for steering
and failure context.

**Rationale**: The 2026-08-19 night-run lesson encoded in `display_goal`: two
dispatches with the same objective must not collapse to identical log lines
when their briefs differ. The generator/detector never-drift contract requires
the marker to live in the shared module.

## R7. Zero-token guard placement (FR-013, SC-007, constitution III)

**Decision**: The delivery read + rendering happen in
`_handle_long_lived_advance` strictly AFTER the `should_plan` gate returns
true — on the work-present/cadence-due path only. Idle and blocked ticks are
byte-identical to today; the existing `FakeClaude.calls == 0` guard tests must
stay green, and a named test asserts no delivery read occurs on the idle path.

**Alternatives considered**: none viable — reading before the gate would add
per-tick work on idle goals for no consumer.
