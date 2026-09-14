# Tasks: the read side of v2

**Input**: Design documents from `/specs/047-console-read-side/` (plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md)

**Tests**: only the two tripwire classes in research R10 (fail-closed block-line parsing; the money-domain metric). Pages, projections and aggregates ship no tests (`.claude/rules/testing.md`).

**Organization**: one PR per user story, branch `feat/047-<story>`, each squash-merged with its own CI green before the next opens. Every story is a complete increment on its own.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: US1 needs-you · US2 drill-down · US3 usage · US4 verdicts

## Path Conventions

Host: `devclaw/…`, sandbox harness: `runner/runner.py`, console: `console/src/…`, tests: `tests/…` (repository root). Run the suite with `TMPDIR=$(mktemp -d) python -m pytest -q`; gate with `ruff check . && mypy && lint-imports`; build the console with `cd console && npm ci && npm run build`.

---

## Phase 1: Setup

**Purpose**: nothing to initialize — the project, routes, store and console exist. One shared acceptance helper.

- [X] T001 Write `specs/047-console-read-side/seed.py`: against `DEVCLAW_DB_PATH`, insert one project (via `ProjectRegistry`), one goal (`StateStore.create_goal`), and three tasks through the store: a BLOCKED session whose `result_json` carries `question`/`options`(3)/`default`/`recommended`/`usage`, a REVIEW session whose `agent_output` ends with a three-clause verdict JSON (one clause unsatisfied) and a `pre_run_sha`, and a DELIVERED session with no usage block; print the ids. Referenced by quickstart.md; never imported by the package.

---

## Phase 2: Foundational

**Purpose**: no blocking prerequisite is shared by all four stories; each story carries its own store read and API types. (Recorded so the phase is not mistaken for an omission.)

**Checkpoint**: user stories can start; US1 first.

---

## Phase 3: User Story 1 — Needs you: unblock with one click (Priority: P1) 🎯 MVP

**Goal**: a blocked goal shows its session's question, options and recommended default as buttons; one click posts `decide`; an answered goal shows as "answered, waiting for the tick". Host-authored stops show their fact with free text only.

**Independent Test**: quickstart §P1 — `goals.json[].attention` carries the seeded options with `recommended ≥ 0`; a POST of the recommended option's text to `/goals/<id>/decide` flips `attention.answered` with a `waitingOn` word; the console page renders buttons, recommended first, and moves the goal to the answered section after the click.

### Tests (tripwire: fail-closed protocol parsing)

- [X] T002 [US1] Extend `tests/test_runner_blocked.py` with parametrized cases for `runner._parse_block_line`: options parsed (2, 3, 4); default resolved by letter `(a)`, by exact text, by case-insensitive prefix, unresolved ⇒ `-1`; no `options:` ⇒ `options == []` and today's question/default split; one option or five ⇒ `options == []` and `question` = the raw remainder; `env —` form never carries options; garbage after `options:` ⇒ raw line as question. Name the test after the invariant (a malformed block still blocks, never fabricates an option).

### Implementation

