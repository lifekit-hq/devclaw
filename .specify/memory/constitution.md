# devclaw Constitution

Rewritten 2026-09-13 for spec 046 (devclaw v2). `CLAUDE.md` remains the repo
contract — on any conflict `CLAUDE.md` wins and this file is corrected in the
same PR. The speckit pipeline checks specs against these principles.

## Core Principles

### I. Two actors, one line
Python decides what is determined; the model reasons everything else. The goal
layer (`devclaw/goal/`) fits one afternoon of reading and a size guard fails
the build when it stops fitting. A spec that adds a host mechanism to do what a
session could reason is unconstitutional on its face.

### II. Software owns five things
Safety (the sandbox, the credential strip), money (the quota pause, sessions
per day), state (single writer, one transaction), the verdict of record (the
project's CI on the delivered head, the one done-gate), and the protocol (issue
in, PR out, `decide`, `cancel`). Python outside these five needs a written
reason in its spec. A gap closes as a fact first, an instruction second, a
brake last.

### III. The issue is the contract
One schema, read live: the `## Done when` / `## Acceptance` section is what the
done-gate judges. The host never triages, grades, or rewrites it.

### IV. The host holds no derived state
Goals, decisions, the quota pause, and the last world fingerprint each session
was given. No failure kinds, no budgets, no holds, no counters: a fingerprint
is an observation whose loss costs one session; a hold was a judgment whose
drift cost days. GitHub carries the rest — devclaw's own records on a thread
are marked and are never instructions.

### V. Zero tokens when nothing changed
Reading the world is free. A session spawns only when the fingerprint moved.
The zero-session idle tests are load-bearing: if one fails the change is
wrong, never the test.

### VI. Done is never the agent's word
A `DONE` proposal gets a fresh read-only review session; its JSON is validated
mechanically — every clause satisfied with evidence — before the merge. An
unreadable verdict never passes. This is the one place fail-closed survives as
a host gate; the other three (materialize, change_class, test integrity) are
mechanical facts about the span.

### VII. No retries — fix the system, not the attempt
The sandbox runs what CI runs before a session ends (`.devclaw/verify`, derived
by the session, fed back on red). A red CI on a delivered head is an
environment gap; a done-gate refusal is a contract disagreement. Both stop the
goal with the fact on the thread; neither is retried. A blocked goal releases
its project lane. Only a newer instruction from the owner wakes it.

### VIII. OAuth only, model-agnostic worker
Cognition is `claude` over Pro/Max OAuth; metered keys are stripped at every
spawn site. Skills are plain markdown in one home; the agent command is the one
swap seam.

### IX. Loud failure over silent degradation
A gate crash is a failure. A delivery that cannot push fails. A missing skill
bundle refuses to run. Usage limits pause-and-resume with the work snapshotted.

## Development Workflow

- Every spec and tinyspec names its north-star case first: which failure it
  moves (*stopped when it shouldn't* / *ran and produced garbage* / *ran but
  needed the owner*), the number that shows it, and its cut condition.
- Behaviour-changing work starts with `/speckit-specify` → `/speckit-clarify`
  (with Denys) → plan → tasks → implement. The spec is the direction memory.
- The suite is a tripwire net: a PR ships a test only when it touches an
  autonomous-operation invariant; the ratchet is symmetric.
- Branch per change; squash merges; the PR's own CI is the verdict before merge.

**Version**: 3.0.0 | **Ratified**: 2026-08-13 | **Last Amended**: 2026-09-13
(3.0.0 — rewritten for spec 046: the v1 principles on typed mechanical blocks,
strictness dials, admission lint and Problem timeboxes are superseded; the
eight pillars replace them. Prior versions: `git show v1.2.0:.specify/memory/constitution.md`.)
