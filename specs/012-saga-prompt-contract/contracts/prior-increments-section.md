# Contract: the prior-increments section of the advance brief

The one external interface US1 adds — a marked section of the worker's
per-increment prompt. The worker return contract is explicitly OUT of scope
(FR-006); this document governs input only.

## Placement

Inside the thin-advance brief (`_advance_brief`), after the `Done when:` block
and before the failure-context and steering sections. Present on EVERY
long-lived/one-shot advance dispatch (FR-009a: re-sent in full, never
referenced).

## Format

```
<PRIOR_INCREMENTS_MARKER — fixed opening line>
This is increment {N+1} of this goal; {N} prior increment(s) have settled.
Build only on increments recorded as shipped below; treat status=failed or
gate=FAILED entries as work that is NOT present in the tree.
- {objective} → status={done|failed} gate={PASSED|FAILED} PR={url} [error={…}]
- … (newest last)
```

Only devclaw-generated facts appear (research R2): the increment's objective,
its terminal status, the verify-gate verdict, the PR url, and the recorded
error. The worker's own `Agent summary:` prose is deliberately EXCLUDED — one
worker's unverified self-report must never become the next worker's premise
(#358).

- **N = 0** (first increment): the section contains ONLY the marker line and
  the explicit absence statement — "No prior increment has settled in this
  goal — this is the first." Never omitted, never fabricated (FR-004).
- **Failed prior increment** (FR-005): its entry carries `status=failed` and/or
  `sandbox gate=FAILED` verbatim from the controlled settle header, so the
  next session sees the failure and its recorded reason rides in the separate
  failure-context section when it was the most recent settle.
- **Unreadable record** (edge case): the entry degrades to
  `- (1 increment's record was unreadable)` — the gap is stated, the dispatch
  proceeds.
- **Size bound** (FR-009b): the assembled section is tail-kept at
  `PRIOR_INCREMENTS_KEEP` (6 000 chars) behind a truncation marker that names
  the elision and points at the full deliveries record. Newest entries always
  survive.

## Display contract (#547/#550)

The section is worker INPUT. Every human-facing surface renders the brief via
`advance_brief.display_goal`, which annotates `+prior increments` — the raw
section never reaches PR titles/bodies, notifications, the goal log head, or
delivery records (the ref is re-stamped with the display form at dispatch,
unchanged by this feature).

## Invariants

- Composed only on the work-present/cadence-due path — an idle or blocked tick
  performs no delivery read (constitution III; SC-007).
- Read from `goal_deliveries` rows only — never from `deliveries.md`
  (constitution IV).
- Pure mechanism — no LLM call anywhere in composition (FR-013).
- Renderer never raises; any internal failure degrades to a stated gap
  (constitution VI).
