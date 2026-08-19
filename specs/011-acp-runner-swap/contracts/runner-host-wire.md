# Contract: runner ⇄ host wire (FROZEN — FR-002)

The layer-4/5 boundary. The engine (`sandcastle.py` / `host.py` via
`runner_io.consume_runner_output`) streams the runner's stdout line-by-line.
This contract is byte-compatible before and after the swap; conformance is
asserted by diffing new-runner output against the shapes below (source of
truth: runner.py @ 8da9ad5).

## Framing

- Zero or more `event: <json>` lines (telemetry, never load-bearing for the
  verdict), then exactly one terminating `result: <json>` line.
- Lines are written to the process's original stdout (`sys.__stdout__`) and
  flushed per line. Nothing else may appear on stdout.
- Lines can exceed 64 KiB; the host reads with `STREAM_LINE_LIMIT`.

## `event:` envelope

```json
{"id": <str|int|null>, "type": "<EventClassName>", "source": "<agent|devclaw|...>", "ts": <float>, "payload": {...}}
```

Vocabulary the host console classifier renders (preserved by D4 mapping):

- `MessageEvent` / `source="agent"` — payload `{"llm_message": {"content": [{"type": "text", "text": "..."}]}}`
- `ACPToolCallEvent` — payload carries `tool_call_id`, `title`, `content`, `raw_input`, `is_error`
- `VerifyResult` / `source="devclaw"` — payload `{cmd, passed, exit_code, timed_out}` (emitted by the runner itself; untouched)
- Other types fall to the classifier's tolerant fallback — allowed, not load-bearing.

## `result:` payload

`status` is the discriminator; the host regex/classifier reads `error` text.

| status | Required fields | Optional fields |
|---|---|---|
| `ok` | `workspace_dir`, `message`, `agent_output` | `usage`, `repo_notes`, `hook_warnings`, `verify` |
| `blocked` | `reason`, `workspace_dir`, `agent_output` | `usage`, `repo_notes`, `hook_warnings` |
| `error` | `error` (verbatim text) | `trace`, `agent_output`, `hook_warnings`, toolchain fields |
| `rate_limited` | `error` | `retry_after_s`, `agent_output`, `hook_warnings` |

- `agent_output` = the agent's OWN final message (falls back to bounded
  transcript tail) — #570 semantics.
- `verify` = `{cmd, passed, exit_code, timed_out, tail, ...}` (+
  `browser_report` when present). The host decides done-vs-failed from
  `verify.passed`; the runner only reports.
- `usage` = `{input_tokens, output_tokens, cache_read_tokens, cost_usd}` —
  optional; absent means unknown, never zero-cost (D6).
- A clear usage/rate limit is classified runner-side (`_detect_usage_limit`)
  into `rate_limited` + `retry_after_s`; ambiguous errors stay `error` for
  host-side classification.

## Invariants

1. Exactly one `result:` line per run, always — including import failure,
   toolchain failure, agent crash, idle timeout.
2. A blocked self-report short-circuits BEFORE the verify gate and never
   settles `ok`.
3. No OpenHands-specific field is part of this contract; nothing here names
   the agent implementation.
