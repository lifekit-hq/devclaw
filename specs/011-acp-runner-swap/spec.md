# Feature Specification: ACP-Direct Runner (retire the OpenHands SDK)

**Feature Branch**: `011-acp-runner-swap`

**Created**: 2026-08-19

**Status**: SHIPPED — merged as #575

**Issue**: [#542 — Worker executor swap: replace runner with headless claude in the sandbox](https://github.com/lifekit-hq/devclaw/issues/542)

**Input**: User description: "Worker executor swap (#542): replace the OpenHands SDK runtime inside the sandbox (runner/runner.py) with a direct ACP client driving claude-agent-acp — cutting the OpenHands dependency while keeping the runner⇄host line-delimited JSON stdout contract, the plain-markdown skills + bash hooks + MCP worker discipline, and OAuth-only key-stripping byte-unchanged."

## Context & Motivation *(informative)*

Today the in-sandbox runner does not talk to the model itself — it drives the
OpenHands SDK's `ACPAgent`, which spawns an ACP server subprocess (default
`claude-agent-acp`) that in turn drives `claude`. OpenHands contributes
conversation plumbing and event callbacks; the actual vendor-neutral seam is
the **Agent Client Protocol** underneath it, plus the runner's own discipline
(plain-markdown skills, bash hooks, NDJSON stdout to the host).

Post-008, the middleman earns nothing: there is no devclaw-specific planning
intelligence left in the worker layer — the executor is "any competent coding
agent following plain markdown" (`.specify/` scripts + `tasks.md`). What the
OpenHands SDK still costs:

- **A heavy dependency** — the sandbox image's own Dockerfile calls itself
  "fat" because `openhands-sdk` pulls a long tail of deps; slow rebuilds, a
  large tracking surface.
- **A failure surface of its own** — the ACP path bypasses OpenHands'
  condenser, producing the prompt-too-long wedge class from night-1
  (2026-08-13) while still paying for the machinery.
- **Operator dislike, long-standing** — the worker-harness rework direction
  (2026-07-19) predates every recent arc.

**Chosen direction (ruled 2026-08-19, clarify-confirmed): ACP-direct.** The runner grows
a thin ACP client (JSON-RPC over stdio) and drives the same `claude-agent-acp`
subprocess directly — the OpenHands SDK is deleted, the protocol seam is kept.
Harness-agnosticism *improves*: the swap-point remains an industry protocol
any ACP-speaking agent can plug into, matching the standing ruling of
canonical paradigms over homegrown vocabulary.

**Rejected alternative — headless `claude -p --output-format stream-json`
behind a homegrown adapter interface.** Simpler and first-party, with a good
session-resume story, but it relocates the harness-agnostic seam from an
industry protocol to a per-agent parser zoo: every future executor (aider,
codex-CLI, …) would need its own bespoke stream adapter, and the "swap the
agent by swapping one command" property degrades to "write a new parser per
agent". Confirmed in the 2026-08-19 clarify session (see Clarifications).

**Constitutional note (explicit, per governance):** Principle II's letter says
swapping the agent "must only change the `ACPAgent` call". This spec deletes
that call; the *spirit* (exactly one swap seam in the runner) is preserved and
strengthened. The constitution and the matching CLAUDE.md sentence MUST be
re-worded in this arc to name the seam abstractly (the runner's agent-drive
seam) instead of an OpenHands symbol — same-PR, never silently (FR-010).

## Clarifications

### Session 2026-08-19

- Q: ACP-direct (thin runner-owned JSON-RPC client to `claude-agent-acp`) vs headless `claude -p` behind a homegrown adapter? → A: **ACP-direct.** The swap seam stays an industry protocol; owning a few hundred lines of protocol client is the accepted cost.
- Q: What does the client answer to mid-session ACP permission requests in an autonomous run? → A: **Blanket grant.** The docker sandbox is the security boundary (throwaway container, key-stripped env, delivery gated host-side); auto-approve everything, no deadlock path.
- Q: How hard is per-run token-usage accounting (today sourced from the OpenHands conversation object)? → A: **Best-effort.** Preserved when the ACP session exposes numbers, otherwise declared-absent — never fabricated. Usage-limit pause detection is unaffected (it rides error classification, not these stats).
- Q: Does `runner/` get renamed now that OpenHands is gone from it? → A: **Yes, as a mechanical rename-only follow-up PR in the same arc** (e.g. to `runner/`). The swap PR lands under the old name so its diff stays reviewable; a dir named after a deleted dependency is a stale-doc smell and does not survive the arc.
- Q: ACP client vendored inline in `runner.py` or a separate sibling module? → A: **Separate module** (e.g. `acp_client.py` beside `runner.py`, copied into the image the same way). Directly importable for protocol-level unit tests in the stubbed suite; keeps the client cleanly deletable/swappable.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The swap is invisible from the host (Priority: P1)

A dispatched task executes in the sandbox exactly as before: the engine
`docker run`s the same image entrypoint, the runner emits the same
line-delimited `event:`/`result:` JSON on stdout, and the settle/gate path,
delivery, and done-gate behave identically. The only observable difference is
what is *absent*: the OpenHands SDK is no longer installed or imported.

