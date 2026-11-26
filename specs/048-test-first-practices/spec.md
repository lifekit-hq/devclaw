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
changes; a negated "breaking", a file permission or a docs migration is not L. S covers one named file with no behaviour change. Everything else is
M. An issue cannot declare its own size. The size is stored on the goal row and shown by
`get_goal` and `list_goals`. The session's prompt opens with it. S skips the test
pass, the mutation check, and the optional workflow stages. L skips none.

### User Story 2 — the test writer is not the coder (P1)

On M and L goals with `## Done when` criteria, on a branch with no commit beyond
the default branch but the host's `wip(devclaw):` snapshots:

- A fresh session writes tests and no code beyond the minimum stub the tests
  need to fail for the right reason. A quota pause mid-pass restarts it
  as a test writer, and its snapshotted partial tests count as the pass's files.
- Verify must be red.
- The engine commits what the writer changed, and a branch-named trailer is the
  record. Only files named like tests (a `tests/`, `test/`, `spec/` or
  `__tests__/` directory, a `.Tests` project, `test_*`, `*_test.*`, `*_spec.*`,
  `*.test.*`, `*.spec.*`, in any case) and runner config are frozen; a stub,
  manifest or lock file is not. A writer whose files match none of these is not
  stopped: red is still required, nothing is frozen, and the goal thread is
  told no test file was recognised.
- A second fresh session writes the code.
- The gate refuses a later diff that edits or deletes a test-pass file, or the
  verify script and runner config present at that commit (`.devclaw/verify`,
  `conftest.py`, `pytest.ini`, jest and vitest config), and names the file.
  Runner config the goal's issue names is in scope and not frozen; no other
  exception. Changes merged in from the default branch are not the code pass's;
  a frozen file they conflicted in is accepted in any resolution, and noted. The same
  check runs on the head the hand-off shipped: a touched file there ends the
  task `BLOCKED` naming it, and the pushed pull request is left as it is.
- New test files are allowed.
- A branch that already carries work and no record skips the test pass.
- The record carries a hash of the criteria. When they change, the test pass runs
  again and refreezes the set. A brief missing one of the goal's issues (a failed
  read) is not a change.

### User Story 3 — one mutation per critical criterion (P1)

For each criterion tagged critical, the session names one single-line mutation.
The engine breaks that line in a detached worktree of HEAD carrying the working
tree's changes, and requires verify to go red there. A survivor fails the gate
with the criterion named. The mutation may not target a test-pass file or a file
named like a test. Verify red there unmutated means the check could not run
here: the branch is not charged for it, and the fact is posted once per goal on
the goal thread — not in the pull request description. A goal with no
critical criterion skips the check. The check runs before the hand-off only.

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
  case records the baseline cost as two turns (a session and its hand-off) and
  asserts that S adds none.

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
- **Leaving `.devclaw/verify` and the runner config editable.** Rejected in
  review (2026-11-26): the criterion forbids a code pass that skips a frozen
  file, and a rewritten verify script or `collect_ignore` skips it on goals with
  no critical criterion. Freezing those files is the check; no
  collection-analysis machinery.
- **A declared `Size:` line in the issue.** Rejected in review (2026-11-26): it
  let any issue opt out of the test pass and the mutation check. Only the
  written rules set the size.
- **Showing the size in the console now.** The goal page's screen baselines would
  move. It is a follow-up together with the baseline update.
