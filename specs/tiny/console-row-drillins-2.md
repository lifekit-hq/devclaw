# Console universal row drill-ins — increment 2: Problems + PR rollup

**What:** Add click-to-expand detail panels to every Problem catalog row and every PR rollup row in the console, exposing every field the backing store row / delivery trace holds. This is the remaining half of issue #682's audit pass — increment 1 (eval-outcomes + cycle-reports) already shipped in PR #696.

**Context:** The steering confirms transcripts index and usage-by-project are clean (no hidden fields). The two tables with gaps are:
- Problems catalog (Problems.tsx): `sample_message`, `first_seen_ms`, `last_goal_id`, `last_task_id`, `issue_state` are returned by `/problems.json` but not shown.
- PR rollup (PRList.tsx): `ts` (delivery trace timestamp), `gatePassed`, `mergeStateStatus`, `mergedAt`, `error` are returned by `/goals/{id}/prs.json` but not shown.

No new backend endpoints needed — both list responses already return all fields. The data simply is not wired into the UI.

**Requirements:**
- R1: Clicking a Problem card expands an inline detail panel showing `fingerprint`, `sample_message`, `first_seen_ms`, `last_goal_id` (linked to `/goals/:id`), `last_task_id` (linked to `/tasks/:id`), `issue_state`. Click again collapses.
- R2: Clicking a PR row expands an inline detail panel showing `ts`, `gatePassed`, `mergeStateStatus`, `mergedAt`, `error`. Click again collapses. The Merge button must not trigger the expansion.
- R3: The `_collect_goal_pr_rows` helper's `gatePassed` and `ts` fields are pinned by regression tests in `tests/test_console_prs_endpoint.py`.

**Plan:**
- `console/src/pages/Problems.tsx`: add `expanded` state (keyed by fingerprint); make each card clickable; add `ProblemDetail` sub-component; stop propagation on fix-goal button + issue link.
- `console/src/components/PRList.tsx`: add `expanded` state (keyed by prUrl); restructure card to header+detail; add `PRDetail` sub-component; stop propagation on Merge button.
- `tests/test_console_prs_endpoint.py`: add test that `_collect_goal_pr_rows` includes `gatePassed` and `ts` in returned rows.

**Tasks:**
- [x] tinyspec written
- [x] Problems.tsx drill-in
- [x] PRList.tsx drill-in
- [x] wire-shape regression test (gatePassed + ts)
- [x] pytest suite green

**Done-When:** Every Problem catalog row and every PR rollup row expands to show all hidden fields; last_goal_id/last_task_id link to existing drill-ins; Merge button works independently of row expansion; `pytest` suite passes.
