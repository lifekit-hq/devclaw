# Contract: done-gate evaluator changes

Layer 3 (`devclaw/goal/evaluator.py` + `devclaw/prompts/goal-evaluator.md`).
One new blank-safe input, one new clause-verdict kind, one new rule in the
prompt. Every existing call site and test stub stays byte-unaffected.

## Input

`evaluate(..., decisions: str | None = None)` — the rendered Decisions
section (`decisions.render(store.decisions(goal_id))`), collected at the
layer-2 call site in `tick_donegate.py` like every other grounding input.
When `decisions` is non-blank the prompt gains a `DECISIONS` block in the
grounding context (the #227 shape — a labelled block plus the standing rule
that absent ⇒ unknown, present ⇒ authoritative):

```
DECISIONS (the owner's recorded rulings on this goal — authoritative):
<rendered section>
```

## Prompt rule (one sentence, stated once, in the procedure section)

> A clause that has a current Decision in DECISIONS is graded
> `resolved_by_decision` with that Decision cited as its evidence, and
> counts as satisfied; do not re-evaluate it against the repository.

The literal header `DECISIONS` is referenced in prose as *Decisions* (no
`##`), per the cognition-prompts rule, so its absence is testable.

## Output

The per-clause object gains an optional field:

```json
{"clause": "...", "satisfied": true, "evidence": "resolved by decision dec_… (owner, 2026-09-02): …", "resolved_by": "dec_…"}
```

`_parse_clauses` accepts `resolved_by` (string, optional) and sets
`ClauseVerdict.resolved_by`; a clause with `resolved_by` set is treated as
satisfied for the aggregate verdict exactly like any satisfied clause. An
undocumented or malformed `resolved_by` is ignored (the clause is graded on
its `satisfied`/`evidence` as today) — an unknown model output field is never
honoured (#233).

## Problem raising from the verdict

- `needs_human` → `raise_problem(kind=needs_answer, raised_by=done_gate,
  what=question, clause=<the unsatisfied clause the question concerns, or
  contract>, why=rationale, options=corrections→options + fixed tail,
  default=first correction or accept_close when none)`.
- churn park → `raise_problem(kind=donegate_churn, raised_by=churn_park,
  what=rationale[:1000], clause=<latest unsatisfied>, options=fixed
  churn set, default=correct)`.

Nothing else in the evaluator's contract changes: verdict vocabulary,
corrections, structural axis, and the single ACHIEVE emitter are untouched.
