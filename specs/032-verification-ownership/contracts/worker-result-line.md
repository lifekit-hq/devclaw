# Contract — the worker's typed block (layer 5 → layer 4)

The sandbox worker's terminating `result:` line (`runner/runner.py:955 _emit_result`)
carries a `blocked` payload today: `{"status":"blocked","reason":"<text>",…}`. This spec
types it.

## Hand-back forms the agent may write (runner `_RETURN_CONTRACT`)

```
STATUS: DONE
STATUS: BLOCKED: env — <the tool, service, credential or access your environment lacks>
STATUS: BLOCKED: <one-line reason>            (a contract-level block, as today)
```

Parsing (`_parse_blocked_reason` unchanged; new `_classify_block(reason)`):

| reason text | `block_kind` | `block_item` |
|---|---|---|
| `env — dotnet-ef not available` | `env` | `dotnet-ef not available` |
| `environment: postgres service unreachable` | `env` | `postgres service unreachable` |
| `the task needs a credential only the owner has` | `contract` | `""` |
| (empty) | `contract` | `""` (reason becomes the existing "without a stated reason" text) |

Regex: `^(env|environment)\s*[—:–-]\s*(.+)$`, case-insensitive, applied to the parsed reason.

## Payload

```json
{"status": "blocked", "reason": "env — dotnet-ef not available",
 "block_kind": "env", "block_item": "dotnet-ef not available",
 "agent_output": "...", "usage": {...}, "repo_notes": [...], "hook_warnings": [...]}
```

`block_kind` and `block_item` are additive; a host older than the runner ignores them and
sees today's `blocked` shape (fail closed, no retry). The blocked short-circuit still runs
before verify (`runner.py:1703-1710` ordering).

## Host consequences (layer 4, `devclaw/queue/settle.py`)

| `block_kind` | `last_failure` marker | retry | catalog | goal layer |
|---|---|---|---|---|
| `env` | `worker reported environment deficiency: <item>` | none | `block/env_deficiency/<item>`, terminal | `mechanical:env` hold on the project (worker-reported row) |
| `contract` | `worker reported BLOCKED: <reason>` (unchanged) | none | (unchanged) | typed `needs_answer` Problem (unchanged) |

Tests that pin this: `tests/test_runner_blocked.py` (parser class, extended),
`tests/acp_fake_agent.py` + `tests/test_runner_acp.py` (a `script_blocked_env` case on the
fake-agent regression), `tests/test_task_retry.py::test_worker_blocked_status_is_not_retried_and_surfaces_reason`
(parametrized over both kinds), `tests/test_env_cap_admission.py` (the project-wide hold
and its env_ref heal).
