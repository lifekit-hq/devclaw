# Tasks: ACP-Direct Runner (retire the OpenHands SDK)

**Input**: Design documents from `specs/011-acp-runner-swap/`
**Prerequisites**: plan.md, research.md (D1–D9), data-model.md, contracts/, quickstart.md

**Tests**: included — the spec mandates them (FR-004 named regression, FR-008 fake
ACP agent) and repo rules require a named regression test per behavior change.

**Organization**: grouped by user story; US1+US2 ship together as PR 1 (plan
slicing — the swap is only safe with its proof); the directory rename is PR 2
and is NOT in this task list (mechanical follow-up).

## Phase 1: Setup

- [ ] T001 Verify worktree import path (`.venv/bin/python -c "import devclaw; print(devclaw.__file__)"` → worktree path) and record the green baseline: `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` count before any change

## Phase 2: Foundational (blocking — the client and its proof harness)

- [ ] T002 [P] Write `tests/acp_fake_agent.py` — a deterministic scripted ACP agent, importable AND runnable as a program (`python tests/acp_fake_agent.py --script <name>`), speaking newline JSON-RPC per `contracts/runner-agent-acp.md`; scripts: `ok` (updates + message chunks + end_turn), `tools` (tool_call/tool_call_update), `permission` (session/request_permission round-trip), `blocked` (final message starting `BLOCKED:`), `refusal`, `rate_limit` (error text with quota phrasing), `hang` (silence after session/new), `client_call` (calls fs/read_text_file expecting -32601), `malformed` (garbage frame)
- [ ] T003 Write `openhands-runner/acp_client.py` — zero-dep JSON-RPC 2.0 client per contracts + research D1–D3, D7: `AcpAgentProcess` (spawn argv/env-allowlist, stderr ring buffer), initialize → session/new → session/prompt lifecycle, single-threaded read loop with poll deadline (`DEVCLAW_ACP_IDLE_TIMEOUT_S`, default 900), `session/update` → caller callback, `session/request_permission` → auto-grant (allow_always ≻ allow_once ≻ first), other inbound requests → -32601, `PromptOutcome{stop_reason, last_agent_message, usage, stderr_tail}`, tolerant usage extractor (all-zero → None), teardown escalation `session/cancel` → SIGTERM → SIGKILL, `AcpError` with verbatim text
- [ ] T004 Write `tests/test_acp_client.py` — protocol-level unit tests driving `acp_client` against the fake agent subprocess: happy path (outcome fields, update callback order), permission auto-grant reply shape, -32601 for unadvertised client methods, idle-timeout kills + raises legibly, malformed frame → `AcpError`, refusal/cancelled stop reasons, teardown escalation on hang, env allowlist excludes `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` even when set (Principle I)

**Checkpoint**: `pytest tests/test_acp_client.py` green with openhands-sdk absent from the venv.

## Phase 3: User Story 1 — the swap is invisible from the host (P1) 🎯 MVP

**Goal**: runner drives the agent via `acp_client`; the `event:`/`result:` wire
contract is byte-compatible; layers 1–4 untouched.

**Independent test**: quickstart §1–§2 — runner-through-fake-agent emits the
frozen contract shapes.

- [ ] T005 [US1] Swap the drive block in `openhands-runner/runner.py` `main()`: delete the `openhands.sdk` imports + `ACPAgent`/`Conversation` wiring + `redirect_stdout` capture; load `acp_client` file-relative (D8, works under `spec_from_file_location`); spawn env allowlist (`CLAUDE_CODE_EXECUTABLE`, `CLAUDE_CONFIG_DIR`, `PATH`, `HOME`, + `ANTHROPIC_MODEL` from `req["model"]`/`DEVCLAW_EXEC_MODEL` per D5); map updates → events per D4 (`MessageEvent` with `llm_message.content[].text`, `ACPToolCallEvent` verbatim, `PlanEvent`, `ACPUpdateEvent`); keep `last_agent_message` sourcing `_parse_blocked_reason`/`_parse_repo_notes`/`agent_output`; `stderr_tail` replaces captured stdout in `_agent_last_words` fallback; `refusal` → failure path; usage from `PromptOutcome` (D6); blocked short-circuit, post-run hook, verify gate, `VerifyResult` event, result assembly all byte-unchanged
- [ ] T006 [US1] Replace the runner's openhands import-error result with an `acp_client` load-failure result in `openhands-runner/runner.py` (same legible `status:"error"` + exit 2 shape); rewrite `_collect_usage` as the D6 update-payload extractor (or fold into acp_client and delete, keeping a thin alias for tests)
- [ ] T007 [P] [US1] Write `tests/test_runner_acp.py` — runner-process-level tests running `openhands-runner/runner.py` as a subprocess against the fake agent via `DEVCLAW_ACP_COMMAND` (quickstart §2 shape, tmp workspace): `ok` → one `result:` line, `status:"ok"`, `agent_output` = fake's final message, `event:` lines include a `MessageEvent`; `blocked` → `status:"blocked"` with reason, short-circuits before verify; `rate_limit` → `status:"rate_limited"` (+ retry_after when parseable); `malformed`/dead-agent → `status:"error"` with verbatim text; verify gate runs (`verify` block present, `VerifyResult` event) when `verify_cmd` given
- [ ] T008 [US1] Rewrite `tests/test_runner_usage.py` to the D6 extractor semantics (usage from update payloads; unrecognized/absent → result omits `usage`; all-zero → None) and fix any other `tests/test_runner_*.py` referencing the removed conversation-based `_collect_usage`

