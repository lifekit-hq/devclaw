# Feature Specification: Worker file memory — the repo carries the mind, the prompt carries the task

**Feature Branch**: `034-worker-file-memory`

**Created**: 2026-09-01 (renumbered from 031 on 2026-09-05 — the 031 slot went to problem-resolution; 033 is skipped to avoid aliasing `feat/033-durable-container-secrets`)

**Status**: Draft — specified and clarified, deliberately NOT armed; queued behind the correctness lane (spec 032 US4 provisioning, #793 done-gate clause pinning, #817 transient pause-and-resume)

**Input**: User description: "the worker file memory proposal" (brainstorm capture `2026-09-01-devclaw-worker-file-memory`, ~/memory/system/proposals.md)

## Why (context the requirements hang off)

The worker's repo-scoped memory is push-based prose today: each settled run
hands back free-text notes, the host appends them to an accumulated blob
(deduping only byte-identical lines), and every dispatch injects the whole
blob into the worker's prompt. Observed 2026-09-01 on the devclaw project
itself: the same three facts repeated fifteen rephrased times, ~2.5KB of a
9.4KB brief. Push-based memory rots because the *artifact* is never edited —
only appended to — and no one owns deduplication.

The proven alternative is the shape the operator's own harness uses: an
amnesiac session plus a **committed file memory** — a small always-read index,
one fact per file pulled on demand, maintained under an explicit write policy
(update-over-append, delete wrong facts). Knowledge accumulates in the
*repository*, deduplicated by editing, reviewed like code, and readable by any
agent. This also lands the vault's standing ruling — "ephemeral body, durable
mind: the agent persists as state on disk" — at the worker layer, and applies
the environment-is-the-instruction doctrine (2026-08-25): a prose instruction
re-sent every run is a design smell; the workspace should make correct
execution implicit.

## Clarifications

### Session 2026-09-01

- Q: When this deploys, what happens to the existing accumulated-notes blobs and projects not yet re-onboarded — migration machinery, or a hard cut? → A: Hard cut. Deploy stops note-injection instance-wide and drops the blobs; onboard seeds an EMPTY `.devclaw/` layout; facts are re-recorded organically by workers. No one-shot migration code, no per-project coexistence of the two lanes.
- Q: How is memory growth governed — hard cap, advisory signal, or nothing? → A: Advisory. The write policy (update-over-append, delete-wrong, mechanize-first) is the primary control; the doctor memory check additionally WARNs when the index grows past a generous threshold (~30 facts) — a curation smell surfaced for a human, nothing dropped or blocked. No hard cap: a cap silently drops facts, the old lane's exact symptom-treatment.
- Q: What structure does a fact file carry — plain markdown, YAML frontmatter (the operator's vault schema), or no convention? → A: Plain. One `# title` heading + free prose body; the index line is `- [title](memory/<file>.md) — one-line hook`. The design adopts the operator-memory PROTOCOL (always-read index, one fact per file, update-over-append, pull-on-demand), not its YAML header — nothing in the system parses fact internals, and plain markdown is the model-agnostic floor.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The worker reads its memory from the repo, not the prompt (Priority: P1)

A project repo carries a committed `.devclaw/` memory: a `MEMORY.md` index
(one line per fact) plus `memory/*.md` fact files. The dispatch brief stops
injecting accumulated repo notes; when the folder exists it carries a
one-line pointer instead. The worker's standard instructions tell it to read
the index at session start and pull individual facts when relevant.

**Why this priority**: This is the relocation itself — it removes the rot
mechanism (prompt injection of an append-only blob) and delivers the token
saving immediately, even before the worker writes anything back.

**Independent Test**: Dispatch a task on a repo with a seeded `.devclaw/`
memory. The rendered brief contains no repo-notes block and does not grow
with the number of stored facts; the worker session's event stream shows it
reading the index; a known recorded gotcha (e.g. the private-TMPDIR rule) is
still respected in the run.

**Acceptance Scenarios**:

1. **Given** a project repo with `.devclaw/MEMORY.md` and N fact files,
   **When** a task is dispatched, **Then** the rendered worker prompt
   contains a single memory pointer line and zero fact bodies, and its size
   is independent of N.
2. **Given** a project repo with no `.devclaw/` directory, **When** a task is
   dispatched, **Then** the rendered prompt is byte-identical to today's
   no-notes brief (safe rollout; a project that never opted in is untouched).
3. **Given** a fact recorded in `.devclaw/memory/` by a previous goal,
   **When** a NEW goal on the same repo dispatches, **Then** the fact is
   reachable by the worker (file read) with zero prompt bytes spent on it —
   memory survives goal teardown because it lives in the repo, not the goal.

---

### User Story 2 - The worker maintains the memory; the hand-back lane retires (Priority: P2)

The worker records durable repo facts by **editing** `.devclaw/` files in its
increment — updating or deleting stale entries rather than appending reworded
copies — under a write policy carried by its standard instructions:
one fact per file, update-over-append, delete wrong facts, never store
goal-scoped state, and **mechanize-first** (a lesson that can become a
committed guard, wrapper, or default MUST become that, not prose — memory
holds only what cannot be enforced by the environment). The settle-side
hand-back lane (free-text notes parsed into an accumulated host-side blob)
is retired in the same arc, with its tests removed symmetrically.

**Why this priority**: Without the write path, memory goes stale and the old
lane keeps feeding the blob. With it, dedupe happens the only way that works
— by editing the artifact — and every memory change is reviewable in the PR
diff.

**Independent Test**: Run an increment whose worker learns a repo fact. The
increment's PR diff shows a `.devclaw/` edit (new fact, or an existing file
updated in place); the host stores no accumulated notes blob for the repo;
re-learning the same fact in a later run produces an edit or a no-op, never a
second near-duplicate file.

**Acceptance Scenarios**:

1. **Given** a worker that hits a repo gotcha already recorded in memory with
   stale wording, **When** its increment settles, **Then** the PR contains an
   in-place update to that fact file — not a new file restating it.
2. **Given** the retired hand-back lane, **When** any increment settles,
   **Then** no accumulated-notes state is written host-side for the repo, and
   the next dispatch reads memory only from the repo.
3. **Given** a lesson that is mechanizable (e.g. a required env var for the
   test command), **When** the worker records it, **Then** it lands as a
   committed mechanism (wrapper/config/guard) with at most a pointer in
   memory — not as a prose instruction.
4. **Given** goal-scoped context (what this goal already attempted, why the
   last attempt failed), **When** increments settle, **Then** none of it
   appears in `.devclaw/` — it stays in the dispatch brief's failure-context
   and steering sections.

---

### User Story 3 - One standardized task prompt (Priority: P3)

The generic task-execution instructions (advance-one-increment, speckit
usage, delivery expectations, the memory read/write policy) live as ONE
canonical worker skill in the existing worker-kind instruction home, versioned
with the image. The per-dispatch brief shrinks to exactly: a pointer to the
standard + the goal/task text + the live referenced-issue contract + failure
context + steering. Onboarding seeds an empty `.devclaw/` layout in the
project, so a fresh repo starts with the structure and the write policy in
place; existing accumulated-notes blobs are dropped at deploy, not migrated
(clarified 2026-09-01) — workers re-record what still matters organically.

**Why this priority**: Valuable but separable — US1/US2 work with the brief's
current instruction text; this story makes the prompt "sharp and standard"
and closes the rollout.

**Independent Test**: Diff two rendered briefs for different repos/goals:
the instruction portion is identical (the standard), and everything else is
task-specific. Onboard a repo and observe the seeded `.devclaw/` in the
install PR.

**Acceptance Scenarios**:

1. **Given** two different projects, **When** tasks are dispatched on each,
   **Then** the instruction portion of both briefs is the same canonical
   text, sourced from the single worker-skill home.
2. **Given** a project with an existing accumulated-notes blob, **When** the
   change deploys, **Then** note-injection stops for that project immediately
   (no per-project coexistence) and the blob is dropped; **When** the project
   is next onboarded/refreshed, **Then** the install PR seeds an empty
   `.devclaw/` layout with the write policy stated in-file.
3. **Given** the live referenced-issue contract, **When** any brief renders,
   **Then** issue bodies are still fetched fresh and included in full — the
   one deliberate push exception (freshness by construction; the sandbox
   carries no tracker credential).

---

### Edge Cases

- **Repo not yet seeded**: no `.devclaw/` → brief renders with no pointer,
  byte-identical to a no-notes brief today. Absence is a supported state,
  never an error.
- **Worker records a wrong fact**: it ships in a reviewed PR diff (auditable)
  and the write policy's remedy is deletion by a later run or the human;
  the post-merge review and the trend detector are the curators. A wrong
  fact is a content bug, not a mechanism wedge.
- **Untracked memory dies**: the sandbox workspace is wiped per dispatch, so
  ONLY committed memory survives — the standard instructs committing memory
  edits with the increment; an uncommitted memory edit is lost by design.
- **Memory edits and change accounting**: `.devclaw/` edits ride the normal
  one-definition-of-change span and the increment's PR — no separate
  delivery path, no exemption from gates. Advisory size/scope projections
  must not treat memory-only edits as scope creep.
- **Concurrent writers**: one-worker-per-project serializes in-repo memory
  writes within a project; cross-project memories are disjoint by
  construction.
- **Index drift**: an index line pointing at a deleted fact file (or a fact
  file missing from the index) is surfaced by an advisory doctor project
  check — drift the stubbed suite structurally cannot see.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A project's worker memory MUST live in the repo as a committed
  `.devclaw/` directory: a `MEMORY.md` index — one line per fact, formatted
  `- [title](memory/<file>.md) — one-line hook` — plus one-fact-per-file
  entries under `.devclaw/memory/`, each a `# title` heading and free prose
  body (no frontmatter; clarified 2026-09-01). Plain markdown throughout —
  readable by any agent, discovery is ls+read, no vendor wiring
  (constitution II).
- **FR-002**: The dispatch brief MUST NOT inject accumulated repo notes. When
  `.devclaw/MEMORY.md` exists in the workspace, the brief carries a one-line
  pointer to it; when absent, the brief renders exactly as a no-notes brief
  does today. Brief size MUST be independent of the number of stored facts.
- **FR-003**: The worker's standard instructions MUST carry the memory
  protocol: read the index at session start, pull fact files on demand, and
  write under the policy — one fact per file; update-over-append; delete
  facts proven wrong; never record goal-scoped state (attempts, failure
  context, steering); **mechanize-first** — a lesson enforceable by a
  committed mechanism (wrapper, config default, guard) MUST become that
  mechanism, with memory holding at most a pointer.
- **FR-004**: The settle-side hand-back lane — free-text notes parsed from
  worker results and merged into a host-side accumulated blob — MUST be
  retired in the same arc: no host-side store of repo memory remains, and the
  lane's tests are removed symmetrically (the ratchet).
- **FR-005**: Onboarding MUST seed an empty `.devclaw/` layout (index +
  the write policy stated in-file for non-devclaw readers) via the install
  PR. No migration machinery ships: deploy retires the host-side notes store
  unconditionally and instance-wide, and existing blob content is dropped —
  workers re-record surviving facts organically (clarified 2026-09-01).
- **FR-006**: Goal-scoped context MUST keep riding the brief: the live
  referenced-issue contract (fetched fresh at each dispatch boundary),
  failure context, and steering. None of it is ever written to repo memory.
- **FR-007**: Memory edits MUST flow through the increment's normal change
  span, commit, and PR — reviewed like all worker output; no separate
  delivery path and no gate exemption.
- **FR-008**: Doctor MUST gain an advisory project check for memory health:
  every `MEMORY.md` index line resolves to an existing fact file, every fact
  file is indexed, and the index has not grown past a generous size
  threshold (~30 facts — a curation smell, clarified 2026-09-01); advisory
  (WARN), never a hold, nothing dropped; with a seeded-fault test (the
  FR-014 convention). There is NO hard cap on memory size anywhere in the
  system.
- **FR-009**: The generic task-execution instruction text MUST have exactly
  one canonical home in the worker-kind skill bundle (constitution II), and
  the per-dispatch brief MUST be composed only of: standard pointer, task,
  live contract, failure context, steering, memory pointer.
- **FR-010**: The tick path MUST stay zero-token: no probing, reading, or
  cognition about memory on idle ticks; everything here happens at dispatch
  or inside the worker session (constitution III).

### Key Entities

- **Memory index** (`.devclaw/MEMORY.md`): the always-read map — one line per
  fact; small by contract; never carries fact bodies.
- **Memory fact** (`.devclaw/memory/*.md`): one durable repo fact per file —
  what a fresh session must not relearn the hard way; updated in place,
  deleted when wrong.
- **Standard task prompt**: the single canonical instruction text every
  dispatch shares, versioned in the worker-skill home.
- **Dispatch brief**: the per-task remainder — task, live contract, failure
  context, steering, pointers. Shrinks; never accumulates.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The rendered brief for the most note-heavy live project (the
  devclaw repo itself, 2026-09-01 baseline: 9.4KB with the same fact repeated
  15 times) shrinks by at least 40%, and no fact text appears twice in any
  rendered brief.
- **SC-002**: A fact recorded by one goal's increment is used by a different,
  later goal on the same repo without any prompt bytes carrying the fact —
  demonstrated end-to-end on a live run.
- **SC-003**: Recording the same lesson k times across runs yields exactly
  one memory entry for it (edits, not siblings) — over a multi-night window,
  no two fact files whose content restates the same rule.
- **SC-004**: A project that never adopts `.devclaw/` renders byte-identical
  briefs before and after the change — proven by the existing brief tests.
- **SC-005**: Zero-token idle guards stay green; no new cognition or
  subprocess appears on any idle tick path.
- **SC-006**: From the deploy onward, no host-side accumulated-notes state
  exists or accrues for any registered project, and every worker increment
  reads memory only from the repo.

## Rejected alternatives (direction memory)

- **Better push curation** (fuzzy near-duplicate dedupe on the accumulated
  blob, tighter caps): treats the symptom; the rot class — append-only prose
  nobody edits — survives. Rejected 2026-09-01 brainstorm; superseded the
  earlier cap raise (4k→12k) which was the same symptom-treatment.
- **Resumable worker sessions** (persist the agent conversation across
  increments): violates the ephemeral-body/durable-mind ruling; the
  2026-09-01 200k context exhaustion is live counter-evidence; conversation
  state is unreviewable and unversioned; vendor-shaped (constitution II).
- **Memory as a host-side service** (worker reads/writes facts through a
  tool): puts repo knowledge in a second store outside the versioned
  artifact, re-creating the views-vs-state split the repo spent #616/#617
  killing; the repo IS the right store.
- **Folding memory into AGENTS.md instead of `.devclaw/`**: AGENTS.md stays
  the human/agent front door and may point at `.devclaw/`; machine-maintained
  one-fact-per-file entries need update/delete semantics a single prose file
  makes noisy. (Operator chose `.devclaw/` explicitly, 2026-09-01.)

- **One-shot migration of existing note blobs** (and per-project lane
  coexistence during rollout): rejected at clarify (2026-09-01) — the blobs'
  content is low-value near-duplicates, one-shot machinery runs once and
  rots, and coexistence means two memory homes during the transition, the
  exact silent-fork smell the one-home rule exists to prevent. Hard cut +
  organic re-recording wins.

## Assumptions

- One-worker-per-project (spec 010) serializes memory writes within a repo;
  no cross-goal write conflict handling is needed beyond git itself.
- Goal-branch delivery + merge-on-close (spec 025) is how memory edits reach
  main; a fact recorded on an unmerged goal branch becomes visible to other
  goals only after the close — accepted latency, same as any other change.
- The post-merge human review and the trend detector act as memory curators;
  no new curation machinery ships with this spec.
- The existing brief-budget machinery (spec 021/029) keeps applying to what
  remains pushed (live issue bodies, failure context); this spec removes the
  notes section from that budget rather than re-tuning it.
- `specs/tiny/doctor-ready-contract-check.md` and this spec's doctor check
  are siblings; no consolidation needed.
