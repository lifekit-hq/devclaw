# Research: Worker Context-Budget Invariant (spec 021)

Phase 0 output. Every decision below is grounded in a code read of the current
tree (2026-08-26); file/line references are to `main`.

## R1 — Context-usage signal: what actually exists

**Decision**: The tripwire consumes the agent's `usage_update` session
notifications, which carry `{"used": <tokens>, "size": <window>}` — observed
live from the production `claude-agent-acp` worker on 2026-08-26 (task
`84d3455e`, e.g. `{"sessionUpdate": "usage_update", "used": 55724, "size":
200000}`). `runner/acp_client.py` currently lets this kind fall through to a
generic `ACPUpdateEvent` and its `accumulate_usage` scavenger
(`_USAGE_FIELD_ALIASES`, acp_client.py ~L77–L118) does not recognize
`used`/`size`. The client gains: recognize `sessionUpdate == "usage_update"`,
track the latest `used/size` ratio, and expose it to the pump loop via a
callback/threshold check. Parsing stays TOLERANT: unknown shapes are ignored;
no usage stream ⇒ ratio unknown ⇒ tripwire inert with one loud note in the
result (FR-007).

**Rationale**: the signal already flows in production; the fix is a parser,
not new telemetry. Tolerant parsing keeps the model-agnostic seam — another
ACP agent that reports nothing degrades loudly, not wrongly.

**Alternatives considered**: (a) runner-side token estimator (chars/4 on
transcript) — rejected: fabricated numbers, exactly what the
"declared-absent, never fabricated" comment in `accumulate_usage` forbids;
(b) additive input+output accumulation from the existing alias table —
rejected: no window size ⇒ no percent; the `usage_update` kind carries both.

## R2 — How the "land-now" nudge can physically work

