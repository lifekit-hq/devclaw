# Contract: the BLOCKED hand-back with options (P1)

Stated in TWO places that change together: `devclaw/prompts/session.md` (the host's prompt) and
`runner/runner.py::_RETURN_CONTRACT` (the runner's own copy of the lines). The parser is ONE:
`runner/runner.py::_parse_block_line` (next to `_classify_block`).

## The line

```
BLOCKED: <the one question the owner must answer> — options: <a> | <b> [| <c> [| <d>]] — default: <a | one option's text>
BLOCKED: <the one question the owner must answer> — default: <what you would do>        (still valid)
BLOCKED: env — <the tool, service, credential or access your ENVIRONMENT lacks>          (unchanged)
```

Prompt guidance (one line, replacing today's BLOCKED line in `session.md`): *"Give two to four
options the owner can pick, and say which you would take."*

## Parsing rules (fail-closed)

1. `_BLOCKED_LINE_RE` finds the last `BLOCKED:` line (unchanged); `_classify_block` decides `env`
   first (unchanged) — an `env` block never carries options.
2. Split the remainder on ` — default: ` (last occurrence; `-`/`–` tolerated, case-insensitive) →
   `default`; then on ` — options: ` → `question`, options text.
3. Options = the options text split on `|`, trimmed, leading `(a)`/`a)`/`a.` labels stripped; kept
   only when 2 ≤ n ≤ 4 and none empty; otherwise `options=[]` and `question` = the full remainder.
4. `recommended` = index of the option `default` names by letter, exact text, or case-insensitive
   prefix; else `-1`.
5. Nothing raises. A remainder with no separators ⇒ `question` = remainder, `options=[]`,
   `default=""`, `recommended=-1` — exactly today's behaviour.

## Payload

```json
{"status": "blocked", "reason": "<raw remainder>", "block_kind": "contract", "block_item": "",
 "question": "…", "options": ["…", "…"], "default": "…", "recommended": 0, "agent_output": "…"}
```

## Host side

- `settle`: unchanged (`mark_done(task_id, json.dumps(result), exit=BLOCKED, exit_detail=reason)`).
- `tick._block`: reads `question`/`options`/`default` from the blocked task's `result_json`;
  `donegate.render_block` renders options as a list under the question and the default line.
- `donegate.block_default` is deleted.
- A decision posted from an option carries the option's full text (never a letter).
