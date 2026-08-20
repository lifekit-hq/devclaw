# Data Model: what becomes orphaned

This feature **adds no entity and alters no schema** (FR-001a). It records what
the amputated code stops reading.

## Orphaned after the cut — left in place as history

| Object | Shape | Why it stays |
|---|---|---|
| `programs` table | id, status, goal, workspace_dir, timestamps | Live instance holds real history; dropping breaks rollback symmetry |
| `tasks.program_id` | FK to `programs`, nullable | **Load-bearing after the cut** — see below |
| `tasks.depends_on`, `order_idx`, `milestone`, `plan_key` | DAG ordering fields | Populated in history; unread after the cut |
| `project_docs` (`repo_brief` kind) | workspace-keyed text blob | Repo-brief accumulation; unread after `repo_brief.py` is deleted |
| `meta` rows: `trend_bookmark:<workspace>`, trend cooldowns | key/value | Unread after the trend stack is deleted |

## The one field that stays functional

`tasks.program_id` is **not inert**. `StateStore.list_pending_standalone()`
selects `WHERE program_id IS NULL AND status = 'pending' ORDER BY created_at
ASC`. History retains rows with a populated `program_id`; if any is still
`pending`, dropping the filter would make it claimable by `_pump()` and launch a
container for a program that no longer exists as a concept.

**Rule**: the `program_id IS NULL` guard is retained verbatim. A named
regression test pins it.

## Entities removed from the code model

`Program` (`state_store/rows.py:151`), `PlannedTask` (`program_plan.py`), the
`program` members of `InFlight` and `PollResult` (`goal/models.py`), and
`ref_kind == "program"` handling in `goal/engine.py`.

## Follow-up (not this PR)

A later migration may drop the orphaned table, columns and rows, once the
amputated build has run several clean nightly cycles.
