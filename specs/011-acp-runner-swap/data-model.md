# Data Model: ACP-Direct Runner (011)

No persistent storage — every shape below is in-memory for one task run or
rides the stdout wire contract.

## AcpAgentProcess (acp_client.py)

The spawned agent subprocess + its stdio channel.

| Field | Type | Notes |
|---|---|---|
| `argv` | `list[str]` | from the command seam (payload → env → `["claude-agent-acp"]`) |
| `env` | `dict[str,str]` | explicit allowlist: `CLAUDE_CODE_EXECUTABLE`, `CLAUDE_CONFIG_DIR`, `PATH`, `HOME`, optional `ANTHROPIC_MODEL` (D5, D9) |
| `proc` | `subprocess.Popen` | stdin/stdout pipes = JSON-RPC channel; stderr → bounded ring buffer |
| `idle_timeout_s` | `int` | `DEVCLAW_ACP_IDLE_TIMEOUT_S`, default 1800 (D7) |

Lifecycle: spawn → `initialize` → `session/new` → one `session/prompt` turn →
teardown (graceful `session/cancel` + terminate/kill escalation). Any
protocol-level failure raises `AcpError` (message text preserved verbatim for
host-side classification).

## AcpSession / turn state

| Field | Type | Notes |
|---|---|---|
| `session_id` | `str` | from `session/new` response |
| `next_request_id` | `int` | JSON-RPC id counter (client→agent) |
| `last_agent_message` | `str` | accumulated `agent_message_chunk` text of the LAST message; feeds `agent_output`, `_parse_blocked_reason`, `_parse_repo_notes` |
| `usage` | `dict \| None` | best-effort extractor over updates (D6); `None` = omitted from result |

## PromptOutcome (returned to runner.py)

| Field | Type | Notes |
|---|---|---|
| `stop_reason` | `str` | `end_turn` \| `max_tokens` \| `max_turn_requests` \| `refusal` \| `cancelled` |
| `last_agent_message` | `str` | see above |
| `usage` | `dict \| None` | `{input_tokens, output_tokens, cache_read_tokens, cost_usd}` when present; all-zero → `None` |
| `stderr_tail` | `str` | bounded agent-stderr, fallback material for `_agent_last_words` |

`refusal` maps to the runner's failure path (reason = last message);
`cancelled` propagates; everything else proceeds to blocked-check → verify.

## Event mapping (in-memory → wire)

See research.md D4 — ACP `session/update` kinds map onto the host's existing
event vocabulary (`MessageEvent`, `ACPToolCallEvent`, `PlanEvent`,
`ACPUpdateEvent`); emitted via the untouched `_emit_event` envelope
(`id`/`type`/`source`/`ts`/`payload`).

## Result payload (wire — FROZEN, see contracts/runner-host-wire.md)

Unchanged from today: `status` ∈ `ok`/`blocked`/`error`/`rate_limited` with
`workspace_dir`, `agent_output`, optional `usage`, `repo_notes`,
`hook_warnings`, `verify{...}`, `reason`, `retry_after_s`, `trace`.
