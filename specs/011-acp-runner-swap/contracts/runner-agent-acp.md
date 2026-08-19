# Contract: runner ⇄ agent (the ACP subset — FR-011)

What `acp_client.py` speaks to the agent subprocess selected by the command
seam (payload `acp_command` → `DEVCLAW_ACP_COMMAND` → `claude-agent-acp`).
JSON-RPC 2.0, newline-delimited, over the subprocess's stdin/stdout.

## Client → agent

| Method | When | Key params |
|---|---|---|
| `initialize` | once, after spawn | protocol version; client capabilities: `fs` absent, `terminal` absent |
| `session/new` | once | `cwd` = workspace dir, `mcpServers: []` |
| `session/prompt` | once per task | `sessionId`, one text content block = the wrapped goal |
| `session/cancel` | teardown/timeout | notification, best-effort before kill |

## Agent → client

| Message | Handling |
|---|---|
| `session/update` notification | mapped to wire events per research D4; `agent_message_chunk` text accumulated as the last agent message |
| `session/request_permission` request | **auto-grant, never blocks** (clarified 2026-08-19): choose an allow-flavoured option (`allow_always` preferred, else `allow_once`, else first option); reply `cancelled` only when the turn is being cancelled |
| any other request (`fs/*`, `terminal/*`, …) | JSON-RPC error `-32601` method not found — the capability was not advertised; loud, not silent |
| unknown notification | tolerated (logged as `ACPUpdateEvent`), never fatal |

## Turn completion

The `session/prompt` response's `stopReason`:
`end_turn` / `max_tokens` / `max_turn_requests` → completed (blocked-check +
verify gate judge the work) · `refusal` → failure, last message as reason ·
`cancelled` → cancellation propagates.

## Failure discipline (D7)

- Malformed frame, EOF, process death mid-turn → `AcpError` with verbatim
  text (host/runner classification unchanged; quota text → `rate_limited`).
- Idle timeout (`DEVCLAW_ACP_IDLE_TIMEOUT_S`, default 1800s of protocol
  silence) → cancel, kill, legible failure.
- Teardown escalation: `session/cancel` → SIGTERM → SIGKILL.

## Env at spawn (D9 — allowlist, Principle I)

`CLAUDE_CODE_EXECUTABLE`, `CLAUDE_CONFIG_DIR`, `PATH`, `HOME`, optional
`ANTHROPIC_MODEL` (model tier, D5). Nothing else crosses; API-key vars are
refused before spawn exactly as today.