**Checkpoint**: US1 independently provable — quickstart §1 (suite) + §2 (fake-agent smoke).

## Phase 4: User Story 2 — harness-agnosticism proven, not asserted (P1)

**Goal**: the command seam + vendor-neutral workspace are continuously
verified, named regressions.

**Independent test**: point `DEVCLAW_ACP_COMMAND` at the fake agent; full path
runs with zero runner-code change; workspace stays vendor-clean.

- [ ] T009 [P] [US2] Add named regression `test_runner_writes_no_vendor_harness_config_into_workspace` in `tests/test_runner_acp.py`: after a full fake-agent run, the workspace contains no `.claude/`, `CLAUDE.md`, `settings.json`, or native-skill dirs created by the runner (pre-existing repo files tolerated — assert on a clean fixture workspace; the only runner-written file allowed is the existing `.mcp.json` drop) and the wrapped goal still instructs `ls .agent/skills/` + `cat` (FR-004)
- [ ] T010 [P] [US2] Add named regression `test_agent_command_seam_swaps_executor_without_code_change` in `tests/test_runner_acp.py`: same runner invocation, two different fake-agent argv variants via payload `acp_command` and via `DEVCLAW_ACP_COMMAND` (payload wins), both complete the standard contract; plus spawn-site key-stripping asserted end-to-end (`ANTHROPIC_API_KEY` set in runner env, fake agent script asserts it is absent and fails the run if present) (FR-003, FR-005)

**Checkpoint**: US2 named regressions green — the seam is now test-enforced.

## Phase 5: User Story 3 — the image sheds the dependency (P2)

**Goal**: sandbox image builds without the OpenHands SDK tail.

**Independent test**: quickstart §3 (build + `pip list` grep).

- [ ] T011 [P] [US3] Rewrite `openhands-runner/requirements.txt`: remove `openhands-sdk`, `openhands-tools`, `agent-client-protocol` pins; the runner is stdlib-only — leave the file as a documented empty manifest (comment says why it exists and stays empty)
- [ ] T012 [US3] Update `.sandcastle/Dockerfile`: drop/empty the pip requirements layer (keep the venv only if something else needs it — verify), `COPY openhands-runner/acp_client.py /opt/devclaw/acp_client.py` beside runner.py, refresh the header comments that name openhands as the runtime
- [ ] T013 [US3] Validate quickstart §3: `docker build` the image, `pip list` shows no openhands distribution, record output in the PR description (skip gracefully if docker unavailable in this environment — then it moves to the pre-merge checklist)

**Checkpoint**: image slim; US3 done.

## Phase 6: Polish & cross-cutting

- [ ] T014 [P] Amend `.specify/memory/constitution.md` Principle II: name the swap seam abstractly (runner's agent-drive seam — the payload/env-selectable agent command), drop the `ACPAgent` symbol, bump version + amendment note (FR-010)
- [ ] T015 [P] Amend `CLAUDE.md`: the model-agnostic-worker invariant sentence ("only the `ACPAgent` call changes" → "only the runner's agent-drive seam — the ACP client spawn — changes") and the layer-5 row/repo-map lines that describe the runner as OpenHands-based
- [ ] T016 [P] Docs sweep in the same PR: `docs/architecture.md` + `docs/flows/task-execution.md` (runner internals prose), `docs/reference/env-vars.md` (add `DEVCLAW_ACP_IDLE_TIMEOUT_S`, confirm `DEVCLAW_ACP_COMMAND`/`DEVCLAW_EXEC_MODEL` prose), update touched docs' currency tags in `docs/INDEX.md`
- [ ] T017 Repo hygiene gate: `grep -rn "openhands" openhands-runner/ devclaw/` → remaining hits are prose/history only (no imports); full suite `TMPDIR=$(mktemp -d) .venv/bin/python -m pytest -q` green at ≥ T001 baseline
- [ ] T018 Update `specs/011-acp-runner-swap/spec.md` status line + check off this tasks.md; PR via `/ship` ritual (branch `011-acp-runner-swap`, conventional commit, PR body cites the named regressions)

## Dependencies & execution order

- Phase 2 blocks everything: T003 needs nothing; T002 [P] alongside T003; T004 needs T002+T003.
- US1 (T005–T008) needs Phase 2. T005→T006 same file (sequential); T007 [P] after T005/T006; T008 after T006.
- US2 (T009, T010) needs T005–T007 landed; both [P] (same new test file but independent tests — write sequentially if editing the same module concurrently matters).
- US3 (T011–T013) independent of US2; T012 after T011; T013 after T012.
- Polish: T014–T016 [P] anytime after US1 shape is fixed; T017–T018 last.

## Implementation strategy

MVP = Phase 2 + US1 (the swap, provable via fake agent). US2 rides the same
PR as the proof (plan slicing). US3 + polish complete PR 1. Stop-line if
anything wedges: the branch is revertible as one unit; no host state
migrates (quickstart Rollback).
