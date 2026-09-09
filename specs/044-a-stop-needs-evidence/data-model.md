# Data model: A stop needs evidence (Phase 1)

All rows are written only by `GoalStore` inside the transaction that raises
or resolves a block (single writer, CAS'd through `goal_status.version`).
Two existing tables gain one column each; no new table. Catalog rows go
through the existing `problems` table (`state_store/problems.py`).

## `Evidence` (new value type, `devclaw/goal/evidence.py`)

| field | type | notes |
|---|---|---|
| `citation` | str | `<shape>:<ref>` — one of `path` · `clause` · `probe` · `manifest` · `decision` · `report` (grammar in research.md D2); `""` when the seam had nothing to cite |
| `check` | str | the check applied: `path_in_tree` · `clause_pinned` · `probe_registered` · `manifest_declared` · `decision_recorded` · `report_present` · `owner` (an owner act, exempt by principle V) · `none` (no citation offered) |
| `proven` | bool | the check passed. `False` renders as **no evidence** and, under `strict`, marks the Problem an unproven stop |
| `detail` | str | one line for the reader: what was looked up and what was found (`"path not in the graded tree (c3a5853, 1 412 files)"`); `""` when proven |

**Validation**: `proven` may be `True` only with a non-empty `citation` and
a `check` other than `none`; `check="owner"` is proven by construction and
needs no citation. `Evidence.unproven(citation, check, detail)` and
`Evidence.owner()` are the two constructors seams use besides the checks
themselves, so a hand-built proven-without-citation value cannot exist.

## `goal_problems` — one new column

| column | type | notes |
|---|---|---|
| `evidence_json` | TEXT NOT NULL DEFAULT `''` | the `Evidence` as JSON (`{"citation","check","proven","detail"}`); `''` on rows that predate the migration, read back as `None` |

`Problem.evidence: Evidence | None`. `new_problem(..., evidence=)` is a
**required keyword** (the structural guard also asserts it at every call
site). `to_dict` emits `"evidence": {...} | null`. Migration: one
`ALTER TABLE goal_problems ADD COLUMN evidence_json TEXT NOT NULL DEFAULT ''`
in `goal/state.py`; doctor check `instance.goal_problems.evidence_column`.

New `ProblemOption` keys (fixed set in `problems.py`):

| key | label | consequence | closes_goal |
|---|---|---|---|
| `drop` | Drop the claim and proceed | the stop is recorded as a dropped claim; the loop continues as if it had not been made | false |
| `proceed` | Proceed — the probe is green | the worker's environment report is superseded by the probe; the next tick dispatches | false |

An unproven stop under `strict` offers `(DROP, *caller options)[:5]` with
`default_key="drop"`. A green-probe env report under `strict` offers
`(PROCEED, SUPPLY, CANCEL)` with `default_key="proceed"`.

## `goal_decisions` — one new column

| column | type | notes |
|---|---|---|
| `missed_stage` | TEXT NOT NULL DEFAULT `''` | `intake_grade` · `admission` · `plan` · `none` · `''` (not recorded). Meaningful on a Decision that resolves a **design** Problem (evidence shape `report:`, raised by `worker_block` or `done_gate`); ignored elsewhere |

`Decision.missed_stage: str = ""`. Set by `resolve_problem(...,
missed_stage=)` from the two verbs; a defaulted Decision leaves it `''`.
Migration: one `ALTER TABLE goal_decisions ADD COLUMN missed_stage TEXT NOT
NULL DEFAULT ''`; doctor check `instance.goal_decisions.missed_stage_column`.

## `problems` (catalog) — one new kind, no schema change

| field | value |
|---|---|
| `category` | `gate` |
| `kind` | `dropped_claim` (`DROPPED_CLAIM_KIND` in `state_store/problems.py`) |
| `message` | `"<seam>: <claim[:200]> — cited <citation or 'nothing'> (<check>)"`; the fingerprint's normalisation keeps the seam and the claim's words, drops ids and numbers |
| `recovered` | `True` (devclaw carried on past it) |
| `goal_id` / `task_id` | when the seam has one (intake has neither) |

Seam names (the first token of `message`, the grouping key for the cut
condition): `intake_staleness` · `env_report` · `review_refusal` ·
`readiness_revoked` · `done_gate_structural` (US2 records the follow-ups it
no longer holds on, so the count shows the path fired).

## `ReadinessVerdict` (`intake_readiness.py`) — one new field

| field | type | notes |
|---|---|---|
| `stale_evidence` | str | the `path:` citation the model gave, verbatim, `""` when absent |

`validate(parsed, *, tree: frozenset[str] = frozenset())` sets `stale=True`
only when `stale_evidence` parses as a `path:` citation whose path is in
`tree`; otherwise `stale=False` and `rationale` is prefixed with the missing
or unfound citation so the comment renders it. `intake.grade` records a
`dropped_claim` row through the store it is bound to (best-effort).

## `ChangedPath` / `in_scope_from_text` (`task_change.py`) — rule change, no shape change

`in_scope_from_text` keeps every backticked, space-free token containing a
`/` or a `.` (path-shaped). `build_paths` marks a path `in_scope` when its
class is `GATE_INPUT` — whatever source classified it (`GATE_INPUT_GLOBS`,
`ENV_DECL_GLOBS`, the install-key regex) — and a declared token matches
it by glob or exact path. A declared product path still widens nothing.

## Review verdict dict (`quality/__init__.py`) — one new key

| key | value |
|---|---|
| `dropped` | issues moved out of `blocking` because their `location` cites no path in the span or the pre-run tree; same item shape, plus `"why": "location not in the judged span or the pre-run tree"` |

`blocking` and `verdict` are recomputed after the move (the existing
reconcile invariant: `request_changes` ⇔ non-empty `blocking`).

## State transitions

No new `State` or `Event`. An unproven stop under `strict` uses the existing
BLOCK with `blocked_kind="needs_answer"` and a Problem; under `trust` no
transition happens at the seam (the caller continues on its normal path).
US2 removes one branch from `validate` (no transition involved). US3 turns
a would-be gate failure into a pass with advisories (the existing
`trust`-advisory shape, now reached under `strict` too for this one case).
