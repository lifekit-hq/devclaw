# Tiny spec — the console can drive the problems window it is already being served

## North-star case

- **Failure moved**: ran but needed the owner. The problems catalog read is
  windowed server-side (14 days, `problems-catalog-recency`), but the console
  could neither say so nor change it — the owner sees a filtered list presented
  as the whole catalog, and the only way to widen or narrow it is an MCP call
  through an agent. On 2026-09-09 the owner's reaction to the windowing landing
  was "it's not obvious to me because we don't have any filtering in the UI".
- **Number that shows it**: owner questions answered by reading the console
  instead of asking an agent to re-query. Before: the window is invisible and
  unchangeable in the UI, and every "what about older ones / show me just the
  env blocks" needs a `list_problems` call. After: both are one click.
- **Cut when**: the catalog stops being windowed, or the console stops being
  the surface the owner reads problems on.

## What

The `/problems.json` route already accepts `category` and `since_ms`. Three
gaps close here:

1. It accepts `since_days` too — a duration the UI can label, rather than an
   epoch the UI would have to compute and then reverse-engineer for display.
2. It returns `windowDays` (null = all-time) and the echoed `category`, so the
   console STATES the filter it is showing instead of implying the catalog is
   that small.
3. The Problems page gains a window picker (7d / 14d / 30d / All time) and a
   category picker, both server-side, alongside the existing client-side
   lifecycle-stage filter.

## Context

`problems-catalog-recency` made every read surface windowed and gave the
default one home. That was right for the MCP tool, whose caller is an agent
that reads the docstring. It was wrong for the console, whose caller is a human
who sees a list with no indication it is filtered — a silently-truncated list
is worse than an honest long one, because the owner cannot tell a quiet week
from a hidden row.

The window default is deliberately NOT restated in the UI: the page sends no
window on first load, and renders whichever `windowDays` the server applied.
One home for the number stays one home.

**Rejected: filtering the fetched rows client-side.** The window changes which
rows the SQL returns (`ORDER BY count DESC` runs after the `last_seen_ms`
filter), so a client-side window over an already-windowed, already-`LIMIT`ed
page would silently show a different set than the same window server-side. The
lifecycle-stage filter stays client-side because it is derived from fields
already on the row and does not change the query.

## Requirements

- **R1** `/problems.json` accepts `since_days` (0 = all-time); it wins over
  `since_ms` when both are present. A non-integer is a 400, as `since_ms` is.
- **R2** The response carries `windowDays` (null = all-time) and `category`.
- **R3** The Problems page renders a window picker and a category picker; both
  refetch. The active window is read from the RESPONSE, never from a constant
  duplicated in the UI.
- **R4** The page states the window in prose, says hidden rows are not deleted,
  and says `×N` is a lifetime count (the reason a wide window looks alarming).
- **R5** The empty state names the window when one is active.

## Plan

`server/routes/observability.py`: parse `since_days`, derive one `window_days`,
echo it. `console/src/api.ts`: `fetchProblems(opts)`, `PROBLEM_CATEGORIES`,
`windowDays` on the response type. `console/src/pages/Problems.tsx`: `FilterBar`
+ two state hooks in the fetch dependency list.

## Tasks

- [ ] T1 `since_days` + `windowDays`/`category` on the route (R1/R2)
- [ ] T2 `fetchProblems(opts)` + types (R3)
- [ ] T3 `FilterBar` + refetch wiring (R3)
- [ ] T4 honest window prose + empty state (R4/R5)

## Done-When

- `/problems.json?since_days=0` returns the whole catalog with
  `windowDays: null`; `?since_days=7` returns a 7-day window with
  `windowDays: 7`; no param returns the shared default.
- `?category=block` returns only block rows and echoes `"category": "block"`.
- The Problems page shows which window it is displaying and switches on click.
- `npm --prefix console run build` (which is `tsc --noEmit && vite build`, and
  runs inside `deploy/Dockerfile`) passes.

## Tests

None. Per `.claude/rules/testing.md` the suite is a tripwire net: this touches
no tripwire class (no zero-token guard, fail-closed gate, CAS, OAuth/sandbox
fence, pause brake, materialize span, doctor seeded-fault or structural guard).
The console build typechecks the UI half; the route half is exercised live.
