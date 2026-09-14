# Implementation Plan: the read side of v2

**Branch**: `047-console-read-side` (docs on `docs/spec-047-console-read-side`; one `feat/047-*` branch per story) | **Date**: 2026-09-14 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/047-console-read-side/spec.md`

## Summary

Four independently shippable increments over state the host already holds. **P1** extends
the session's `BLOCKED:` hand-back with options and a recommended one, parsed by the ONE
parser of that line (`runner/runner.py`), carried in the blocked payload, rendered on the
thread record and as one-click `decide` buttons on a needs-you page; the page's rows are a
pure projection over goal, last session, decisions and last-seen world — nothing stored.
**P2** restores project and session pages over the JSON routes that exist. **P3** surfaces
the runner's usage block inline (session, goal, project) via one SQLite `json_extract`
read, and exposes the instance total as a `/metrics` counter. **P4** lists done-gate
verdicts across goals and per goal by re-parsing review sessions' output with the gate's
own `parse_verdict`.

## Technical Context

**Language/Version**: Python 3.12 (host, `devclaw/`), Python in the sandbox (`runner/runner.py`), TypeScript + React 18 + react-router 6 + Vite (`console/`)

**Primary Dependencies**: FastMCP/Starlette custom routes (host), SQLite via `state_store` (json1 `json_extract` for usage sums), no new packages on either side

**Storage**: existing `tasks.result_json` (runner payloads: `usage`, `question`/`options`/`default` for blocks, `agent_output` for verdicts), `decisions`, `goals.last_seen_json`. **No new tables or columns** (FR-009, constitution IV)

**Testing**: pytest tripwires only (`tests/test_runner_blocked.py` extended for the block grammar's fail-closed cases; `tests/test_deadman_metrics.py` extended for the token counter); console `tsc --noEmit && vite build` in CI; acceptance = HTTP against the stub-engine instance (quickstart)

**Target Platform**: the VPS compose (`devclaw-mcp` container), console served from `/console`

**Project Type**: web service + bundled SPA

**Performance Goals**: every new read is a single SQLite pass over ≤ a few thousand task rows; verdict parsing is per review row on request (tens of rows); no polling added beyond the shell's existing 15 s control poll

**Constraints**: goal layer + prompts ≤ 1,500 lines (`tests/test_goal_layer_stays_readable.py`; today 1,372) — the needs-you projection therefore lives in the server layer; the hand-back stays ONE trailing line; console routes must register before `console.py` (`tests/test_route_shadowing.py`)

**Scale/Scope**: 4 new pages (needs-you, project, session, verdicts), 1 new JSON route (`/verdicts.json`), fields added to 4 existing feeds, 1 runner parser extension, 1 prompt line, 1 metrics counter

## Constitution Check

- [x] **I. OAuth only** — no spawn-site change; the runner's env handling is untouched.
- [x] **II. Model-agnostic worker layer** — the block grammar is plain text in the hand-back, parsed from the agent's own final message; no vendor tool-wiring. `_RETURN_CONTRACT` (runner) and `session.md` (host) state the same line and change together.
- [x] **III. Zero-token idle** — every new read is a projection; no tick-path cognition, no spawn from any page. A click posts `decide` (existing) which pokes the tick as today.
- [x] **IV. Single writer** — no new writer: the runner's payload is recorded by settle exactly as before (`json.dumps(result)`); decisions via `GoalService.decide`; no view is read back. `goals` table unchanged (`test_the_goals_table_holds_no_derived_state`).
- [x] **V. Fail-closed / done is a proposal** — an unparsable block line still blocks with the raw line as its question and no buttons; an unreadable verdict is listed as unreadable; the done-gate and close path are untouched.
- [x] **VI. Loud failure** — "not reported" for absent usage, "unreadable" for verdicts, "not recorded" for absent session parts; never a zero or a blank that looks like a value.
- [x] **VII. Fix the class** — the class is "the agent's structured hand-back reaches the human as choices"; the plan REPLACES the host's second parser of the block line (`donegate.block_default`) with the runner payload, leaving one parser at the seam.
- [x] **VIII. Cognitive guardrail?** — none added; none kept. The recommendation is the session's, rendered verbatim.
- [x] **IX. Instruct thin, verify thick** — new Python by domain: runner grammar + payload = **protocol**; usage read + `/metrics` counter = **money** (the fact the quota brake lacks); needs-you projection, verdicts read = **protocol** (the owner's one verb and the verdict of record, made readable). No project-code knowledge enters devclaw. Standard practice: Prometheus holds the series, not a console page.

## Project Structure

### Documentation (this feature)

```text
specs/047-console-read-side/
├── plan.md              # this file
├── research.md          # decisions with alternatives
├── data-model.md        # the projections and payload fields
├── quickstart.md        # HTTP acceptance against the stub engine
├── contracts/
│   ├── handback.md      # the BLOCKED line grammar + blocked payload
│   ├── json-routes.md   # feeds: new fields + /verdicts.json
│   └── metrics.md       # the token counter
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
runner/runner.py                       # P1: _parse_block_line → question/options/default in blocked_payload; _RETURN_CONTRACT line
devclaw/prompts/session.md             # P1: the BLOCKED line with options (moves with the parser)
devclaw/goal/donegate.py               # P1: render_block renders options; block_default deleted (one parser)
devclaw/goal/tick.py                   # P1: _block reads question/options/default from the blocked task's result
devclaw/server/routes/_attention.py    # P1: attention(goal, last, decisions, control, last_seen) → needs-you row (pure)
devclaw/server/routes/goals.py         # P1/P3/P4: goals.json rows gain attention + usage; goal.json gains verdicts; P4: /verdicts.json
devclaw/server/routes/projects.py      # P3: rollup gains usage
devclaw/server/routes/tasks.py         # P2/P3: task.json gains usage + block fields
devclaw/server/routes/metrics.py       # P3: devclaw_tokens_total{kind}
devclaw/state_store/core.py            # P3: usage_totals(...) via json_extract; P4: list_tasks(exit=...)
console/src/api.ts                     # types + fetchers for the new fields/route
console/src/pages/NeedsYou.tsx         # P1
console/src/pages/ProjectDetail.tsx    # P2
console/src/pages/SessionDetail.tsx    # P2 (+ usage, P3)
console/src/pages/Verdicts.tsx         # P4
console/src/pages/GoalDetail.tsx       # session links, usage total, verdicts section
console/src/pages/Projects.tsx         # project links, usage column
console/src/components/AppShell.tsx    # nav: Needs you (badge), Verdicts
console/src/main.tsx                   # routes
tests/test_runner_blocked.py           # extend: grammar fail-closed cases
tests/test_deadman_metrics.py          # extend: token counter renders, absent ⇒ 0 with reported count
```

**Structure Decision**: host routes stay split by resource under `devclaw/server/routes/`;
the needs-you derivation is a pure function in its own module there (not in `devclaw/goal/`,
which has 128 lines of ceiling left and must keep them for the tick). Console pages stay
one file per page under `console/src/pages/`.

## Complexity Tracking

No constitution violations to justify.
