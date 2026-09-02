# Data model: Structured problem resolution (Phase 1)

All rows are written only by `GoalStore` inside the transactions that raise or
clear a block (single writer, CAS'd through `goal_status.version`). The two
new tables are append-only; "current" is a query, never an overwrite.

## `goal_problems` (new, append-only)

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | `prb_<ulid>` |
| `goal_id` | TEXT NOT NULL | FK-by-convention to `goal_status` |
| `kind` | TEXT NOT NULL | the human-gated `blocked_kind` it accompanies: `needs_answer` · `donegate_churn` · `worker_block` · `admission` |
| `raised_by` | TEXT NOT NULL | `done_gate` · `churn_park` · `worker_block` · `dispatch_park` · `admission_lint` |
| `what` | TEXT NOT NULL | what is wrong — one paragraph, devclaw-authored (evaluator rationale / worker reason / lint reason) |
| `clause` | TEXT | the `done_when` clause concerned; empty = contract-level |
| `why` | TEXT NOT NULL | why the loop cannot decide it itself |
| `options_json` | TEXT NOT NULL | 2–5 `ProblemOption`s, see below |
| `default_key` | TEXT NOT NULL | the key of the default option |
| `timebox_at` | INTEGER NOT NULL | epoch ms after which the default applies |
| `status` | TEXT NOT NULL | `open` · `resolved` · `defaulted` · `superseded` |
| `raised_at` | INTEGER NOT NULL | epoch ms |
| `closed_at` | INTEGER | epoch ms; set with any non-`open` status |
| `closed_by_decision` | TEXT | `goal_decisions.id` that closed it |

Index: `(goal_id, status)`.

**Validation**: 2 ≤ options ≤ 5; `default_key` ∈ option keys; `timebox_at` >
`raised_at`; exactly one `open` Problem per goal (enforced by the raise seam:
raising while one is open marks the older `superseded` in the same
transaction).

## `ProblemOption` (JSON inside `options_json`)

| field | type | notes |
|---|---|---|
| `key` | str | stable short key: `correct` · `accept_close` · `split` · `supply` · `cancel` · `c1`…`cN` (evaluator corrections) |
| `label` | str | one line, owner-facing |
| `consequence` | str | one line: what the loop does if chosen |
| `closes_goal` | bool | true only for `accept_close` — drives the Q2 dial rule |

## `goal_decisions` (new, append-only)

| column | type | notes |
|---|---|---|
| `id` | TEXT PK | `dec_<ulid>` |
| `goal_id` | TEXT NOT NULL | |
| `problem_id` | TEXT | the Problem it resolves; empty for an admission rewrite (no Problem was raised) |
| `clause` | TEXT | copied from the Problem, or the rewritten clause for admission |
| `verb` | TEXT NOT NULL | `correct_implementation` · `decide` |
| `option_key` | TEXT | for `decide` when an option was picked |
| `text` | TEXT | the correction (for `correct_implementation`) or the free-form decision (for `decide` with no option) |
| `provenance` | TEXT NOT NULL | `owner` · `defaulted` · `admission` |
| `made_by` | TEXT NOT NULL | `denys` (owner identity as recorded by steering today) · `tick` · `admission_lint` |
| `made_at` | INTEGER NOT NULL | epoch ms |
| `superseded_by` | TEXT | a later `goal_decisions.id` on the same clause |

Index: `(goal_id, clause)`.

**Validation**: `correct_implementation` requires non-empty `text`; `decide`
requires `option_key` or `text`; `provenance=defaulted` requires
`made_by=tick` and `option_key=default_key` of its Problem. "Current"
Decisions for a goal = rows with `superseded_by` empty, newest per clause.

## `goal_status.problem_id` (new column)

`TEXT NOT NULL DEFAULT ''` — the id of the goal's single `open` Problem, or
empty. Written in the same `transition()` that sets `phase="blocked"`; cleared
in the same `transition()` that `UNBLOCK`s. Read by: the tick's blocked branch
(timebox), `steer_goal` (refusal), `get_goal`/`goal_json` (surface).

Migration: `ALTER TABLE goal_status ADD COLUMN problem_id TEXT NOT NULL
DEFAULT ''` in the idempotent boot list beside `donegate_progress`.

## State transitions (no new `State`, no new `Event`)

```
                 raise_problem() inside the caller's BLOCK
   idle/verifying ───────────────────────────────────────▶ blocked + problem_id=P (P.open)
                                                              │
        resolve_problem(verb) ── Decision(owner) ── UNBLOCK ──┤──▶ idle, problem_id="" , P.resolved
        timebox elapsed        ── Decision(defaulted) ─ UNBLOCK┤──▶ idle, problem_id="" , P.defaulted   (trust)
        timebox elapsed, default closes goal, strictness=strict │──▶ stays blocked, notify (no Decision)
        steer_goal                                              │──▶ REFUSED (returns P + the two verbs)
        cancel_goal                                             └──▶ cancelled, P.superseded
```

A defaulted or decided *accept and close* never reaches `done` here: the goal
returns to idle, and the done-gate's next round — reading the Decision for
that clause — closes through the existing `Event.ACHIEVE` path.

## Dataclasses (`devclaw/goal/models.py`)

- `ProblemOption(key, label, consequence, closes_goal=False)` — frozen
- `Problem(id, goal_id, kind, raised_by, what, clause, why, options,
  default_key, timebox_at, status, raised_at, closed_at=None,
  closed_by_decision=None)` — frozen
- `Decision(id, goal_id, problem_id, clause, verb, option_key, text,
  provenance, made_by, made_at, superseded_by=None)` — frozen
- `GoalStatus.problem_id: str = ""`
- `ClauseVerdict` gains `resolved_by: str = ""` (a `goal_decisions.id`) —
  set by the evaluator parser when the gate grades a clause
  `resolved_by_decision`.

## Read shapes

- `GoalStore.current_problem(goal_id) -> Problem | None`
- `GoalStore.decisions(goal_id) -> list[Decision]` (current only, oldest
  first — the feed-forward input)
- `GoalStore.raise_problem(...) -> Problem` (inside a transaction; supersedes
  any open one)
- `GoalStore.record_decision(...) -> Decision` (inside a transaction; closes
  the Problem; supersedes an earlier Decision on the same clause)