- [X] T003 [US1] In `runner/runner.py` add `_parse_block_line(reason: str) -> tuple[str, list[str], str, int]` per `contracts/handback.md` (split on ` — default: ` last, then ` — options: `; `|`-separated options with `(a)`/`a)`/`a.` labels stripped; keep 2..4 non-empty; recommended by letter/text/prefix; never raises); call it after `_classify_block` at the blocked-payload site and add `question`, `options`, `default`, `recommended` to `blocked_payload` (`env` blocks: `options=[]`, `recommended=-1`).
- [X] T004 [P] [US1] Update the BLOCKED line in `runner/runner.py::_RETURN_CONTRACT` and in `devclaw/prompts/session.md` to the `— options: a | b — default: a` form with the one-line guidance "give two to four options the owner can pick, and say which you would take"; keep the `env —` line; both files in the same commit (cognition-prompts rule).
- [X] T005 [US1] In `devclaw/goal/donegate.py` delete `block_default` (and its `__all__` entry); extend `render_block(..., options: list[str] = (), recommended: int = -1)` to render options as a list under the question, the recommended one marked, before the default line.
- [X] T006 [US1] In `devclaw/goal/tick.py::tick_goal` (the `last.exit == EXIT_BLOCKED` branch) read `question`, `options`, `default`, `recommended` from `json.loads(last.result_json)` (fallback: `question = last.exit_detail`, no options) and pass them to `_block` → `render_block`; keep the `text or "(no question stated)"` guard.
- [X] T007 [US1] Create `devclaw/server/routes/_attention.py` with the pure function `attention(goal: dict, last: Task | None, decisions: list[Decision], last_seen: dict | None, control: dict) -> dict | None` implementing data-model §2 and research R4/R5: kinds `session` / `env` / `done-gate refused` (re-parse the REVIEW row with `devclaw.goal.donegate.parse_verdict`) / `red CI` (last_seen `pr == "open"` and `ci == "red"`) / `delivery refused` / `DONE without a PR`; `answered` when the newest decision is newer than `since`, with `waitingOn` from `control` (`hold` / `window` / `pause` / `lane busy` / `tick`); `None` for closed goals and goals needing nothing.
- [X] T008 [US1] In `devclaw/server/routes/goals.py` attach `attention` to every row of `/goals.json` and to `/goals/{id}.json` (build `control` once per request from `store.operator_hold()`, `store.get_run_schedule()`, `store.global_pause()`, `store.count_running()` via `dispatch_gate.operator_block`); in `devclaw/server/routes/tasks.py` add `block: {question, options, default, recommended, kind, item}` to `/tasks/{id}.json` for a blocked row.
- [X] T009 [P] [US1] In `console/src/api.ts` add `Attention` and `Answered` types (data-model §2) and `attention: Attention | null` on `GoalRow`; add `block` to the task detail type.
- [X] T010 [US1] Create `console/src/pages/NeedsYou.tsx`: fetch `goals.json`, filter `attention != null`; section "Needs you" (oldest `since` first) with the question, option buttons (recommended first, marked "recommended"), a free-text field + Decide, and Cancel goal (confirm); a click posts `decideGoal(id, option)` (full text) and refetches; section "Answered, waiting for the tick" for `attention.answered != null` showing the text and `waitingOn`; host-authored kinds show the fact and the link with no buttons; empty state "Nothing needs you."; time-to-decide / open age shown per row (SC-003).
- [X] T011 [US1] Wire the page: `console/src/main.tsx` route `needs-you` and make it the index redirect; `console/src/components/AppShell.tsx` NAV entry "Needs you" first with a count badge from `goals.json` (reuse the 15 s poll), and `crumb()` label; `console/src/pages/GoalDetail.tsx` shows `attention` (buttons or fact) above the Decide box, reusing the same component.
- [X] T012 [US1] Run quickstart §P1 against the stub instance (seed with T001); run the suite, `ruff`, `mypy`, `lint-imports`, and `npm run build`; open PR `feat/047-needs-you` with the North-star case from the spec; update the spec `Status` to `PARTIAL — owner: Denys, date: <+14d>` naming US1 built.

**Checkpoint**: a session-authored block is one click; the thread record lists the options.

---

## Phase 4: User Story 2 — Drill down: project → goal → session (Priority: P2)

**Goal**: project, goal and session pages, each reachable by URL, every recorded part shown or stated "not recorded".

**Independent Test**: quickstart §P2 — three clicks from Projects to a session's events; the session page shows the same task and events as `/tasks/{id}.json` and `/tasks/{id}/events.json`; an unknown id says so.

### Implementation

