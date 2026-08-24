# Feature Specification: Error-Issue Schema & Single Filing Doorway

**Feature Branch**: `014-issue-doorway`

**Created**: 2026-08-24

**Status**: Draft

**Input**: User description: "One machine-parseable issue schema (source, fingerprint, evidence, expected-vs-actual, severity, proposed done_when) filed through one doorway module, used by every machine-found problem: validator findings, deploy smoke, problems catalog. Grilled and locked with Denys 2026-08-24."

## Why (direction memory)

Grilled end-to-end with Denys on 2026-08-24 (captured in the owner vault, log entry
2026-08-24). The driving requirement, in his words: *"if there is an error from the
error pipeline — the issue will be filed in a predictable way with predictable
schema, so we get the predictable output."* Today devclaw's machine-found problems
reach the owner through heterogeneous surfaces — problems-catalog rows
(machine-shaped but not issues), self-filed issues in loose prose, owner pings,
`blocked_on` strings. None of them arrives in a shape the intake-grading loop can
consume without human rewriting. This spec makes every machine-found problem arrive
as ONE kind of artifact: a gradeable, dispatchable GitHub issue in a fixed schema.

This is the first of two specs from that session: spec 015 (live-validation loop)
and the post-deploy smoke both *write into* this doorway, which is why it ships
first and alone.

**Rejected alternatives** (recorded per the direction-memory rule):

- *Per-mechanism ad-hoc issue text (status quo)* — rejected: unparseable variety is
  exactly the unpredictability being removed; every consumer pays a rewriting tax.
- *Adopting `kunchenguid/no-mistakes` wholesale* — rejected 2026-08-24: it is a
  pre-push workstation gate at the wrong altitude, duplicating devclaw's
  server-side gate chain, and its interaction model assumes a human at a keyboard
  mid-pipeline. Its typed findings-contract *idea* is deliberately borrowed here.
