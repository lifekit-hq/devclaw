# Console universal row drill-ins — increment 3: CycleDetail summary + Usage per-project

**What:** Two targeted fixes from the auto-eval steering after increment 2.

**Context:** Increment 2 (PR #697) incorrectly closed the Usage per-project table as
"clean" (worker_tasks_with_usage and the cognition/worker cost breakdowns are in the
wire response but not exposed in the UI). It also left the summary field absent from
CycleDetail even though it's shown in the CycleRow header and is returned by the
/evals/cycles/{key}.json endpoint.

**Requirements:**
- R1: `CycleDetail` (Evals.tsx ~529-599) renders the `summary` field so the expanded
  view shows the full report text, not just the row header preview.
- R2: Each per-project row in Usage.tsx (~173-210) is clickable and expands an inline
  detail panel showing `worker_tasks_with_usage`, `cognition_cost_usd`, and
  `worker_cost_usd` — the UsageBucket fields not shown in the table columns.
- R3: A regression test in tests/test_usage_endpoint.py pins that by_project rows
  include `worker_tasks_with_usage`, `cognition_cost_usd`, and `worker_cost_usd`.

No new backend endpoints needed — all fields are already returned by /usage.json and
/evals/cycles/{key}.json.

**Plan:**
- `console/src/pages/Evals.tsx`: add `summary` field to CycleDetail, rendered before
  the wedges/pauses section.
- `console/src/pages/Usage.tsx`: add `expandedProject` state; make each project `<tr>`
  clickable; add a `ProjectDetail` inline `<tr>` after the clicked row showing the
  three hidden UsageBucket fields; use React.Fragment keyed on project_id.
- `tests/test_usage_endpoint.py`: add test pinning that by_project rows carry
  `worker_tasks_with_usage`, `cognition_cost_usd`, `worker_cost_usd`.

**Tasks:**
- [x] tinyspec written
- [x] CycleDetail: add summary field (Evals.tsx)
- [x] Usage.tsx: per-project expandable rows
- [x] test_usage_endpoint.py: wire-shape regression test
- [x] pytest suite green

**Done-When:** CycleDetail shows the summary text when expanded; each per-project
usage row opens a detail panel with the three hidden fields; regression test pins the
wire shape; `pytest` suite passes.
