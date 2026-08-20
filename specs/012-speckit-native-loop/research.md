# Research: Speckit-Native Amputation

All findings are from a direct scan of the tree at `8fe277f` and from the live
VPS instance. No external research was required; nothing here is inferred from
documentation alone.

## Decision 1 — the program vocabulary is unreachable, not merely unused

**Method**: enumerated every `Action(...)` construction site in `devclaw/goal/`.

**Finding**: exactly two exist — `tick.py:485` (`tool="implement_feature"`) and
`tick_donegate.py:558` (`tool="review_repository"`), both hardcoded. The
`start_program` branch at `goal/engine.py:69` can therefore never be entered.
Independently, `task_queue.py:59` raises `PlannerError` for any
`submit_program()` without pre-planned tasks.

**Decision**: delete the vocabulary rather than deprecate it.
**Alternatives considered**: keep as "might be useful for multi-task goals" —
rejected, because speckit's `tasks.md` *is* the task DAG and lives in git.

## Decision 2 — the cut is whole-function, not refactoring

**Method**: per-function `program` reference counts across all 10 affected files.

**Finding**: the surface decomposes into ~30 whole deletable functions plus ~15
surgical call-site edits. In `task_queue.py`, 8 functions account for ~387 lines
(`_no_host_planner`, `cancel_program`, `submit_program`, `_maybe_terminalize`,
`_schedule_program`, `_persist_plan`, `start_planned_program`,
`_plan_and_start`). `state_store/core.py` has 10 whole program methods.
`task_notify.py`'s program half is one function.

**Decision**: order the commit-series whole-functions-first, so the compiler and
suite surface a missed call site immediately.

## Decision 3 — schema untouched (owner-ruled)

**Finding**: `programs` table plus five program-era `tasks` columns
(`program_id`, `depends_on`, `order_idx`, `milestone`, `plan_key`),
`project_docs`, and trend `meta` rows.

**Decision**: no migration in this PR. `pre-amputation-v0.3.0` rolls back code,
not data; a destructive migration would make rollback asymmetric against a live
instance holding 18 goals of history.

**Consequence discovered during the scan**: `list_pending_standalone` selects
`WHERE program_id IS NULL`. Because history retains populated `program_id`
values, **this filter must be kept**. Removing it as "now vacuous" would allow a
historical pending program-child row to be claimed and launched. This is the
single highest-risk edit in the arc and carries a named regression test.

**Sub-decision**: leave the `_bootstrap` DDL that creates the `programs` table.
A fresh database and the live database then share one schema shape, and the
single-writer principle stays honest — the table exists and nothing writes it.

## Decision 4 — dispatch tool names survive, implementations do not

**Finding**: the OpenClaw waiter drives devclaw entirely over MCP and lives
outside this repo. `tools.py:519` shows the house precedent: `start_program` was
kept as deprecated sugar over `create_goal(mode='one_shot')`, not deleted.

**Decision**: `dispatch_task` / `implement_feature` / `fix_bug` /
`review_repository` / `onboard` become sugar; the direct-dispatch implementation
is removed so `queue.submit()` loses every caller outside the goal layer.

**Genuinely removed tools (6)**: `get_program`, `list_programs`,
`cancel_program`, `start_program`'s program internals, `review_trends`,
`scope_grill`. Surface: **47 → 42**.

## Decision 5 — the brief leak is two defects, one root

**Method**: reproduced against the real code.

```
is_advance_brief(brief)      = True
is_advance_brief(dispatched) = False        # repo-notes prefix defeats startswith()
TITLE = [Repo notes — observations handed back by previous devclaw runs on this…
```

**Findings**: (a) `advance_brief.is_advance_brief()` uses
`strip().startswith(MARKER)`, which `repo_brief.render_brief_prefix()` defeats
by prepending at `tick_dispatch.py:186`; (b) `task_queue.py:1333` passes
`deliver_change(goal=...)` the task row's **raw** text while `tick_dispatch`
already computed a sanitized `display` form for the ref.

**Decision**: cutting `repo_brief` removes the prefix, but the *class* survives
until delivery stops receiving worker-input text. Both fixes ship
(FR-005, FR-010), each with a named regression test.

## Decision 6 — what is NOT cut, and why

`deploy/` and `goal/triage.py` both fail this spec's own test: neither exists
because there was no speckit, and both are live. `intake.py` /
`intake_readiness.py` / `grade_backlog` are the pipeline that produces current
work. All deferred or out of scope by owner ruling.

## Baseline evidence

- Suite at `8fe277f`: `1990 passed, 4 skipped in 61.02s`
- MCP surface: 47 tools (`grep -c '@mcp.tool'`)
- Live instance: 18 goals, 4/19 clean cycles, 89 problem fingerprints,
  **0** `donegate_churn` occurrences — the evidence that demoted US4 to P2
