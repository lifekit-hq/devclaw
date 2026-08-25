# Console universal row drill-ins — increment 1: Evals page

**What:** Add click-to-expand detail panels to every eval-outcome row and every cycle-report row in the console Evals page, exposing every field the backing store row holds. Follow-up increments (increment 2) audit and add drill-ins to remaining console tables.

**Context:** The Evals page shows two tables whose backing `eval_outcomes` and `cycle_reports` rows hold more data than the rows display. The outcome table hides `workspace_dir`, `program_id`, `ticket`, `attempts`, `wall_ms`, `error`, `report_ref`, `goal_id`, `task_id` (the last two link to existing drill-ins). The cycle-report cards hide `window_start_ms`, `window_end_ms`, `sent_at`, `created_at`, and the full parsed `wedges_json`/`pauses_json` arrays. Goals and tasks already have drill-ins (the reference pattern).

**Requirements:**
- R1: Clicking an eval-outcome row expands an inline detail panel showing all stored fields; `task_id` and `goal_id` link to their existing drill-ins at `/console/tasks/:id` and `/console/goals/:id`.
- R2: Clicking a cycle-report card expands an inline detail panel showing `window_start_ms`, `window_end_ms`, `sent_at`, `created_at`, plus the wedges and pauses arrays parsed from their JSON strings.
- R3: New detail endpoints `GET /evals/outcomes/{id}.json` and `GET /evals/cycles/{cycle_key}.json` follow the existing un-prefixed `.json` convention; both 404 on unknown keys.
- R4: Both endpoints have wire-shape regression tests in `tests/test_console_evals_endpoint.py`.

**Plan:**
- `devclaw/state_store/evals.py`: add `get_eval_outcome(id)` and `get_cycle_report(cycle_key)` — pure SELECTs, plain dicts or None.
- `devclaw/server/routes/evals.py`: register both detail routes.
- `console/src/api.ts`: add typed fetch wrappers.
- `console/src/pages/Evals.tsx`: inline expand UX for both tables (click row → expand; click again → collapse; Esc closes).

**Tasks:**
- [x] state store methods
- [x] route endpoints + validation
- [x] api.ts wrappers
- [x] Evals.tsx expand UX
- [x] wire-shape regression tests
- [x] pytest suite green

**Done-When:** Every eval-outcome row and cycle-report row in the Evals page expands to show all its stored fields; task_id/goal_id in outcomes link to their drill-ins; both new `.json` endpoints return correct shapes with 404 on unknown; `pytest` suite passes.