- [X] T013 [P] [US2] In `console/src/api.ts` add `TaskDetail` (`task`, `verify`, `delivery`, `change`, `agentOutput`, `block`, `usage`) and `TaskEvent` types plus `fetchTask(id)` and `fetchTaskEvents(id)`; add `fetchProject(id)` returning `ProjectRow`.
- [X] T014 [US2] Create `console/src/pages/ProjectDetail.tsx`: name, status, repo link, workspace, then the goals list as links (`/goals/:id`) with state word and last exit; unknown id ⇒ "No such project."
- [X] T015 [US2] Create `console/src/pages/SessionDetail.tsx`: header (goal link, kind, exit + exitDetail, created/started/completed, PR link), cards for verify / delivery / change span / block (US1 fields) / agent output, each rendering "not recorded" when absent; events list in order (type, source, time, payload summary) with a "load more" on `nextCursor`; unknown id ⇒ "No such session."
- [X] T016 [US2] Wire routes `projects/:id` and `sessions/:id` in `console/src/main.tsx`; make project names links in `console/src/pages/Projects.tsx`; make session rows links in `console/src/pages/GoalDetail.tsx` and in the Goals list's "Last session" column (`console/src/pages/Goals.tsx`); extend `crumb()` in `AppShell.tsx` for `projects/<id>` and `sessions/<id>`.
- [X] T017 [US2] Run quickstart §P2; suite + gates + `npm run build`; PR `feat/047-drill-down`; spec `Status` names US1+US2 built.

**Checkpoint**: any recorded session is three clicks from Projects.

---

## Phase 5: User Story 3 — Usage inline + the `/metrics` counter (Priority: P3)

**Goal**: tokens per session; totals per goal and project with the unreported count; the instance total as a counter on `/metrics`. No page, no stored aggregate.

**Independent Test**: quickstart §P3 — `goal.json.usage` sums equal the seeded blocks with `sessions_reported 2 / sessions_total 3`; the session without a block reads `usage: null`; `/metrics` carries `devclaw_tokens_total{kind=…}` without a token.

### Tests (tripwire: the money-domain metric)

- [X] T018 [US3] Extend `tests/test_deadman_metrics.py`: `render_metrics(..., tokens={...}, sessions_total=n, sessions_reported=m)` renders the four `devclaw_tokens_total{kind}` samples and the two session gauges; all-zero input renders `0` samples (never absent) with `devclaw_sessions_reported_usage 0`.

### Implementation

- [X] T019 [US3] In `devclaw/state_store/core.py` add `usage_totals(self, *, parent_goal_id: str | None = None, project_id: str | None = None) -> dict` (data-model §3) as ONE query using `json_extract(result_json, '$.usage.<kind>')` sums plus `COUNT(*)` and a count of rows whose `$.usage` is not null; filters by `parent_goal_id` or `project_id` when given.
- [X] T020 [US3] Attach usage: `devclaw/goal/service.py::_task_view` gains `usage` (from `result_json.usage` or `null`); `devclaw/server/routes/goals.py` rows and detail gain `usage = store.usage_totals(parent_goal_id=…)`; `devclaw/server/routes/projects.py::project_rollup` callers gain `usage = store.usage_totals(project_id=…)`; `devclaw/server/routes/tasks.py` adds `usage` to `/tasks/{id}.json`. Keep the goal layer under its ceiling (`_task_view` is a 2-line change).
- [X] T021 [US3] In `devclaw/server/routes/metrics.py` extend `render_metrics` and `_collect` per `contracts/metrics.md` (`devclaw_tokens_total{kind}` counter ×4, `devclaw_sessions_total`, `devclaw_sessions_reported_usage`) from `store.usage_totals()`.
- [X] T022 [P] [US3] In `console/src/api.ts` add `Usage` (block) and `UsageTotals` types on `SessionRow`, `GoalRow`, `GoalDetail`, `ProjectRow`, `TaskDetail`.
- [X] T023 [US3] Render inline: a `UsageChip` in `console/src/ui.tsx` (input/output/cache tokens, "not reported" when null, "n of m reported" for totals); use it on `SessionDetail.tsx`, `GoalDetail.tsx` (header + per session row), `Goals.tsx` (column), `Projects.tsx` and `ProjectDetail.tsx` (total).
- [X] T024 [US3] Run quickstart §P3; suite + gates + `npm run build`; PR `feat/047-usage-inline`; spec `Status` names US1–US3 built. Note in the PR body the lifekit-stack follow-up: a Grafana panel `increase(devclaw_tokens_total[1d]) by (kind)`.

**Checkpoint**: every page that names a session, goal or project shows what it cost, or that it was not reported.

---

## Phase 6: User Story 4 — Verdicts (Priority: P4)

**Goal**: every done-gate review across goals and per goal, clause by clause with evidence; unreadable verdicts listed as such.

