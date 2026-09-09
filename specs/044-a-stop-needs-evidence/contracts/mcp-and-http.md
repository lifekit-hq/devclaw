# Contract: MCP tools and HTTP routes

Every change here is additive to the spec 031 shapes
(`specs/031-problem-resolution/contracts/mcp-and-http.md`) except the two
admission refusals, which are new `ToolError`s.

## Changed read shape — `get_goal` / `list_goals` / `GET /goals/<id>`

The `problem` object gains one key:

```json
"problem": {
  "...": "spec 031 fields unchanged",
  "evidence": {
    "citation": "path:backend/src/FinanceSentry.Infrastructure/Encryption/CredentialEncryptionService.cs:35",
    "check": "path_in_tree",
    "proven": true,
    "detail": ""
  } | {
    "citation": "",
    "check": "none",
    "proven": false,
    "detail": "the grader cited nothing"
  } | null
}
```

`null` only on rows that predate the migration. `blocked_on`'s one-line
summary is unchanged; `render_for_human` (the ping, the steer refusal, the
console fallback) appends one line: `evidence: <citation> (<check>)` or
`evidence: none — <detail>`, and on an unproven stop the option marked
` ← default` is `drop`.

Each entry in `decisions` gains `"missed_stage": "intake_grade" | "admission"
| "plan" | "none" | ""`.

## Changed verbs — `decide` / `correct_implementation` / `POST /goals/<id>/resolve`

```
decide(goal_id, problem_id, option=None, text=None, missed_stage=None)
correct_implementation(goal_id, problem_id, correction, missed_stage=None)
```

`missed_stage` ∈ {`intake_grade`, `admission`, `plan`, `none`}; optional;
any other value is a `ToolError`. Recorded on the Decision; meaningful on a
design Problem (the tool docstring says so and names the four values). The
response gains `"missed_stage": "<value or empty>"`. The HTTP route takes
the same field in its JSON body.

## Changed read — `list_problems` / `GET /problems.json`

The response object gains one key, computed over the same window:

```json
"design_stops": {
  "intake_grade": [{"goal_id": "...", "problem_id": "prb_...", "decision_id": "dec_...", "what": "...", "made_at": 1789000000000}],
  "admission": [],
  "plan": [],
  "none": [],
  "unrecorded": [{"goal_id": "...", "problem_id": "prb_...", "decision_id": "dec_...", "what": "...", "made_at": 0}]
}
```

A design Problem is one whose `evidence.check == "report_present"` raised
by `worker_block` or `done_gate`. `unrecorded` holds resolved design
Problems whose Decision carries no stage — the gap is counted, never hidden.
`dropped_claim` rows need no new key: they are ordinary catalog rows
(`category="gate"`, `kind="dropped_claim"`), read with
`list_problems(category="gate")`; the seam is the first token of
`sample_message`.

## New refusals — `create_goal` / `set_goal_verify_cmd`

`create_goal(..., verify_cmd=<non-empty>)` on a project whose manifest
declares `verifyCmd` raises `ToolError`:

```
verify_cmd is refused: the project manifest declares its gate —
`<manifest verifyCmd>` — and a goal-level command would narrow the verdict
of record. Omit verify_cmd; the manifest command runs.
```

Nothing is persisted. A bare tool name (`dotnet`, `pytest`, `npm`) raises
on every project:

```
verify_cmd 'dotnet test' is a bare tool name — it is not a gate. Use the
project manifest's verifyCmd (declare one in devclaw.json) or a full
command.
```

`set_goal_verify_cmd(goal_id, verify_cmd)` applies the same two rules to a
non-empty value; an empty value (clear, fall back to the manifest) stays
allowed and its response says which command now runs.

## Changed read — `regrade_intake` / `grade_backlog`

The result object gains `"stale_evidence": "<citation or empty>"` and, when
the model claimed `stale` without a passing citation, `"dropped_claim":
"<what was cited and why it failed>"`. The posted grade comment for a stale
verdict shows the citation (`… already resolved at
`backend/src/…/CredentialEncryptionService.cs:35``); for a dropped stale
claim the readiness comment is the ordinary ready / needs-refinement text
plus one line: `The grader claimed the ask was stale but cited <nothing |
a path not in the repository: …>; the claim was dropped.`

## Unchanged, by requirement (FR-015)

`steer_goal` (still refused while a Problem is open), `resume_goal`,
`cancel_goal`, the two verbs' error cases, the `admission` response block.
