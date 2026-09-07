# Contract: the runner result `usage` block (amends spec 021 `contracts/runner-result.md`)

Unchanged rule: `usage` is OPTIONAL and absent means "the agent reported
nothing" — never a block of zeros.

## Shape

```json
"usage": {
  "input_tokens": 1234,
  "output_tokens": 567,
  "cache_read_tokens": 89000,
  "cache_creation_tokens": 21000,
  "cost_usd": 0.0,
  "source": "acp" | "transcript"
}
```

- `source: "acp"` — accumulated from `session/update` usage reports (the
  existing extractor; all four token keys optional, `cost_usd` present).
- `source: "transcript"` — summed by the runner from the agent's own session
  transcript(s) under `$CLAUDE_CONFIG_DIR/projects/*/*.jsonl` after the run,
  ONLY when the ACP path reported nothing AND the ACP command is the claude
  adapter (basename starts with `claude`). `cost_usd` is omitted (an OAuth
  session reports none; the host records NULL, never 0).
- Precedence: ACP-reported usage wins when both exist.

## Transcript read rule (the claude-specific branch of the ACP seam)

- Files: `<config dir>/projects/*/*.jsonl` with mtime ≥ the run's start.
- Lines: `type == "assistant"` whose `cwd` equals the workspace (or is
  absent), carrying `message.usage`.
- Dedup: one row per `requestId` (fallback `message.id`) — a single API
  response is written across several JSONL lines.
- Sidechain (subagent) lines count: they are real spend.
- Sum `input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`. All-zero ⇒ no block.
- Best-effort, never raises; a missing/unreadable dir ⇒ no block.

## Host side

`queue/settle.py` records one `usage_ledger` row per attempt immediately
after the runner returns (`reported=0` when the block is absent); the final
`result_json` still carries the block verbatim for the transcript window.
`telemetry.sum_task_usage` / `_accum_worker` tolerate the two new keys.

## Sandbox

`engine/sandcastle.py` overlays `/home/agent/.claude/projects` with a
writable tmpfs (the same posture as `session-env/` and `shell-snapshots/`).
The transcript exists for the life of the container and dies with it; the
auth files stay read-only binds.
