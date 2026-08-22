# Data Model: 012 US1 — increment feed-forward

No new storage. US1 reads existing rows back; the only new artifacts are a
read accessor, a pure renderer, and a prompt section.

## Existing entities (read, unchanged)

### Delivery record (`goal_deliveries` row)
- **Written by**: `tick_settle._resolve_polling_action` on EVERY settle
  (done / failed / gate-FAILED), inside the atomic settle transaction,
  idempotent by `ref_id`.
- **Fields used**: `ref_id` (join key), `instruction` (the increment's display
  objective, #550 re-stamped), `body` (the rendered block
  `## [ts] instruction\n\n<engine._task_detail output>`), insertion order
  (position source).
- **Body sections, by trust** (research R2): `PR: <url>`,
  ``Verify gate `cmd`: PASSED/FAILED`` and `Error:` are devclaw-generated and
  MAY be fed forward; `Agent summary:` is the worker's own prose and MUST NOT
  be (#358).
- **Constraint honored**: rows are the source of truth; `deliveries.md` is a
  generated mirror and is never read (constitution IV).

### Settlement record (`goal_settlements` row)
- **Written by**: the same atomic settle transaction, idempotent by
  `UNIQUE(goal_id, ref_id)`.
- **Fields used**: `ref_id` (join key), `status` (`done` / `failed` — the
  authoritative terminal verdict).

### Advance brief (`goal/tick._advance_brief` output)
- Machine-facing worker input; display surfaces render the embedded objective
  (`advance_brief.display_goal`). Gains one new marked section (below).

## New/changed surfaces

### 1. Read accessors (new, read-only)
- `GoalState.delivery_records(goal_id) -> list[tuple[str|None, str, str]]` —
  `(ref_id, instruction, body)`, oldest first.
- `GoalState.settlement_statuses(goal_id) -> dict[str, str]` — `ref_id → status`.
- `GoalStore.increment_records(goal_id) -> list[IncrementRecord]` — ingests the
  legacy mirror, reads both, joins by `ref_id`, and returns the parsed
  per-increment facts. No mutation, no transaction; callable from the tick path.

### 2. `devclaw/goal/prior_increments.py` (new module)

`IncrementRecord` (frozen dataclass): `objective: str`, `status: str | None`,
`gate: str | None` (`"PASSED"`/`"FAILED"`), `pr_url: str | None`,
`error: str | None`, `readable: bool`.

`parse_record(ref_id, instruction, body, status) -> IncrementRecord` — pulls
ONLY the devclaw-generated lines out of the body (`PR:`, ``Verify gate …:``,
`Error:`); the worker's `Agent summary:` prose is deliberately dropped (#358).
Never raises: an unparseable body yields `readable=False`.

`render(records: list[IncrementRecord]) -> str` — pure, never-raises.

- **Output** (always non-blank):
  - Position line: `This is increment {N+1} of this goal; {N} prior
    increment(s) have settled.` — with N = 0, the explicit absence statement
    (FR-004) and nothing else.
  - Fixed imperative line: build only on increments recorded as shipped; treat
    `status=failed` / gate `FAILED` entries as work NOT present in the tree.
  - Per-increment entry, newest last:
    `- {objective} → status={status} gate={gate} PR={url}` plus a truncated
    `error=` clause when present; an unreadable record renders
    `- (1 increment's record was unreadable)`.
  - Bounded by `prompt_budget.cap_prior_increments` (6 000 chars) — tail-keep,
    loud elision.

### 3. `advance_brief.py` additions
- `PRIOR_INCREMENTS_MARKER` — the section's opening line prefix; the
  generator builds from it, `display_goal` detects on it (never-drift).
- `display_goal` annotates `+prior increments` when the marker is present.

### 4. `goal/tick.py` changes
- `_advance_brief(goal, steering, failure_context="", prior_increments="")` —
  blank-safe kwarg; non-blank renders the marked section between the
  `Done when:` block and the failure-context section.
- `_handle_long_lived_advance`: after the `should_plan` gate passes, read
  `store.delivery_blocks(goal_id)`, render, and pass `prior_increments=`.
  Idle/blocked paths untouched (zero-token guard).

### 5. `prompt_budget.py` additions
- `PRIOR_INCREMENTS_KEEP = 6_000`
- `PRIOR_INCREMENTS_TRUNCATION_MARKER` (names the elision + where the full
  record lives)

## State transitions

None. No new events, no new phases, no writes on the read path.