**Why this priority**: This IS the feature — the executor becomes maximally
replaceable with zero movement at layers 1–4. Everything else rides on it.

**Independent Test**: Run one task end-to-end on the new runner (stub ladder
first, then live L1); diff the emitted result payload schema against the
current runner's — byte-identical field set, statuses, and failure classes.

**Acceptance Scenarios**:

1. **Given** a dispatched task, **When** the sandbox runs it on the new
   runner, **Then** the host receives the same `event:`/`result:` NDJSON
   contract (same fields, same terminal statuses) and no host-side code needs
   to change.
2. **Given** the agent hits a clear usage/rate limit mid-run, **When** the
   runner surfaces it, **Then** the result carries `status="rate_limited"`
   with retry-after exactly as today, and the host pauses-and-resumes.
3. **Given** the agent dies mid-session (crash, non-zero exit, broken pipe),
   **When** the runner settles, **Then** the task fails loud with the agent's
   last words in `agent_output` — never a silent success, never a stdout
   transcript echo (#570 semantics preserved).
4. **Given** the #538 shakedown scenario (speckit-install PR +
   feature-through-speckit + done-gate close), **When** replayed on the new
   runner, **Then** it passes byte-for-byte at the contract level.

---

### User Story 2 - Harness-agnosticism is proven, not asserted (Priority: P1)

The agent the runner drives is selected by one payload/env seam (today
`acp_command` / `DEVCLAW_ACP_COMMAND`); pointing that seam at a *different*
ACP-speaking agent — including a deterministic fake used by the test suite —
drives the full runner path with zero runner code change.

**Why this priority**: The invariant this spec exists to protect. A fake ACP
agent in the test suite makes the seam continuously verified instead of a
doc claim, and gives the runner its first harness-level stubbed tests.

**Independent Test**: Point the command seam at a scripted fake ACP agent;
verify a complete task run (events, final message, result settle) without
touching runner code; verify the workspace received no vendor-specific
harness config.

**Acceptance Scenarios**:

1. **Given** a fake ACP agent set via the command seam, **When** the runner
   executes a task, **Then** the run completes through the identical code
   path and emits the standard result contract.
2. **Given** any task workspace, **When** the runner prepares and runs it,
   **Then** skills are discovered as plain markdown (`ls .agent/skills/` +
   `cat`), hooks run as bash `.sh`, and no vendor-native harness config
   (agent-brand settings, native skill dirs, hook manifests) is written into
   the workspace — enforced by a named regression test.
3. **Given** a stray `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` in the
   environment, **When** the runner spawns the agent, **Then** the key is
   stripped exactly as today (OAuth-only, Principle I).

---

### User Story 3 - The sandbox image sheds the dependency (Priority: P2)

The sandbox image builds without the OpenHands SDK and its dependency tail;
`runner/requirements.txt` shrinks to the runner's real needs;
rebuild time and image size drop measurably.

**Why this priority**: The concrete payoff (smaller tracking surface, faster
rebuilds) — but worthless if US1/US2 don't hold, so it rides behind them.

**Independent Test**: Build the sandbox image from the new
requirements/Dockerfile; verify no `openhands` distribution is installed and
the image still passes the runner's smoke path.

**Acceptance Scenarios**:

1. **Given** the new image, **When** built, **Then** no OpenHands package is
   installed and the build succeeds.
2. **Given** the repo, **When** searched, **Then** no `openhands.sdk` import
   remains in runner code; the `runner/` directory is renamed by a
   mechanical rename-only follow-up PR closing the arc (clarified
   2026-08-19).

---

### Edge Cases

- **Agent process hangs (no events, no exit)**: the runner's existing
  wall-clock discipline must still terminate the run and settle a loud,
  classified failure — a hung ACP session never wedges the task forever.
- **Context overflow (prompt-too-long class)**: with no condenser anywhere in
  the chain (already true via ACP today), the runner must classify the
  overflow error legibly so the host's context-overflow handling (#572 class)
  engages — not a generic `error` with an opaque trace.
- **Permission requests from the agent**: an autonomous sandbox run has no
  human to answer an ACP permission round-trip; the client auto-grants every
  request — the docker sandbox is the security boundary and delivery is gated
  host-side — so no deadlock path exists (clarified 2026-08-19).
- **Usage accounting**: today's per-run usage stats come from the OpenHands
  conversation object. If the protocol session does not expose equivalent
  numbers, the field degrades *declared-absent* (omitted/null and documented),
  never silently wrong (Principle VI).
- **Malformed/unknown protocol messages from the agent**: tolerated and
  logged where safe to ignore, but a session that cannot reach a final
  agent message settles as a loud failure with whatever last words exist.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The runner MUST drive the executor agent over the Agent Client
  Protocol directly, with no OpenHands SDK import anywhere in the worker
  layer.
