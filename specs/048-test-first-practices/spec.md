# Feature Specification: size, tests first, one mutation

**Feature Branch**: `fm/idea-devclaw-1053`

**Created**: 2026-11-26

**Status**: SHIPPED — 2026-11-26: all three stories built in one PR (#1053). The remainder is a live read: the first goals that run a test pass on a real repository.

**Input**: issue #1053, "Take three practices: test-writer separation, S/M/L task
sizing, one-mutation check". The owner said "do this" on 2026-11-24, and the issue
was shaped on 2026-11-26. The shaped issue is the clarify record: its `## Done when`
is the contract and its `## Out of scope` is the boundary.

## North-star case *(mandatory — constitution 2.9.0)*

- **Failure moved**: **ran and produced garbage.** An agent that writes the code
  and its tests together can delete or weaken a failing test to get to green. Then
  verify, CI and the done-gate all read a branch whose tests no longer pin the
  contract. The same session writes tests that pass whatever the code does.
- **Number that shows it**: first-pass rate and rounds-to-close on goals that run
  a test pass, against goals that do not. A second number is the count of
  test-pass refusals (`TestPass` with `red: false`, a frozen-file refusal, or a
  surviving mutation) per week. A non-zero count is garbage caught before it
  reaches CI.
- **Cut condition**: the practices go if the next 14 nights' goals show the test
  pass adding rounds without lowering the done-gate's not-achieved rate. The first
  cut is S being the default for every small change, which already costs nothing.

## User Stories

### User Story 1 — the size is set once from the issue (P1)

`create_goal` sets S, M or L from the issues by the rules in
`docs/architecture.md`. L covers money, auth, data migrations and breaking API
changes. S covers one named file with no behaviour change, or an explicit
`Size: S`. Everything else is M. The size is stored on the goal row and shown by
`get_goal` and `list_goals`. The session's prompt opens with it. S skips the test
pass, the mutation check, and the optional workflow stages. L skips none.

### User Story 2 — the test writer is not the coder (P1)

On M and L goals with `## Done when` criteria:

- A fresh session writes tests and no code.
- Verify must be red.
- The engine commits the file set, and a branch-named trailer is the record.
- A second fresh session writes the code.
- The gate refuses a later diff that edits or deletes a test-pass file, and names
  the file.
- New test files are allowed.

### User Story 3 — one mutation per critical criterion (P1)

For each criterion tagged critical, the session names one single-line mutation.
The engine breaks that line in a scratch copy and requires verify to go red there.
A survivor fails the gate with the criterion named. A goal with no critical
criterion skips the check.

## Requirements

- **FR-001** The rules and checks live in `devclaw/engine/practices.py`. Its
  callers are the engine (`engine/claude.py`: `_test_pass`, `_gate`) and
  `create_goal`, for the size only. The goal layer stays under its 1,500-line
  ceiling.
- **FR-002** Git and the goal row's one identity column hold all of it. There are
  no host records, task columns or counters (pillar 5).
- **FR-003** A refusal is a fact handed back through the existing verify rounds.
  Running out of rounds is `INTERRUPTED`, as a red verify is. A test pass that is
  green before any code is `BLOCKED` with that reason. No new exit, owner verb or
  retry is added.
- **FR-004** Tripwires live in `tests/test_practices_gate.py`. They use real git
  and a real verify script, and stub only the session and the hand-off. The S-size
  case records the baseline cost as two turns and the stub's usage, and asserts
  that S stays at it.

## Rejected alternatives

- **The test pass as a goal-layer task kind** (a separate row before the code
  row). This needs a new tick rule and goal-layer lines past the ceiling. The
  engine already owns the in-session loop, so the two sessions run in one task.
- **The test-pass file set as a host record** (a `<!-- devclaw:test-pass -->`
  comment or a task column). Either one is derived state. A commit trailer travels
  with the branch it describes and dies with it.
- **Mutation by a generic operator** (flip every `>=` in the diff). This is
  language-specific and fails on most files. The model knows which condition a
  criterion depends on, and the host only checks that breaking it turns the tests
  red.
- **Freezing `.devclaw/verify` too.** A code pass may need to change how the
  project verifies, and a frozen script turns that into an owner question. Using
  the verify script to deselect the frozen tests is a follow-up. On critical
  criteria the mutation check already catches it.
- **Showing the size in the console now.** The goal page's screen baselines would
  move. It is a follow-up together with the baseline update.
