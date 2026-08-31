# Feature Specification: Code-map pointer + brief retention

**Feature Branch**: `029-code-map-brief`

**Created**: 2026-08-31

**Status**: Draft

**Issue**: lifekit-hq/devclaw#591

## Context

Workers spend a large fraction of every session re-deriving the codebase.
Owner observation 2026-08-22: roughly half a live session went to
"let me look at the HTTP server to understand the pattern" — orientation, not
work, paid for in quota on every dispatch.

Two independent root causes:

**Shape**: the accumulated `project_docs` brief contains only build and test
gotchas, never one line about where anything lives. The onboard task writes
`ARCHITECTURE.md` — a component map — as its primary artifact, but nothing at
dispatch time tells the worker it exists. Workers rebuild the map from scratch
every session.

**Size**: `MAX_BRIEF_CHARS` is 4000. The lifekit-dashboard brief was already at
3583 (2026-08-22 evidence), so it had begun evicting oldest-first. As briefs
mature they forget, and re-learning returns.

Onboard was broken in every deployed container until issue #588 / PR #590
merged. With that fixed, the missing piece is connecting the two: workers need
to be told the map exists, and the brief must not silently forget durable
operational facts.

## Clarifications (issue #591 answers, 2026-08-31)

- Q: How should the worker be pointed at ARCHITECTURE.md? → A: Two signals:
  (a) the dispatch brief prefix gets an explicit line when the file is present
  (mechanical fs check, zero-LLM, zero-token on idle paths), and (b) `_common.md`
  gains a standing instruction so the worker checks regardless of what the brief
  says.
- Q: Fix for SIZE — raise cap, tier, or per-category buckets? → A: Raise
  `MAX_BRIEF_CHARS` to 12 000 (3× the prior cap). The brief is the right
  mechanism; it just needs a bigger bucket. Tiering is deferred until evidence
  shows plain growth doesn't solve the problem.
- Q: Does detecting ARCHITECTURE.md add LLM calls or affect the idle-tick path?
  → A: No. The check happens inside `_dispatch_action` after `prepare_ws` has
  already run — purely a file-existence probe, never on idle or blocked paths.
- Q: Scope of tests? → A: Named structural tests per the done-when contract:
  presence when map exists, absence when map absent, cap raised confirms no
  eviction below the new limit.

## User Scenarios & Testing

### User Story 1 — Architecture map pointer at dispatch (Priority: P1)

A worker dispatched to a repo whose onboarding has produced `ARCHITECTURE.md`
receives an explicit pointer to that file in its dispatch brief, before the
goal text, so it reads the map before exploring rather than re-deriving it.
A worker dispatched to a repo without the map gets no pointer (no noise, no
broken link).

**Why this priority**: Directly addresses the session-time waste. A one-line
change to dispatch output buys every future session on an onboarded repo.

**Independent test**: build a temp workspace with and without `ARCHITECTURE.md`
and assert the pointer appears / is absent in the rendered dispatch prefix.

**Acceptance Scenarios**:

1. **Given** a workspace where `ARCHITECTURE.md` exists, **When** the dispatch
   brief prefix is rendered, **Then** the prefix contains an explicit line naming
   `ARCHITECTURE.md` and instructing the worker to read it before exploring.
2. **Given** a workspace where `ARCHITECTURE.md` does NOT exist, **When** the
   dispatch brief prefix is rendered, **Then** no `ARCHITECTURE.md` pointer
   appears (no broken link, no noise).
3. **Given** a `review_repository` action, **When** dispatched, **Then** the
   architecture pointer is NOT included (same exemption as the repo-notes brief).
4. **Given** a workspace whose `ARCHITECTURE.md` read fails (permissions, any
   OS error), **When** dispatch runs, **Then** the pointer is silently omitted
   and dispatch proceeds normally — never raises.

---

### User Story 2 — Brief retention: raise the cap (Priority: P2)

The accumulated repo brief no longer silently evicts the oldest operational
facts when it grows. `MAX_BRIEF_CHARS` is raised from 4 000 to 12 000 so the
lifekit-dashboard brief (3 583 chars as of 2026-08-22) has comfortable headroom
and typical multi-goal accumulation doesn't cause eviction.

