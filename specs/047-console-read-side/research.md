# Research: the read side of v2

## R1 — The block line grammar

**Decision**: one trailing line, extended:

```
BLOCKED: <question> — options: <a> | <b> [| <c> [| <d>]] — default: <a | the text of one option>
```

`— options:` is optional (today's `BLOCKED: <q> — default: <x>` stays valid); `— default:` stays
mandatory in the prompt (the session's recommendation). Separators: ` — ` (em dash, as the prompt
already uses) with `-`/`–` tolerated; options split on ` | `. The default resolves to an option by
letter (`a`/`(a)`/`A`), by exact text, or by case-insensitive prefix; otherwise it stands alone as a
"take the default" button and the options are unranked.

**Rationale**: `_BLOCKED_LINE_RE` already captures the whole remainder of the last `BLOCKED:` line;
the protocol stays "exactly one line" (runner `_RETURN_CONTRACT` and `session.md` both say so);
a session that ignores the new fields still blocks correctly. A fenced JSON block would be more
expressive and less reliable: the model already fails to emit valid JSON for the done-gate often
enough that `parse_verdict` has an `unreadable` path.

**Alternatives**: a second-line `OPTIONS:` list (breaks "exactly one line"; two regexes); a JSON
fence (see above); numbered options `1)`/`2)` (letters read better in a sentence and never collide
with quantities in the question text).

## R2 — Where the parser lives, and how many there are

**Decision**: `runner/runner.py` gains `_parse_block_line(reason) -> (question, options, default,
recommended)` next to `_classify_block`; the blocked payload carries `question`, `options`,
`default`, `recommended`. `devclaw/goal/donegate.block_default` (the host's regex over
`exit_detail`) is **deleted**; `tick._block` reads the fields from the blocked task's
`result_json`. `exit_detail` keeps the raw reason line for the row.

**Rationale**: the north-star class test counted two parsers of the same line (runner
`_classify_block`, host `block_default`). Two or more at a boundary means replace with one, never
add a third. The runner is the model-agnostic parser of the agent's own hand-back and already owns
the `env` sub-form.

**Alternatives**: parse in settle (a third reader); keep `block_default` as a fallback for pre-047
rows (the v2 DB is one day old with one goal — the fallback would outlive its only case).

## R3 — Where the needs-you derivation lives

**Decision**: `devclaw/server/routes/_attention.py`, a pure function
`attention(goal_row, last_task, decisions, last_seen, control) -> dict | None` applied per goal by
`goals.json`/`goal.json`. No new route: the needs-you page filters `goals.json` on `attention`.

**Rationale**: `devclaw/goal/` + prompts is 1,372 of 1,500 lines; the tick keeps that headroom.
The derivation is determined (no judgment), reads only served rows, and the server layer already
derives words (`project_rollup` health, `_vitals`). Import direction server → goal is allowed
(`parse_verdict` is reused from `goal.donegate`).

**Alternatives**: `GoalService.needs_you()` in the goal layer (eats the ceiling); a `/needs-you.json`
route (a second list of the same goals; the page can filter).

## R4 — Host-authored blocks without copying GitHub

**Decision**: derive each host-authored stop from what the host already holds:

| Stop | Local evidence | Needs-you row |
|---|---|---|
| done-gate refused | last task `exit == REVIEW`, `parse_verdict(result.agent_output).achieved == False` | kind `done-gate refused`, the verdict summary + unsatisfied clauses, link to PR |
| red CI on delivered head | `goal.last_seen.pr == "open"` and `last_seen.ci == "red"` | kind `red CI`, head sha, link to PR |
| env gap | last task `exit == BLOCKED`, `result.block_kind == "env"` | kind `waiting for <item>`, no buttons |
| delivery refused (twice) | last task `exit == REFUSED` | kind `delivery refused`, `exit_detail` |
| DONE without a PR | last task `exit == DONE`, `last_seen.pr == "none"` | kind `DONE without a PR` |

All show free text + cancel, never options (ruled 2026-09-14). The thread record's text (CI
logs) is not copied: the row links to the thread/PR.

