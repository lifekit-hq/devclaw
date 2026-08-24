# tinyspec: legacy redirect shadows /goals/{id}.json

## What
Console goal-detail dies with `SyntaxError: Unexpected token '<'` — the
`/goals/{id}.json` fetch gets the SPA's index.html.

## Context
Starlette matches routes in registration order = http.py import order. The
#625 split alphabetized that block, so console.py's legacy redirect
`/goals/{goal_id}` registers before goals.py's `/goals/{goal_id}.json` and
matches it (goal_id="….json" → 302 → SPA HTML). Full static sweep of all 40
routes found exactly this one shadow.

## Requirements
- `/goals/{id}.json` answers JSON again; bare `/goals/{id}` still redirects.
- No earlier route may ever swallow a later, more specific one (the class).

## Plan
Move the console import last in http.py (loud ordering comment) + a static
shadow-matrix regression test over every custom_route in registration order.

## Tasks
- [x] Reorder http.py route imports; document the ordering contract
- [x] tests/test_route_shadowing.py — full pairwise shadow matrix (flip-checked)

## Done-When
Suite green; deployed `/goals/<id>.json` returns 200 JSON, `/goals/<id>` 302s.