**Why this priority**: The current cap was chosen before the brief mechanism was
in production use. Operational evidence (lifekit-dashboard near the limit after
a handful of goals) shows it is too tight.

**Independent test**: merge ≥ 5 000 chars of notes and assert all lines are
retained (no eviction under the new cap).

**Acceptance Scenarios**:

1. **Given** an existing brief of 5 000 chars, **When** new notes are merged,
   **Then** the existing lines are NOT dropped (the total fits under 12 000).
2. **Given** a brief whose combined size exceeds 12 000 chars, **When** merged,
   **Then** oldest lines are dropped (the cap still enforces a ceiling, just a
   higher one).

---

### Edge Cases

- A workspace with a stub or empty `ARCHITECTURE.md` (0 bytes) — the pointer
  should still fire (the map is being drafted); existence is the criterion.
- The architecture pointer must NOT appear in the "goal log" or delivery record
  — only in the worker's dispatch text (same invariant as the repo-notes brief:
  the dispatch brief is worker input, not the display goal; see `_display_goal`).
- No architectural-pointer fact should be written back as a REPO NOTE by the
  worker — the pointer is generated mechanically at dispatch time; workers
  learning from the map should write *content* notes (what they discovered), not
  announce that the file exists.

## Requirements

### Functional Requirements

- **FR-001**: When dispatching any non-review_repository action to a workspace
  that contains `ARCHITECTURE.md` at the root, the dispatch brief prefix MUST
  include an explicit line naming the file and instructing the worker to read it
  before exploring.
- **FR-002**: When `ARCHITECTURE.md` is absent, the prefix MUST NOT contain any
  reference to it.
- **FR-003**: The architecture map pointer MUST be generated by a pure function
  in `repo_brief.py` (the module that owns all brief assembly), tested in
  isolation without mocking filesystem — real tmp dirs only.
- **FR-004**: The detection MUST be best-effort and never-raises: an OS error
  during the check silently omits the pointer and dispatch proceeds.
- **FR-005**: `_common.md` MUST instruct workers to read `ARCHITECTURE.md` if
  it exists at the repo root, before exploring the tree — this is a
  belt-and-suspenders instruction that fires whether or not the dispatch brief
  includes the pointer.
- **FR-006**: `MAX_BRIEF_CHARS` MUST be raised from 4 000 to 12 000. The
  eviction policy (oldest-first on exact-duplicate-stripped lines) is unchanged.
- **FR-007**: All standing invariants hold: zero-token idle guard (the check is
  inside `_dispatch_action`, never on idle/blocked paths), single-writer-to-state
  (no state write is added), model-agnostic worker seam (the instruction is
  plain markdown).

### Key Entities

- **Architecture map pointer**: a short plain-text section prepended to the
  dispatch brief prefix when `ARCHITECTURE.md` is present at the workspace root;
  generated by `architecture_map_pointer(workspace_dir)` in `repo_brief.py`.
- **MAX_BRIEF_CHARS**: the module-level constant in `repo_brief.py` capping
  accumulated brief size; raised to 12 000.

## Success Criteria

- **SC-001**: Named test `test_dispatch_includes_architecture_pointer_when_map_exists`
  passes (pointer present in prefix).
- **SC-002**: Named test `test_dispatch_skips_architecture_pointer_when_no_map`
  passes (pointer absent when file missing).
- **SC-003**: Named test `test_brief_retains_facts_under_raised_cap` passes
  (5 000-char existing brief + new notes all retained, no eviction).
- **SC-004**: The zero-token guard tests (`FakeClaude.calls == 0`) remain green.
- **SC-005**: Full pytest suite ≥ baseline, `ruff check .` clean, `mypy` clean.

## Rejected Alternatives

- **LLM summarization of the map pointer**: forbidden by the zero-token idle
  guard; the file-existence check is mechanical (OS call, not cognition).
- **Tiering the brief** (navigational facts outrank transient ones): deferred
  — raise the cap first and measure; tiering adds complexity not yet justified
  by evidence.
- **Storing ARCHITECTURE.md content in the brief** (prepend the whole file):
  the brief is for operational notes, not design documentation; pointing the
  worker at the file lets them read the current version, not a stale snapshot.
- **Dispatch-boundary project_docs field** (write the map path into the DB):
  unnecessary machinery; a file-existence check at dispatch time is simpler
  and has the same effect.