**Rationale**: constitution IV — GitHub carries the record; the fingerprint already holds `pr`,
`head`, `ci`; the verdict is re-parsed from the review row. Nothing new is stored.

**Alternatives**: store the posted block text locally (a copy that drifts); read GitHub on page
load (a token-free but rate-limited read on every console poll — no).

## R5 — "Answered, waiting for the tick"

**Decision**: a goal is `answered` when `max(decision.made_at) > last_task.completed_at` (or
`> last_task.created_at` for a REVIEW row). The reason it has not run comes from `control.json`
facts already served: operator hold, run window closed, usage pause, or `running > 0` on the same
project lane ("lane busy"). Rendered as a second section on the needs-you page, no buttons.

**Alternatives**: hide on click (the owner cannot see a hold sitting on the answer); keep buttons
(a second decision on the same question).

## R6 — Usage aggregation without a table

**Decision**: `StateStore.usage_totals(*, parent_goal_id=None, project_id=None) -> dict` runs one
`SELECT SUM(json_extract(result_json,'$.usage.input_tokens')) …, COUNT(*) FILTER (WHERE
json_extract(result_json,'$.usage') IS NOT NULL), COUNT(*)` over `tasks`. Goal rows, goal detail,
project rollups and the metrics route call it. Per-session usage is read straight from the row's
`result_json.usage` in `task.json`.

**Rationale**: SQLite json1 is built into Python's sqlite3 on every target; one pass, no Python
loop, no cache. Tasks are never pruned (only events are), so the instance sum is monotonic.

**Alternatives**: a Python loop over `list_tasks(limit=…)` (a cap turns the total into a lie);
a `usage` column filled at settle (a stored aggregate — constitution IV).

## R7 — The `/metrics` counter

**Decision**: `devclaw_tokens_total{kind="input"|"output"|"cache_read"|"cache_creation"}` as TYPE
`counter`, computed on scrape from `usage_totals()`; plus `devclaw_sessions_reported_usage` /
`devclaw_sessions_total` gauges so a Grafana panel can show the unreported share honestly.

**Rationale**: the north-star verdict moved the per-day history to the standard stack; a counter
computed from an append-only table is a legal counter (`increase()`/`rate()` work; a DB restore
reads as a reset).

**Alternatives**: a gauge (loses `increase()` semantics on dashboards); per-project labels (a
label per project id is unbounded cardinality for a series nobody asked for — N=2 trigger).

## R8 — Verdicts across goals

**Decision**: `GET /verdicts.json` (module `routes/verdicts.py`, imported before `console`) lists
`list_tasks(exit="REVIEW")` newest first, each parsed with `parse_verdict` from the row's
`agent_output`; `head` is the review task's `pre_run_sha` (the checkout the review ran on = the
delivered head). `goal.json` adds the same rows filtered by goal (`verdicts`).

**Rationale**: the gate's own parser guarantees the page shows what the gate decided, unreadable
included. `pre_run_sha` avoids storing the head twice.

**Alternatives**: read verdict records from GitHub (rate-limited, and the host would parse its own
comment); store the parsed verdict at settle (a derived copy).

## R9 — Acceptance surface

**Decision**: HTTP against a stub-engine instance (`DEVCLAW_ENGINE=stub`) with seeded rows, per
`quickstart.md`; the console is proven by `tsc --noEmit && vite build` in CI plus the same JSON.
The repo has no browser-e2e harness and this spec does not add one (a second consumer would).

## R10 — Tripwire tests (testing rule)

Only two classes touched: **fail-closed protocol parsing** (extend `tests/test_runner_blocked.py`
with: options parsed; default resolved by letter / text / prefix; missing `options:` ⇒ empty list;
garbage after `options:` ⇒ raw line as question, no options) and **observability of the money
domain** (extend `tests/test_deadman_metrics.py`: counter renders per kind; zero reported sessions
⇒ `0` with `devclaw_sessions_reported_usage 0`). Pages, projections and aggregates ship no tests.