**Decision**: There is NO mid-turn message injection in ACP —
`AcpClient.run()` sends exactly one `session/prompt` per session (acp_client
~L201–239) and pumps until it returns. The tripwire therefore: (1) sends
`session/cancel` for the running turn, (2) sends a SECOND `session/prompt` in
the SAME session — the land-now instruction ("finish the current coherent
piece: commit, update tasks.md honestly, stop") — and pumps that turn to
completion, (3) proceeds to the normal verify/materialize/result path. At most
one tripwire firing per session.

**Consequence fixed alongside**: `runner.py` ~L1441 only special-cases
`stopReason == "refusal"`; a `"cancelled"` turn currently flows through as
`status: "ok"` — a live fail-open hole. With this spec: `cancelled` WITHOUT a
completed land-now follow-up is not an ok result (fails closed as an error);
`cancelled` AS PART OF the tripwire sequence is fine because the follow-up
turn's outcome is what settles.

**Rationale**: the session (and the agent process) survives a turn cancel, so
the follow-up prompt keeps all in-flight file state; this is the only
ACP-level lever that exists, and it doubles as the fix for the cancelled→ok
hole (constitution V).

**Alternatives considered**: injecting via permission-response side-channel —
rejected: not a message channel, semantics abuse; killing the process and
re-dispatching — rejected: loses the session's uncommitted work, which is the
thing US2 exists to save.

## R3 — What a "chunk" is, concretely

**Decision**: A chunk IS the speckit story-slice the worker already plans:
one `[US<n>]` slice in the current feature's `specs/NNN-*/tasks.md`. No new
artifact or format. The chunk-plan artifact of the spec = the committed
speckit trio (`spec.md`, `plan.md`, `tasks.md`); the "typed contract" = the
checkbox grammar `slice_guard` already parses host-side
(`devclaw/goal/slice_guard.py`: `_TASKS_PATH_RE`, `_TASK_ID` `T\d+`,
`_STORY_TAG` `US\d+`, ~L60–L89). Per-chunk distilled context (Axis B) lives
in the feature's `plan.md` (the skill already says "record load-bearing
choices in plan.md" — `runner/skills/_writes-code/05-speckit-memory.md`).

**Rationale**: the worker-side vocabulary, the artifact, the host parser, and
the "one slice = one reviewable PR, never build ahead" instruction ALL exist
(skill text + slice_guard). Spec 021 turns the instructional rule mechanical
(the #358 class) instead of inventing a parallel plan format that would fork
direction memory. It also matches the 2026-08-18 unit-of-work ruling (the
increment is the Unit of Work).

**Alternatives considered**: a new `chunks.md` ledger — rejected: second
plan-shaped artifact = silent-fork risk (#610 class), invisible to
slice_guard and the console's plan tab, and duplicates tasks.md; host
extracts per-chunk sub-prompts (dispatch-shaped chunking, clarify Q2 option
C) — rejected in clarify: host plan-parsing cognition.

## R4 — Runner-side slice enforcement mechanics

**Decision**: At session start the runner snapshots `specs/*/tasks.md`
checkbox state (workspace reads, stdlib). After each tool-call notification
it re-reads the files (small; a stat gate misses same-size flips inside one
timestamp granule) and computes flipped slices
exactly like `slice_guard.count_slice_advances`: when ≥1 full story-slice has
flipped complete relative to the session-start snapshot AND the worker starts
touching tasks OUTSIDE that slice, the runner ends the turn via the R2
cancel+land-now sequence with a "slice complete — stop" instruction. A
single-chunk ask (no `specs/` tree, or one slice total) never triggers it —
FR-005's no-ceremony path is the absence of a multi-slice tasks.md.

The runner CANNOT import `slice_guard` (zero-dep standalone file baked into
the image); it carries a ~30-line parser mirroring the grammar, and the
grammar is frozen in `contracts/chunk-grammar.md` with tests on BOTH parsers
against shared fixtures so they cannot drift apart.

**Rationale**: mechanical enforcement at the seam that already sees every
agent step; the trigger is "flipped complete + kept going", not "flipped
complete" alone, so the worker's own wrap-up (commit, notes) is never
truncated mid-landing.

**Alternatives considered**: enforce purely by tripwire (clarify Q2 option B)
— overruled by Denys: instruction-only decays (#358); host-side post-hoc
slice_guard verdict only — that's detection after the context is already
spent, not prevention.

## R5 — Chunk done-ness derivation (clarify Q3)

**Decision**: No new state. "Which chunks are done" = the host's existing
settle records: `tick_settle._resolve_polling_action` already appends a
delivery per settled action (`append_delivery`, idempotent on `ref_id`) and
`store.increment_records(goal_id)` already renders prior increments into the
next brief (capped at 6,000 chars by `prompt_budget.cap_prior_increments`).
The continuation brief additionally names the current feature dir and lets
the worker's own start-of-session rule ("find the smallest incomplete slice
in tasks.md") select the next chunk — tasks.md checkboxes committed by prior
sessions ARE the workspace's memory of progress, and the settle record is the
authoritative cross-check the host uses (e.g. a slice whose task settled
`failed` gets failure context attached even if its boxes were flipped).

**Rationale**: FR-002/FR-003 satisfied with zero schema change; the capped
prior-increments section is exactly the "input MUST NOT grow" mechanism and
it already exists.

## R6 — Oversized-chunk marking and the no-identical-retry rule (FR-008)

**Decision**: When a session ends via tripwire (or dies with the
`_PROMPT_TOO_LONG_MARKER`, `devclaw/queue/settle.py` ~L128/L1348), the settle
path stamps the failure/delivery detail with a structured marker naming the
active slice (from the runner's result payload). `_advance_brief`'s existing
failure-context branch (tick.py ~L415–442, already special-cases overflow
advice) renders it as "slice T00x/USn is oversized — re-slice it in tasks.md
before implementing". The existing brake stack (retry cap, circuit breaker
`_check_and_trip_breaker`, done-gate churn) bounds repeated re-slicing; no
new brake is invented.

## R7 — Tripwire observability (FR-006/FR-007)

**Decision**: The runner emits an `event:` line (`type: "ContextTripwire"`,
payload: used/size/threshold/active slice) — persisted automatically via
`settle.py::_append_task_event` → `state_store/observability.append_event`
with zero host changes — AND sets a marker field in the result JSON. The host
settle path, on seeing the marker, calls `StateStore.record_problem(
category="limit", kind="context_tripwire", recovered=True, ...)` — the
documented one-line integration (`state_store/problems.py` ~L170). This makes
firings countable per goal/repo/cycle via the existing problems + cycle-report
machinery, and SC-005's ratchet metric is readable from `list_problems`.

## R8 — Configuration

**Decision**: One knob: `DEVCLAW_CONTEXT_TRIPWIRE_PCT`, default `75`, `0`
disables. Declared as a call-time accessor in `devclaw/config.py` (the
`goal_tick_seconds()` pattern, ~L132) + documented in
`docs/reference/env-vars.md` (the doc-sync test enforces this). The engine
forwards it into the sandbox env exactly like `DEVCLAW_SANDBOX_MEMORY`/`_CPUS`
(runner env allowlist, runner.py ~L1396–1425); the runner reads it with its
own `os.environ.get` (config.py's doorway explicitly excludes `runner/`,
config.py L23). Instance-level only; per-project overrides deferred until
evidence demands them (spec Assumption).

## R9 — Skill text changes (Axis A instruction + Axis B diet)

**Decision**: `runner/skills/_writes-code/05-speckit-memory.md` gains: (1)
the chunk contract stated once, imperatively — one slice per session, the
harness enforces the stop; (2) per-slice distilled context: while planning,
record for EACH slice the files/areas it touches and constraints learned, in
the feature's plan.md; when implementing a slice, read plan.md's entry and
the repo brief FIRST, explore raw files only within the slice's declared
surface, and refresh a stale entry explicitly. `_common.md` pull-order gains
the repo brief pointer if absent. Brief-size ceilings in
`tests/test_runner_skills.py` (<13000/<12600) are bumped deliberately in the
same PR, per the existing convention. Prompt-style rules apply (state once,
no war stories in the template).

## R10 — Test strategy

**Decision**: Follow the two existing harnesses. Fake-agent scripts added to
`tests/acp_fake_agent.py` (`script_<name>` pattern): `usage_window` (streams
usage_update with rising used/size), `slice_flip` (writes a tasks.md flip
then keeps going — asserts the runner stops it), `overrun_landing` (receives
cancel + land-now prompt and completes). Driven subprocess-level in
`tests/test_runner_acp.py`. Host-side: `FakeEngine.dispatched` briefs
asserted for bounded continuation input and oversized-slice framing
(`tests/test_goal_tick.py` / `test_thin_plan_advance.py` styles); settle
marker → `record_problem` asserted against the store. Named regression tests
per behavior, per repo convention. Zero-token guards untouched and re-run.
