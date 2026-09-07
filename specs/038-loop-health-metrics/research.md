# Research: Loop Health Metrics (Phase 0)

All findings verified 2026-09-07 against instance `cdc1b88` (read-only query of
`/var/lib/devclaw/devclaw.db` inside `devclaw-devclaw-mcp-1`) and the tree at
the same SHA. No NEEDS CLARIFICATION remains.

## Live findings

### L1 — The worker reports NO usage in production (the spec's stated risk, now a fact)

- Of the last 60 settled tasks with a `result_json`, **0** carry a `usage`
  block. The payload keys are `{agent_output, change, chunk, context, delivery,
  diff_stats, hook_warnings, message, repo_notes, status, verify,
  workspace_dir}` — no `usage` key at all.
- `runner/acp_client.py` has a tolerant extractor (`accumulate_usage` over
  `session/update` params and `_meta.usage`); its own comment says ACP 0.x
  standardizes no token-usage report. `claude-agent-acp` emits a
  `usage_update` notification carrying `{used, size}` (context fill — the spec
  021 tripwire reads it) but never per-turn token counts. So `sum_task_usage`
  and the scorecard's `worker_*` figures have read zero since spec 011.
- Host-side cognition usage IS real: `llm_call.parse_cli_envelope` records the
  `claude --print --output-format=json` envelope's `usage` (tokens in/out,
  cache read/creation, `total_cost_usd`) into `traces` (kind=`cognition`,
  payload `tokens_in`/`tokens_out`/`cost_usd`/`role`). 14-day window: 28.7k
  in / 585k out. But `traces` is pruned at `TRACE_RETENTION_DAYS_DEFAULT=30`
  — the same amnesia the spec names for `tasks.result_json`.

### L2 — Where Claude Code writes what it spent

- The host container's `CLAUDE_CONFIG_DIR` (`/home/node/.claude`) holds
  `projects/<cwd-slug>/<session>.jsonl` transcripts for EVERY session,
  including `claude --print` cognition calls (`projects/-app/*.jsonl`, `cwd:
  /app`). Each `type: assistant` line carries `message.usage`:
  `{input_tokens, output_tokens, cache_read_input_tokens,
  cache_creation_input_tokens, ...}` plus top-level `requestId`, `sessionId`,
  `cwd`, `timestamp`, `isSidechain`. This is the artifact `ccusage` reads —
  the standard, not a devclaw invention.
- In the sandbox (`engine/sandcastle.py`) the curated config dir
  `/home/agent/.claude` is RO binds + two writable tmpfs subpaths
  (`session-env/`, `shell-snapshots/`). `projects/` is NOT writable, so the
  agent's transcript write hits EROFS and is dropped — there is nothing for
  the runner to read today. One more tmpfs subpath (`projects/`) makes the
  agent's own record exist for the life of the container; it dies with it.

### L3 — The live `blocked_kind` vocabulary is wider than FR-002's list

Grep of `devclaw/goal/*.py`: `mechanical:ci`, `mechanical:corrupt_doc`,
`mechanical:dispatch_cap`, `mechanical:env`, `mechanical:env_cap`,
`mechanical:lost_ref`, `mechanical:merge_failed`, `mechanical:prep`,
`mechanical:slice_hold`, plus `needs_answer`, `bug`, `dispatch_cap`,
`lost_ref`, `donegate_churn`. `cycle_report.py` already treats any
`mechanical:*` as a wedge and any unrecognized block kind as human-gated.

### L4 — The durable rows this feature extends (row counts live)

