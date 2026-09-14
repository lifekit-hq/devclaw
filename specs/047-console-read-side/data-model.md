# Data model: the read side of v2

No new tables or columns. Three payload/projection shapes, all derived on request.

## 1. Blocked payload (runner → settle → `tasks.result_json`)

Existing fields: `status: "blocked"`, `reason` (the raw line remainder), `block_kind`
(`contract` | `env`), `block_item`, `workspace_dir`, `agent_output`, `usage?`, `hook_warnings?`.

Added by P1 (all strings/lists, verbatim from the session, may be empty):

| Field | Type | Meaning |
|---|---|---|
| `question` | string | text before `— options:` / `— default:`; the raw reason when nothing parses |
| `options` | string[] (0 or 2..4) | the session's choices in the order written |
| `default` | string | the session's recommendation text (today's default) |
| `recommended` | int | index into `options` that `default` resolved to, else `-1` |

Validation (runner): a line with `options:` but fewer than two readable options ⇒ `options=[]`,
`recommended=-1`, `question` = the raw remainder. `env` blocks never carry options.

## 2. Attention row (server projection, per goal, in `goals.json` / `goal.json`)

`attention: null` when the goal needs nothing. Otherwise:

| Field | Type | Source |
|---|---|---|
| `kind` | `session` \| `env` \| `done-gate refused` \| `red CI` \| `delivery refused` \| `DONE without a PR` | last task exit + `result_json` + `last_seen` (research R4) |
| `question` | string | the session's question, the verdict summary, the CI head, the credential name |
| `options` | string[] | session blocks only; `[]` otherwise |
| `recommended` | int | as above; `-1` otherwise |
| `default` | string | session blocks only |
| `since` | ms | the blocking task's `completed_at` (or `last_seen_at` for red CI) |
| `link` | string | the PR url or the issue url |
| `answered` | `{ text, madeAt, commentUrl, waitingOn }` \| null | newest decision newer than `since`; `waitingOn` ∈ hold \| window \| pause \| lane busy \| tick |

Time-to-decide (SC-003) = `answered.madeAt - since` when answered; open age otherwise.

## 3. Usage (row read + aggregate)

Per session (`task.json`, `sessions[]` in `goal.json`): `usage: { input_tokens, output_tokens,
cache_read_tokens, cache_creation_tokens, source? } | null` — the runner's block or `null`
("not reported").

Aggregate (`usage_totals`): `{ input_tokens, output_tokens, cache_read_tokens,
cache_creation_tokens, sessions_total, sessions_reported }` — sums over rows with a usage block;
`sessions_total - sessions_reported` is the "not reported" count shown next to every total.
Attached as `usage` on goal rows, goal detail, project rollups; instance-wide on `/metrics`.

## 4. Verdict row (`/verdicts.json`, `goal.json.verdicts[]`)

| Field | Source |
|---|---|
| `taskId`, `goalId`, `createdAt`, `completedAt` | the REVIEW task row |
| `head` | the task's `pre_run_sha` |
| `achieved`, `unreadable`, `rawError`, `question`, `structuralHealth`, `concerns[]`, `summary` | `parse_verdict(result.agent_output)` |
| `clauses[]` | `{ clause, satisfied, evidence }` verbatim |
| `satisfied`, `total` | counts over `clauses` |
| `prUrl` | the goal's last session with a PR url, if any |

## State transitions

None new. A goal's state word is unchanged; `attention` and `answered` are read-side facts that
flip when a newer session or decision row exists.