- *Extending the problems catalog instead of filing issues* — rejected: the catalog
  is a gatherer-signal readout by design (N1/#371); GitHub Issues are the single
  canonical store of intent. The doorway strengthens that boundary rather than
  blurring it.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A machine-found problem becomes a predictable issue (Priority: P1)

Any devclaw mechanism that discovers a problem — a gate advisory that shipped under
`trust`, a deploy-smoke failure, a validator finding (spec 015), a recurring
problems-catalog root cause — files it as a GitHub issue whose body follows one
fixed, machine-parseable schema. The owner opens any machine-filed issue on any
repo and sees the same sections in the same order; the intake-grading loop can
grade it without a human rewriting it first.

**Why this priority**: The schema and the single writer ARE the feature; every
other story and both downstream specs depend on them.

**Independent Test**: Trigger one filing from a stubbed source and assert the
created issue body parses against the schema and carries every mandatory field.

**Acceptance Scenarios**:

1. **Given** a mechanism reports a problem with source, evidence and severity,
   **When** it files through the doorway, **Then** an issue is created whose body
   contains the schema sections (source, fingerprint, evidence,
   expected-vs-actual, severity, proposed done-when) in canonical order, each
   machine-extractable.
2. **Given** the schema version changes in the future, **When** an issue is filed,
   **Then** the body carries the schema version so consumers can parse old and new
   issues deterministically.
3. **Given** a filing attempt fails (network, auth, permissions), **When** the
   doorway cannot create the issue, **Then** the failure is loud — surfaced on the
   originating mechanism's error path and in the problems catalog — never a silent
   drop.

---

### User Story 2 - Filing is idempotent by fingerprint (Priority: P2)

Re-encountering the same root cause never files a duplicate. The doorway
deduplicates on the problem fingerprint: a second occurrence updates the existing
open issue (occurrence count, last-seen, fresh evidence) instead of creating a new
one. A fingerprint whose issue was closed and which recurs afterwards reopens the
conversation as a recurrence, explicitly marked as such.

**Why this priority**: Without dedup the post-deploy smoke and the validator would
flood the backlog on every run — predictable output includes predictable *volume*.

**Independent Test**: File the same fingerprint twice; assert one issue exists with
an incremented occurrence record.

**Acceptance Scenarios**:

1. **Given** an open issue exists for fingerprint F, **When** F is reported again,
   **Then** no new issue is created and the existing issue records the new
   occurrence with its evidence.
2. **Given** the issue for fingerprint F was closed, **When** F recurs, **Then**
   the recurrence is filed referencing the closed issue (comment-and-reopen or
   linked follow-up — one behavior, chosen at planning), marked as a regression.

---

### User Story 3 - The problems catalog files through the doorway (Priority: P3)

The existing self-issue-filing path (problems catalog → GitHub issue) is migrated
onto the doorway, so the catalog's `issue_number`/`lifecycle` linkage continues to
work while the issues it produces gain the schema. After this story there is
exactly one code path in devclaw that creates issues from machine findings.

**Why this priority**: Valuable but not blocking — the doorway is complete and
usable by spec 015 without migrating the legacy path; migrating it removes the
last second writer.

**Independent Test**: Drive the catalog's filing path in the stub environment and
assert the resulting issue parses against the schema and the catalog row links it.

**Acceptance Scenarios**:

1. **Given** a catalog root cause crosses its filing threshold, **When** it is
   filed, **Then** the issue conforms to the schema and the catalog row's
   `issue_number`/`issue_state`/`lifecycle` behave exactly as before.
2. **Given** the migration is complete, **Then** no mechanism outside the doorway
   module creates issues from machine findings (enforced structurally, in the same
   spirit as the views-never-read-back guard).

---

### Edge Cases

- Evidence larger than an issue body allows: truncate deterministically with an
  explicit truncation marker; never fail the filing because evidence is verbose.
- A problem with no meaningful expected-vs-actual (e.g. a crash): the section is
  present and says so explicitly — absent-but-stated, never omitted.
- The doorway is invoked while GitHub is unreachable: the finding is not lost —
  it remains queued/recorded on the originating surface and the failure is loud
  (Scenario 1.3); retry policy is a planning decision.
- Two sources report the same underlying defect with different fingerprints: the
  doorway does not guess; they file as two issues and a human merges. Fingerprint
  design (planning) should make this rare, not impossible.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: One schema defines a machine-filed issue: `source` (which mechanism
  found it), `fingerprint` (stable dedup key), `evidence` (what was run and what
  it showed), `expected vs actual` (with a spec-scenario reference when the source
  is the spec-015 validator), `severity`, and `proposed done_when` (a draft
  completion contract a fixing goal could adopt). Every field is mandatory;
  "unknown" is an explicit stated value, never an omitted section.
- **FR-002**: Exactly one doorway module creates issues from machine findings; it
  is the only writer on that path and is structurally guarded against bypass.
- **FR-003**: The issue body is machine-parseable AND human-readable — the owner
  reads it as a normal issue; the intake grader parses it without heuristics.
- **FR-004**: The schema is versioned in-band.
- **FR-005**: Filing is idempotent by fingerprint (US2 semantics).
- **FR-006**: Filing failure is loud, never silent (fail-loud invariant); the
  originating mechanism's outcome reflects it.
- **FR-007**: The doorway is mechanical — zero LLM calls; the zero-token idle
  guard is unaffected.
- **FR-008**: Issues filed through the doorway are consumable by the existing
  intake-grading loop without modification to grading.

### Key Entities

- **Machine finding**: the doorway's input — source, fingerprint, evidence,
  expected-vs-actual, severity, proposed done-when.
- **Filed issue**: the GitHub issue rendering of a finding, schema-versioned.
- **Occurrence record**: the per-recurrence update attached to an existing issue.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of machine-filed issues parse against the schema (asserted by
  a named regression test over each producing path).
- **SC-002**: Zero duplicate open issues for the same fingerprint across repeated
  runs of the same failing condition.
- **SC-003**: A machine-filed issue can be graded by the existing intake loop and
  dispatched as a goal without any human edit to its body.
- **SC-004**: After US3, exactly one code path in devclaw creates issues from
  machine findings (structural test).

## Assumptions

- GitHub Issues remain the canonical intent store (N1/#371 stands).
- The doorway lives host-side; sandboxed workers do not file issues directly (the
  sandbox carries no GitHub credential — unchanged).
- Consumers of the schema in this arc: intake grading, the owner, spec 015's
  validator and the post-deploy smoke as producers.

## Out of Scope

- The validator, `validate_product` task kind, QA goals, e2e-test policy — spec 015.
- Any change to intake grading itself (FR-008 requires compatibility, not change).
- Issue triage/prioritization logic; labels beyond what filing needs.
- Migrating human-authored issue flows — this governs machine-found problems only.