`goal_convergence` 33 (one row per terminal goal: `goal_id, outcome, rounds,
workspace_dir, closed_at`), `cycle_reports` 48 (`clean`, `idle`),
`pr_ledger` 47 (ground-truth PR state, refreshed once per cycle), `problems`
253 (`recovered_count`, `terminal_count`, `last_seen_ms`), `goal_interventions`,
`goal_steering` (human rows have `source NOT LIKE 'auto-%'`),
`goal_issue_identity`, `goal_settlements`. There is no `limit_events` table.
The scorecard (`telemetry.compute_scorecard`) is a window-bounded projection
(max 720h) over these; `/metrics` (#849) is the dead-man endpoint; the console
is a Vite/React SPA under `console/` built into `devclaw/server/console_dist`
by `deploy/Dockerfile`.

### L5 — The grader's assessment is thrown away

`intake_readiness.SizingAssessment.assessed` is computed on the readiness
call, compared to the filer's claim in `intake.sizing_outcome`, rendered into
the mirror comment (`_sizing_paragraph`) and returned in the tool result —
and persisted nowhere. The filer's claim lives in the issue body
(`parse_expected_increments`). A goal links to its issues via
`Goal.issue_refs` (ints) + `Goal.repo_url`; `goal_issue_identity` keys
`(project_id, issue_key)`.

### L6 — Retries and the settle single writer

`queue/settle.py` runs the runner per attempt inside `_run_and_settle`
(`self._runner(request)` at two sites: the retry loop and the validation
path); only the final attempt's payload reaches `mark_done`/`mark_failed`.
Intermediate attempts' usage is lost unless recorded per attempt.

### L7 — Spec 037 deleted four cognition roles

Trend detector, summarizer, self-triage, `evaluate_goal` are gone (#844).
Nothing in this feature assumes them.

## Decisions

### D1 — Worker usage source: the agent's own transcript, read by the runner (IN scope)

**Decision**: Ship the source inside spec 038 (PR-B). Two bounded changes:
1. `engine/sandcastle.py` adds `--tmpfs /home/agent/.claude/projects:rw,exec`
   — the third scratch subpath, same posture as `session-env`.
2. `runner/runner.py`: when the ACP outcome reported no usage AND the ACP
   command is the claude adapter (`_is_claude_adapter(argv)` — basename
   starts with `claude`), read `$CLAUDE_CONFIG_DIR/projects/*/*.jsonl` files
   modified since the run started, sum `message.usage` over `assistant`
   lines whose `cwd` is the workspace, deduplicated on `requestId` (one API
   response can span several JSONL lines), and emit it as the result's
   `usage` block with `"source": "transcript"`. Best-effort, never raises,
   absent ⇒ no `usage` key (never zeros).

**Rationale**: constitution IX — supply a missing FACT through the standard
artifact before any brake; without it US4 and SC-004 are structurally
unbuildable (L1). The transcript is Claude Code's own record (L2), the same
one `ccusage` reads. The reader sits in the claude-specific branch of the
ACP seam (constitution II) so a swapped agent is untouched.

**Alternatives considered**: (a) patch `claude-agent-acp` to forward usage —
a vendor fork devclaw would carry forever; (b) wrap the agent's API calls —
impossible, the agent owns them; (c) name it a gap and ship US3 cognition-only
— honest but leaves the spec's stated purpose ("what does a merged increment
cost on a constrained quota") unmeasured with no owner. Rejected.

**Denys ratifies**: the tmpfs subpath (a transcript on scratch, dies with the
container) and that ACP-reported usage wins when both exist.

### D2 — Idle attribution storage: run-length spans, one write point, explicit gaps

**Decision**: `loop_spans(cause, start_ms, end_ms, ticks, detail)`. On every
completed `tick_all` pass: read the last span; if the interval since its
`end_ms` exceeds `3 × tick_seconds` (default 45 min), insert an `unobserved`
span covering the gap; then extend the last span when its cause matches,
else insert a new span `[prev_end, now]`. First ever tick seeds a zero-length
span (there is no interval to attribute). Bucket is derived from the cause at
read time (FR-005a), never stored.

**Rationale**: the spec's key entity ("a period with a start, an end, and
exactly one cause"); coalescing keeps SC-006 trivially true; a heartbeat gap
(process down, redeploy) attributed to whatever the restart sees would be a
lie — `unobserved` is reported separately and excluded from the not-stuck
denominator (FR-005b's "unknown rather than 100%" applied to the gap).

**Alternatives**: one row per tick (simpler, unbounded growth on poke-driven
ticks); transition-time writes across layers (FR-004a forbids).

### D3 — Cause derivation (deterministic precedence), all zero-token

`devclaw/loop_health.derive_loop_cause(...)` is pure. Inputs: the sweep's
`outcomes`, the live goal statuses, pause state, operator hold, global run
window, per-goal windows, whether any graded-ready issue lacks a goal.
Precedence:

1. any outcome in `{dispatched, verifying, in_flight, done, conflict}` → `working`
2. account pause active → `paused`
3. operator hold → `operator_hold` (**plan addition to FR-003**, bucket: owner's turn — a deliberate stop is the owner's, never devclaw's failure and never "no work")
4. global run window closed → `window_closed`
5. no goal rows at all → `empty_backlog`
6. no live (non-terminal) goal → `no_goal_armed` when the instance knows a graded-ready issue without a goal (PR-C, via `intake_grades` ∖ `goal_issue_identity`), else `all_planned_done`
7. any live goal blocked → its `blocked_kind` verbatim; among several, devclaw-caused outranks owner's turn (a wedge is the fact that matters), ties by goal id; an empty kind on a blocked row (pre-column DB) reads `needs_answer` (cycle_report's human-gated default)
8. any outcome `error` → `bug` (the existing kind for devclaw's own failure)
9. every live goal outside its per-goal window → `window_closed`
10. otherwise (live goals idle on cadence / queued) → `all_planned_done`

Bucket rule (`bucket_for`): `working` → working; `mechanical:*`, `bug`,
`lost_ref`, `dispatch_cap`, `donegate_churn` → devclaw; `needs_answer`,
`no_goal_armed`, `operator_hold` → owner; `empty_backlog`,
`all_planned_done`, `window_closed`, `paused` → no_work; `unobserved` →
unobserved (excluded); any other string → devclaw (an unknown block kind is
devclaw's until named — loud, not lenient).

### D4 — Self-heal rate reads the counters as they are, and says so

`rate = Σrecovered / (Σrecovered + Σterminal)` over problems rows with
`last_seen_ms` in the window; `null` when the denominator is 0. The counters
are lifetime per fingerprint (the 2026-07-23 "read recency" lesson), so the
surface carries `basis: "lifetime counters of problems last seen in window"`
and both raw sums. A per-occurrence ledger was rejected: the spec says derive
from the existing counters; a second store would be a second writer.

### D5 — `usage_ledger`: one table, two sources, written at the two existing entry points

- `source='worker'`: `record_task_usage(task_id, attempt, usage|None)` called
  right after each `self._runner(request)` returns (both sites in
  `queue/settle.py`); goal/workspace/kind copied from the task row;
  `reported=0` with NULL tokens when the runner sent none.
- `source='cognition'`: inside `ObservabilityMixin.append_trace_event` when
  `kind == 'cognition'` (same commit as the trace row): `reported` iff the
  payload carries real `tokens_in`/`tokens_out` (never the `len/4` estimate).
- Backfill (FR-010a): `maybe_backfill_usage_ledger()` — one-shot, meta
  watermark, on the heartbeat's cheap slot next to the prunes: every surviving
  cognition trace → a row; every task with a non-NULL `result_json` → an
  `attempt=0` row (reported iff it carries usage). Tasks already compacted
  get no row — they read as `tasks_without_record` on the surface, never zero.
- `UNIQUE(source, ref_id, attempt)` makes every writer idempotent.

Retention (FR-010) is untouched: `maybe_compact_task_results` and the trace
prune keep their schedules; the ledger is the permanent projection.

### D6 — Cost per outcome: segment by increments behind the merged PR

- Every task belongs to a goal since spec 022 (dispatch_task is one_shot-goal
  sugar), so "delivery shape" is derived, not declared: a merged PR (ground
  truth from `pr_ledger`) produced by exactly one worker dispatch is a
  **standalone PR**; one produced by several is a **goal-cumulative PR**.
- Cost of a goal = Σ reported ledger rows (worker + cognition) carrying its
  `goal_id`; goal→PR via the goal's tasks' `pr_url`; PR state from the
  ledger's platform refresh. Achieved goal + merged PR → merged bucket
  (segmented); abandoned/cancelled goals and failed tasks with no PR →
  shipped-nothing; PR `open`/`unknown`/never refreshed → unknown bucket,
  excluded from both rates (FR-015).
- The scorecard's `usage.tokens_per_merged_pr` and `cost_per_merged_pr_usd`
  are REMOVED and `usage.cost_per_outcome` (the same block the HTTP surface
  serves) takes their place; `format_scorecard` follows (FR-015a).

### D7 — Calibration: extend `goal_convergence` in place, read below a stated floor as "not yet"

- `intake_grades(repo, issue_number)` PK — written by `grade_and_label` via an
  optional `record` callback the MCP layer binds to
  `store.record_intake_grade` (the intake orchestrator holds no store; the
  callback keeps layer 3 free of state writes per the cognition-callers rule).
- `record_convergence` gains `claimed_units`, `assessed_units`,
  `prediction_issues` (JSON: the issue numbers summed + which lacked a grade),
  `dispatches` (worker tasks of the goal, `kind != review_repository`),
  `steered` (human `goal_steering` rows exist), `cost_tokens` (Σ reported
  ledger rows; NULL when none). Predictions for a multi-issue goal are the
  SUM over its issues and NULL when any issue lacks a grade (the record says
  which).
- `compute_calibration`: over achieved, unsteered, predicted goals: sample
  `n`, per predictor mean absolute error, exact-match and within-one rates;
  `determinable=false` with `needed = MIN − n` below `CALIBRATION_MIN_SAMPLE =
  10` (a constant, not a knob — there is nothing to tune yet).

### D8 — Surfaces

- `GET /loop-health.json?window_hours=` and MCP `get_loop_health` (the
  scorecard has an MCP twin for the same reason: the owner reads over
  Telegram); `GET /calibration.json`; `/usage.json` gains `history` (monthly
  totals from the ledger, each month with `records`/`reported`).
- Console: an Overview "Loop health" strip (not-stuck rate + three buckets,
  self-heal, clean-cycle, first-pass, cost per merged goal / standalone PR);
  Usage page renders `history`. `null` renders as `—` with a "no data"
  caption, never `0`.
- `/metrics` untouched (the dead-man signal stays seven gauges).

### D9 — Tests that ship (tripwire classes only)

1. `tests/test_loop_health_absent_is_never_zero.py` (FR-020): parametrized
   over the runner transcript reader, the ledger rollups, the loop-health
   read, the self-heal read, cost per outcome, calibration below the floor.
2. `tests/test_goal_tick.py`: `tick_all` on an idle store records ONE loop
   sample through the engine seam with `FakeClaude.calls == 0`.
3. `tests/test_doctor.py`: seeded fault — a DB missing the new tables FAILs
   with the restart remedy.
4. Existing fake-agent tests keep proving `"usage" not in result` for a
   non-claude agent (constitution II).
No behaviour tests for the console, the routes, or the projections.
