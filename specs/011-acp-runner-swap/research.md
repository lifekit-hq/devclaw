# Research: ACP-Direct Runner (011)

All decisions grounded in the current code (runner.py @ 8da9ad5,
`devclaw/server/worker_events.py`, `.sandcastle/Dockerfile`,
`runner/requirements.txt`, `tests/test_runner_*.py`).

## D1 — Client basis: hand-rolled minimal JSON-RPC, zero new dependencies

**Decision**: The ACP client is hand-written JSON-RPC 2.0 over the agent
subprocess's stdio — no protocol library.

**Rationale**:
- The host test suite runs with the OpenHands stack *absent* (the import is
  lazy inside `main()`; `import acp` fails in the venv today). A zero-dep
  client is importable everywhere — sandbox, host engine mode, and
  `spec_from_file_location` tests — with no install-matrix work.
- The `agent-client-protocol` PyPI lib already bit us live: 0.11.0 flipped
  `prompt()`'s argument order and every sandbox turn died (the 0.10.1 pin
  comment in requirements.txt). Owning ~300 lines kills that fragility class.
- The needed surface is 3 requests, 1 notification stream, and 1 inbound
  request — a library earns nothing at that size.

**Alternatives considered**:
- *`agent-client-protocol` (official Python lib)* — rejected: asyncio +
  pydantic dependency in a sync runner, must be added to host dev deps for
  unit tests, and the version-flip fragility is documented history.
- *Keep openhands-sdk* — rejected by the spec itself.

## D2 — Concurrency: single-threaded synchronous read loop

**Decision**: One blocking loop. Send a request, then read agent→client
lines: dispatch `session/update` notifications and inbound requests inline,
return when the response for the outstanding request id arrives. Reads use
a poll/deadline so silence is detectable.

**Rationale**: The runner is strictly sequential (one prompt turn per task).
A reader thread or asyncio adds nothing but nondeterminism; a single loop is
trivially unit-testable against scripted line sequences.

**Alternatives**: reader thread + queue — rejected (no concurrent requests
exist to justify it).

## D3 — ACP method surface (what the client implements)

Outbound (client → agent):
- `initialize` — protocol version, client capabilities: **no** `fs`, **no**
  `terminal` (the agent is self-sufficient inside the sandbox; claude-code
  brings its own tools).
- `session/new` — `cwd` = workspace, `mcpServers: []` (MCP config keeps
  riding the existing `/workspace/.mcp.json` drop; unchanged mechanism).
- `session/prompt` — the wrapped goal as a single text content block.
- `session/cancel` (notification) — sent on teardown/timeout.

Inbound (agent → client):
- `session/update` notifications — consumed per D4.
- `session/request_permission` — **auto-grant** (clarified 2026-08-19): pick
  the first allow-flavoured option (`allow_always` preferred over
  `allow_once`), never block. Cancelled outcome honored if the turn is
  already being cancelled.
- Anything else (`fs/read_text_file`, `terminal/*`, …) — JSON-RPC
  `method not found` error. We didn't advertise the capability; a compliant
  agent won't call it; a non-compliant call fails loud (FR-011).

Stop reasons on the `session/prompt` response: `end_turn` → normal
completion; `max_tokens`/`max_turn_requests` → treated as completion (the
agent's last words + verify gate judge the work); `refusal` → failure with
the last message as reason; `cancelled` → propagates the cancellation.

## D4 — Event mapping: keep the host's existing vocabulary

The host (`worker_events._classify`) already renders two families natively:
`MessageEvent` (`llm_message.content[].text` — kept in sync with the
runner's `_agent_message_text`) and `ACPToolCallEvent`
(`tool_call_id`/`title`/`content`/`raw_input`/`is_error`). **Decision**: the
new runner emits exactly that vocabulary:

| ACP `session/update` kind | Emitted event |
|---|---|
| `agent_message_chunk` | accumulated per turn; flushed as `type="MessageEvent"`, `source="agent"`, payload `{"llm_message": {"content": [{"type": "text", "text": ...}]}}` |
| `agent_thought_chunk` | accumulated; flushed as `type="MessageEvent"`, `source="agent"` with `thought: true` marker in payload (renders as message; never parsed for contract lines) |
| `tool_call` / `tool_call_update` | `type="ACPToolCallEvent"`, payload = the ACP update object verbatim (already the host's expected shape) |
| `plan` | `type="PlanEvent"`, payload verbatim (falls to the classifier's tolerant `other` branch) |
| other/unknown kinds | `type="ACPUpdateEvent"`, payload verbatim — tolerated, logged, never fatal |

`_agent_message_text`, `_parse_blocked_reason`, `_parse_repo_notes`, and the
console renderer all keep working unmodified. `VerifyResult` emission is
untouched.

## D5 — Model tiering: agent-env passthrough, live-verified

**Decision**: `req["model"]` / `DEVCLAW_EXEC_MODEL` is exported into the
agent subprocess env as `ANTHROPIC_MODEL` (the claude CLI honors it; ACP has
no standard model field). `None` → unset → the agent's default, exactly
today's semantics.

**Risk + mitigation**: whether `claude-agent-acp` forwards the env to the
CLI it spawns cannot be proven offline — flagged as a live-shakedown check
(L1 with a model override) in quickstart.md. Failure mode is benign
(default model), not silent breakage of the run.

## D6 — Usage stats: declared-absent on the ACP-direct path

ACP 0.x `session/update` carries no standardized token-usage report, and the
numbers previously came from the OpenHands conversation object. Per the
2026-08-19 clarification: the `usage` block is **omitted** unless a
recognizable usage payload appears in updates (a tolerant extractor keeps
the door open). `test_runner_usage.py` is rewritten against the extractor;
all-zero → None semantics preserved ("no report" reads as unknown, not
free). Usage-limit *pause* detection is unaffected — `_detect_usage_limit`
still classifies error text.

## D7 — Failure & hang discipline

- Agent process exit / broken pipe / malformed frame mid-turn → the existing
  `except` path: `_failure_result` with the error text + agent last words;
  `_detect_usage_limit` classifies clear quota errors into
  `status="rate_limited"` + retry-after. Byte-same result contract.
- **Idle timeout (new, closes a latent gap)**: no protocol traffic for
  `DEVCLAW_ACP_IDLE_TIMEOUT_S` (default 1800s) → cancel + kill the agent,
  fail loud with a legible reason. A hung session never wedges the task
  until the host's container kill.
- Context-overflow error text passes through verbatim so the host's #572
  named class engages.
- Agent stderr is drained to a bounded buffer (replaces the OpenHands
  `captured_stdout` transcript in `_agent_last_words`'s fallback role).

## D8 — Sibling-module import strategy

`acp_client.py` sits beside `runner.py`; `runner.py` loads it
file-relative (`importlib` from `os.path.dirname(__file__)`), which works
identically in the sandbox (`/opt/devclaw/`), host engine mode
(`runner/`), and the test suite's `spec_from_file_location`
pattern. No sys.path mutation, no package install.

## D9 — Key-stripping unchanged

`_refuse_api_key()` continues to run before spawn; the agent env is built
explicitly (`CLAUDE_CODE_EXECUTABLE`, `CLAUDE_CONFIG_DIR`, `PATH`, `HOME`,
optional `ANTHROPIC_MODEL`) — allowlist construction, so stray API keys
never cross (Principle I). Same env names as today's `acp_env`.