**Independent Test**: quickstart §P4 — `/verdicts.json` lists the seeded review with `achieved false`, `satisfied 2 / total 3`, `head` = its `pre_run_sha`; the console page lists it and expands to three clauses with evidence.

### Implementation

- [X] T025 [US4] In `devclaw/state_store/core.py::list_tasks` add an optional `exit: str | None` filter (WHERE `exit = ?`), keeping the existing signature otherwise.
- [X] T026 [US4] Create `devclaw/server/routes/verdicts.py` with `GET /verdicts.json` (`json_limit`): `store.list_tasks(exit=EXIT_REVIEW, limit=…)` newest first → rows per data-model §4 via `devclaw.goal.donegate.parse_verdict(result.agent_output)`, `head = pre_run_sha`, `prUrl` from the goal's newest session with a PR url; a review row with `status != "done"` ⇒ `unreadable` with `rawError = error`. Register it in `devclaw/server/http.py` BEFORE `console` (route-shadowing test).
- [X] T027 [US4] In `devclaw/server/routes/goals.py` add `verdicts[]` (same row builder, filtered by `parent_goal_id`) to `/goals/{id}.json`; move the row builder to `verdicts.py` and import it.
- [X] T028 [P] [US4] In `console/src/api.ts` add `VerdictRow` and `Clause` types, `fetchVerdicts(limit?)`, and `verdicts: VerdictRow[]` on `GoalDetail`.
- [X] T029 [US4] Create `console/src/pages/Verdicts.tsx`: list newest first (goal link, head short sha, achieved / not achieved / unreadable, `satisfied/total`, time), expandable to clauses (text, ✓/✗, evidence verbatim), structural health, concerns, the reviewer's question, PR link; "truncated" note when the feed says so; empty state "No reviews yet."; and a `VerdictList` component reused on `GoalDetail.tsx` as a "Verdicts" section.
- [X] T030 [US4] Wire route `verdicts` in `console/src/main.tsx`, NAV entry in `AppShell.tsx`, `crumb()` label.
- [X] T031 [US4] Run quickstart §P4; suite + gates + `npm run build`; PR `feat/047-verdicts`; spec `Status` → `SHIPPED` naming the remaining live task (SC-002/SC-003 read after 14 nights) with an issue.

**Checkpoint**: the "ran and produced garbage" axis is readable across goals.

---

## Phase 7: Polish & cross-cutting

- [X] T032 Docs honesty in the SAME PR as each story: `docs/INDEX.md` currency tags for `docs/reference/env-vars.md` (unchanged) and any doc naming the console pages or `/metrics` gauges (`docs/runbooks/devclaw-self-deploy.md` §67-70 lists the gauges); `README.md` console section names the four pages.
- [X] T033 After US4 merges: record in `~/memory/projects/devclaw/log.md` one dated line (shipped; SC-002/SC-003 regrade date) and file the lifekit-stack Grafana panel as an issue there.

---

## Dependencies & execution order

- **US1 → US2 → US3 → US4** as PRs (each merged before the next opens, CI green). Code-wise: US2, US3 and US4 do not depend on US1; US3's `SessionDetail` usage chip depends on US2's page (T023 after T015); US4's goal-page section depends on nothing in US2/US3.
- Within US1: T003 → T006 (payload before the tick reads it); T005 with T006; T007 → T008 → T010 → T011; T004 and T009 parallel with the rest.
- Within US3: T019 → T020 → T021; T022 parallel; T023 after T020 and T015.
- Within US4: T025 → T026 → T027; T028 parallel; T029 after T028; T030 after T029.

## Parallel examples

- US1: `T004` (prompt lines) and `T009` (TS types) alongside `T003` (runner parser).
- US3: `T022` alongside `T019`.
- US4: `T028` alongside `T025`/`T026`.

## Implementation strategy

MVP = US1 alone: it is the only story that moves the north-star number, and it is complete
without the others (the goal page already exists for the decision to land on). US2 makes the
decision readable, US3 makes the night's cost visible, US4 makes the gate's refusals readable
across goals. Stop and re-judge if US1's live data after 14 nights shows fewer than half of
session-authored blocks carrying options (the spec's cut condition).
