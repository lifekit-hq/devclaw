# Contract: MCP tools and HTTP routes

One service entry, three clients (MCP tool, HTTP route, the waiter's chat
translation). The MCP tools live in `devclaw/server/tools/goals.py` beside
`steer_goal`; routes in `devclaw/server/routes/goals.py` beside `/steer`.

## New MCP tools

### `correct_implementation(goal_id, problem_id, correction) -> json`

The requirement was right; the work was wrong. Records a Decision
(`verb=correct_implementation`, `provenance=owner`, `text=correction`) against
the Problem's clause, closes the Problem (`resolved`), UNBLOCKs the goal with
the full budget restored, pokes the tick. The next brief carries the
correction as settled fact (see `brief-section.md`).

Errors (`ToolError`): unknown goal; `problem_id` is not the goal's current
open Problem (stale id → the response names the current one); empty
correction.

Response:
```json
{"goal_id": "...", "resolved": true, "decision_id": "dec_...", "verb": "correct_implementation", "clause": "..."}
```

### `decide(goal_id, problem_id, option=None, text=None) -> json`

The owner takes an action. Exactly one of `option` (an option key from the
Problem) or `text` (a free-form decision). Records a Decision
(`verb=decide`, `provenance=owner`), closes the Problem, UNBLOCKs with the
full budget, pokes. A Decision on a clause that already has one supersedes it
(history kept). If the chosen option has `closes_goal=true`, the goal still
returns to idle — the done-gate's next round closes it.

Errors: as above; `option` not in the Problem's options; both or neither of
`option`/`text`.

Response: as above with `"verb": "decide"` and `"option": "<key>"` or
`"text": "..."`.

## Changed MCP tools

### `steer_goal(goal_id, message)` — refusal (FR-006, Q1 → A)

If the goal has an open Problem, the tool raises `ToolError` whose message is
the Problem rendered for a human (what · clause · options with the default
marked · timebox) followed by:
`Resolve it with correct_implementation or decide; steering resumes once no
Problem is open.` Nothing is written. Behaviour without an open Problem is
unchanged.

### `get_goal` / `list_goals` — read shape

Each goal object gains:
```json
"problem": {
  "id": "prb_...", "kind": "needs_answer", "raised_by": "done_gate",
  "what": "...", "clause": "...", "why": "...",
  "options": [{"key": "c1", "label": "...", "consequence": "...", "closes_goal": false}, ...],
  "default": "c1", "timebox_at": 1788400000000, "raised_at": 1788356800000,
  "status": "open"
} | null,
"decisions": [{"id": "dec_...", "clause": "...", "verb": "decide", "option": "c1", "text": null,
               "provenance": "owner", "made_by": "denys", "made_at": 1788360000000}]
```
`blocked_on` keeps a one-line summary (`"<kind>: <what[:160]> — see problem"`)
so readers that predate this spec still get a sentence.

### `create_goal` — admission refusal (FR-008/009, Q3 → A)

For a class-(a) clause the tool raises `ToolError`:
`done_when clause "<clause>" requires <capability> which the sandbox cannot
provide; rewrite it as observable repository behaviour and resubmit.` Nothing
is persisted. A class-(b) rewrite is applied, recorded as an admission
Decision, and reported in the success response under `"admission": {"rewrites": [...]}`.
A class-(c) clause admits the goal blocked with an `admission` Problem
(raised_by `admission_lint`), reported under `"admission": {"problem": {...}}`.

## New HTTP route

### `POST /goals/{goal_id}/resolve`

Body: `{"verb": "correct_implementation", "problem_id": "...", "correction": "..."}`
or `{"verb": "decide", "problem_id": "...", "option": "..."}` /
`{"verb": "decide", "problem_id": "...", "text": "..."}`.

200 → the tool response above. 400 `{"error": "invalid_verb" | "stale_problem" | "bad_option" | "missing_field", "problem": {...current...}}`. 404 unknown goal. Console-facing; same `GoalService.resolve_problem` entry as the tools.

### `POST /goals/{goal_id}/steer` — refusal

409 `{"error": "problem_open", "problem": {...}, "resolve_with": ["correct_implementation", "decide"]}` when a Problem is open; unchanged otherwise.

### `GET /goals/{goal_id}.json`

Gains `problem` and `decisions` exactly as the MCP read shape.

## Notifier (owner ping) wording

```
🟡 [<goal_id>] problem — <clause or "contract">
<what>
Options: (1) <label>  (2) <label> ← default  (3) <label>
Default applies <timebox, e.g. "in 12h">. Resolve with correct_implementation or decide.
```
Never mentions `steer_goal`. A defaulted Decision pings once:
`ℹ️ [<goal_id>] defaulted — <option label> on "<clause>"; override with decide.`