- **FR-002**: The runner⇄host stdout contract — line-delimited `event:` /
  `result:` JSON, the result field set, and the terminal status vocabulary
  (success / failed / error / rate_limited / blocked semantics) — MUST be
  byte-compatible with the current runner; layers 1–4 MUST require no code
  change.
- **FR-003**: The agent command MUST remain selectable through the single
  existing seam (task payload first, then env, then the `claude-agent-acp`
  default); swapping executors MUST require only changing that command.
- **FR-004**: Worker artifacts MUST stay vendor-neutral: skills as plain
  markdown discovered by `ls`+`cat`, hooks as bash `.sh` run by the runner,
  cross-tool capability via MCP; the runner MUST NOT write vendor-native
  harness configuration into the task workspace. A named regression test
  MUST enforce the workspace claim.
- **FR-005**: OAuth-only key-stripping MUST be preserved at the agent spawn
  site — `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN` never reach the agent.
- **FR-006**: Existing runner behaviors MUST survive unchanged: skill/hook
  loading and goal-wrapping, toolchain provisioning (ADR 0005), the verify
  gate run, usage-limit detection with retry-after, blocked-reason parsing,
  and `agent_output` = the agent's own final message (#570).
- **FR-007**: A clear usage/rate-limit failure MUST still surface as
  `status="rate_limited"` (+ retry-after when parseable); a context-overflow
  failure MUST surface in its named class; ambiguous errors stay `error`
  with the text preserved for host-side classification.
- **FR-008**: The test suite MUST gain a deterministic fake ACP agent
  exercising the runner's full drive path (session lifecycle, streamed
  events, final message, failure modes) with no docker and no claude —
  stubbed like everything else in `tests/`.
- **FR-009**: The sandbox image and `runner/requirements.txt` MUST
  drop the OpenHands SDK and its transitive tail; the image MUST build and
  pass the smoke path without it.
- **FR-010**: The constitution (Principle II) and the CLAUDE.md
  model-agnostic-worker sentence MUST be amended in this arc to name the
  swap seam abstractly (the runner's agent-drive seam) instead of the
  `ACPAgent` symbol — same PR as the wording becomes stale, never silently.
- **FR-011**: The ACP client MUST implement only what the runner needs
  (initialize, session, prompt, streamed updates, permission handling,
  cancellation/teardown) — a minimal client, not a general SDK; anything
  unimplemented fails loud, not silently. Permission requests are
  auto-granted (the sandbox is the security boundary); the grant path MUST
  never block awaiting input.

### Key Entities

- **ACP client**: the runner-owned protocol driver — a separate module
  beside the runner (clarified 2026-08-19), spawns the agent subprocess,
  speaks JSON-RPC over stdio, streams session updates into the runner's
  existing event/result emission. Replaces `ACPAgent`+`Conversation`.
  Directly unit-testable in the stubbed suite.
- **Agent command seam**: the one configuration point selecting the executor
  binary (payload → env → default). Pre-exists; this spec freezes it as the
  harness-agnosticism contract.
- **Fake ACP agent**: a deterministic scripted agent used by the test suite
  to drive the runner without docker/claude; the executable proof of US2.
- **Runner⇄host wire contract**: the NDJSON `event:`/`result:` stdout
  protocol and result schema — frozen, the layer-4/5 boundary.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The #538 shakedown scenario (install PR +
  feature-through-speckit + done-gate close) passes on the new runner with a
  result-contract diff of zero against the current runner.
- **SC-002**: The live-shakedown ladder L1–L3 and L5 passes on the new
  runner.
- **SC-003**: The full stubbed pytest suite is green, including the new fake
  ACP agent tests and the named workspace-vendor-neutrality regression test;
  zero-token guard tests untouched and green.
- **SC-004**: `pip list` inside the built sandbox image shows no OpenHands
  distribution; the image build completes from the slimmed requirements.
- **SC-005**: Layers 1–4 show zero behavioral diff (docs-only changes
  allowed); `git diff` outside the worker layer + docs + constitution
  wording is empty.
- **SC-006**: Pointing the command seam at the fake ACP agent runs a
  complete task with zero runner-code modification.

## Assumptions

- `claude-agent-acp` remains the shipped default executor and its ACP surface
  is stable enough to pin; the model stays claude over Pro/Max OAuth
  (Principle I) — this spec changes the *drive mechanism*, never the model.
- The ACP client is small (a few hundred lines) and lives in the worker
  layer; whether it is vendored into `runner.py` or split as a sibling
  module is settled below (Clarifications).
- Per-run usage stats are best-effort (clarified 2026-08-19): preserved if
  the protocol session exposes them, otherwise declared-absent (omitted
  field), never fabricated. Usage-limit pause detection does not depend on
  them.
- Sequencing gate from #542 is satisfied: the amputation landed (#563,
  2026-08-18) and round 2 (#574) is merged; the runner swap no longer resets
  any in-flight live evidence.
- Slicing: US1+US2 are one increment (the swap is only safe with its proof);
  US3 (image slim-down) can land in the same PR or trail as a follow-up, and
  the directory rename is a separate mechanical trailing PR — firmed at plan
  time under the N-PRs/end-of-week cap discipline.

