# Data model — spec 035 pinned done-gate clauses

## Table: `goal_contract_pins` (new, `devclaw.db`)

One row per (goal, contract revision). History retained (clarify Q3);
written only by GoalStore (Principle IV).

| Column | Type | Meaning |
|---|---|---|
| `goal_id` | TEXT NOT NULL | the goal this pin belongs to |
| `revision` | TEXT NOT NULL | the existing content digest of the live contract (`sha256(contract)[:12]`, as logged today by `tick_donegate._live_contract`) |
| `clauses` | TEXT NOT NULL | JSON array of clause records (below) |
| `ceremony_drops` | TEXT NOT NULL | JSON array of dropped ceremony clauses (verbatim text + one-line reason), recorded once at pin time (FR-005) |
| `pinned_at` | INTEGER NOT NULL | ms epoch of the harvesting round |
| `pinned_by_round` | INTEGER NOT NULL | the `donegate_rounds` value of the harvesting round |
| `recovery` | TEXT NOT NULL DEFAULT '' | non-empty iff this pin replaced a corrupt/missing one (FR-006) — the recorded reason |

`PRIMARY KEY (goal_id, revision)` — the "at most one decomposition per
revision" invariant is a constraint, not a convention.

## Clause record (entries of `clauses` JSON)

| Field | Type | Meaning |
|---|---|---|
| `id` | string | `c1..cN`, assigned mechanically at pin time, unique within the pin |
| `text` | string | verbatim clause text — the identity pair with `id` (clarify Q1) |
| `satisfied` | bool | current accounting state (US2) |
| `evidence` | string | the citation from the round that satisfied it (`""` while open) |
| `satisfied_round` | int \| null | round number that flipped it to satisfied |
| `via_decision` | string | Decision id when satisfied by a recorded Decision (clarify Q4); `""` otherwise |
| `carried_from` | string | on a re-pin: the prior revision's clause id this entry inherited from (byte-identical text, FR-003); `""` otherwise |

## Verdict-shape delta (evaluator JSON, pinned mode only)

- Each entry in the model's `clauses` array MUST carry the pinned `id`.
  Unknown or missing id ⇒ `GoalEvalError` ⇒ round fails closed, no churn
  increment (research D4).
- A clause entry flipping a previously-satisfied id to unsatisfied MUST
  carry `flip_cause` (string): a repo change in the span since the
  satisfying evidence, or a named defect in that evidence (FR-011). Absent
  ⇒ malformed round.
- Decomposition mode (no pin exists) is byte-identical to today's schema —
  the harvest happens host-side from the parsed result.

## State transitions

```
(no pin for revision R)
  round runs in decomposition mode
    ├─ parse OK  → pin written (ids assigned, drops recorded) + round judged
    └─ parse/crash → #186 fail-closed round, NOTHING pinned

(pin exists for R)
  round runs in pinned mode against the clause list
    ├─ judgment  → accounting updated (satisfied set grows, or flips with cause)
    ├─ malformed → fail-closed, no churn increment, pin untouched
    └─ digest ≠ R (amendment) → decomposition mode for R2; byte-identical
        clauses inherit satisfied/evidence/via_decision (carried_from set);
        rationale names the revision change

(pin row unreadable/corrupt)
  → decomposition mode; new pin written with `recovery` reason (FR-006)
```

## Interaction with existing state

- `goal_status.donegate_progress`: unchanged column, now computed as
  `|{c: c.satisfied}|` against the pinned denominator (US2).
- `goal_status.donegate_rounds`: incremented only by judgment rounds
  (research D4).
- `goal_decisions` (spec 031): unchanged; `via_decision` references it.
- Doctor (`check_contract_pins`): table present post-migration; every row's
  `goal_id` resolves; the newest revision row per active goal parses as
  valid JSON with unique ids — FAIL on any violation, with a seeded-fault
  test (FR-008).
