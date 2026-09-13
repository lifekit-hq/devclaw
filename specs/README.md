# `specs/` — the estate and its ledger

Direction memory for behaviour-changing work, one directory per spec, written
and executed through the pipeline in [`.claude/rules/speckit-workflow.md`](../.claude/rules/speckit-workflow.md).
Small work uses the tiny lane (`/speckit-tinyspec-tinyspec`, one file under `specs/tiny/`) instead.

## If it is in the tree, it is alive

This directory is read as a work queue — by the owner at `/devclaw-morning`, and
by anyone asking what is left. So a directory that describes work nobody intends
to do is not a harmless archive; it reads as schedulable. Spec 007 read as
schedulable for six weeks after it was parked, and spec 036's US3 stopped its own
clock by hiding `SPECIFIED, NOT IMPLEMENTED` inside a user-story heading where no
header sweep could see it.

One closed vocabulary, checked by `tests/test_spec_estate_is_alive.py`:

| Word | Where it lives | Means |
|---|---|---|
| `SHIPPED` | the tree | every user story built; the remainder, if any, is a live-verification task with an issue |
| `PARTIAL` | the tree | some stories built — **names an owner and a date**, always |
| `DRAFT` | the tree | specified, nothing built — **names an owner and a date**, always |
| `CUT` | this ledger | deliberately dropped; the history is the archive |
| `SUPERSEDED` | this ledger | replaced by another spec, named below |

`PARKED`, `SUSPENDED`, `DEFERRED` and `NOT IMPLEMENTED` are not in the
vocabulary. A parked spec is `PARTIAL` with a clock, or it is `CUT` — those two
are the only honest states, and picking one is the owner's call. Non-terminal
states carry an owner and a date because a label with no clock stops one
(`~/memory/README.md` rule 4).

**A number is an identity, never a position.** Numbers are never reused and
never renumbered: they are cited in `CLAUDE.md`, the constitution, code comments,
tinyspec bodies, PR titles, issues, the memory vault, and in merged history,
which cannot be renumbered at all. A gap is readable, not untidy — every number
ever issued resolves here or to a directory, and the test enforces exactly that.
A citation to a number in this ledger resolves to the row, and the row names why
the directory went away.

## Terminal ledger

| # | State | Name | Disposition |
|---|---|---|---|
| 001 | SUPERSEDED | Review-Gate Repositioning | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/001-review-gate-repositioning/spec.md`). |
| 003 | SUPERSEDED | Registry as the single source of truth for dispatch (project reference | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/003-project-reference-key/spec.md`). |
| 004 | SUPERSEDED | Auth Session Refresh | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/004-auth-session-refresh/spec.md`). |
| 005 | SUPERSEDED | devclaw owns its own deployment | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/005-devclaw-self-deploy/spec.md`). |
| 006 | SUPERSEDED | Intake Readiness Gate | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/006-intake-readiness-gate/spec.md`). |
| 008 | SUPERSEDED | Speckit as the Universal Execution Substrate (retire PLAN.md) | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/008-speckit-execution-substrate/spec.md`). |
| 009 | SUPERSEDED | Universal Issue Adoption | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/009-universal-issue-adoption/spec.md`). |
| 010 | SUPERSEDED | Unit of Work & Planned Parallelism | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/010-unit-of-work-parallelism/spec.md`). |
| 011 | SUPERSEDED | ACP-Direct Runner (retire the OpenHands SDK) | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/011-acp-runner-swap/spec.md`). |
| 012 | SUPERSEDED | Saga & Unit-of-Work Prompt Contract | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/012-saga-prompt-contract/spec.md`). |
| 013 | SUPERSEDED | One definition of the change | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/013-materialize-change/spec.md`). |
| 014 | SUPERSEDED | Error-Issue Schema & Single Filing Doorway | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/014-issue-doorway/spec.md`). |
| 015 | SUPERSEDED | Live-Validation Loop | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/015-live-validation-loop/spec.md`). |
| 016 | SUPERSEDED | Instance Doctor + Per-Project Manifest | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/016-doctor-project-manifest/spec.md`). |
| 017 | SUPERSEDED | PR Authorship from Agent Commit | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/017-pr-authorship/spec.md`). |
| 018 | SUPERSEDED | Scorecard Measures the Ratchet | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/018-scorecard-ratchet/spec.md`). |
| 019 | SUPERSEDED | Goal-as-Pointer | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/019-goal-as-pointer/spec.md`). |
| 020 | SUPERSEDED | Sandbox OOM Legibility and Prevention | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/020-sandbox-oom-legibility/spec.md`). |
| 021 | SUPERSEDED | Worker Context-Budget Invariant (two-axis overflow class fix) | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/021-worker-context-budget/spec.md`). |
| 022 | SUPERSEDED | One Dispatch Lane — issue-keyed companion dispatch over the goal primi | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/022-one-dispatch-lane/spec.md`). |
| 023 | SUPERSEDED | Event-Driven Triggers — webhooks drive the state machine, the heartbea | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/023-event-driven-triggers/spec.md`). |
| 024 | SUPERSEDED | Ticket as Contract — the issue is the only holder of "what and why" | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/024-ticket-as-contract/spec.md`). |
| 025 | SUPERSEDED | Unattended-Week Operation | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/025-unattended-operation/spec.md`). |
| 026 | SUPERSEDED | # Spec 026 — Dispatch brief budget | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/026-dispatch-brief-budget/spec.md`). |
| 027 | SUPERSEDED | # Spec 027 — Instance health drift detection | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/027-instance-health-drift/spec.md`). |
| 028 | SUPERSEDED | # Spec 028 — Stale worker inputs | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/028-stale-worker-inputs/spec.md`). |
| 029 | SUPERSEDED | Code-map pointer + brief retention | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/029-code-map-brief/spec.md`). |
| 030 | SUPERSEDED | Environment-capability admission | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/030-env-admission/spec.md`). |
| 031 | SUPERSEDED | Structured problem resolution | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/031-problem-resolution/spec.md`). |
| 032 | SUPERSEDED | Verification ownership | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/032-verification-ownership/spec.md`). |
| 034 | SUPERSEDED | Worker file memory — the repo carries the mind, the prompt carries the | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/034-worker-file-memory/spec.md`). |
| 035 | SUPERSEDED | Pinned done-gate clauses — decompose the contract once per revision | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/035-pin-donegate-clauses/spec.md`). |
| 036 | SUPERSEDED | A provider-side transient pauses and resumes — it never burns the disp | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/036-server-transient-pause/spec.md`). |
| 037 | SUPERSEDED | Loop-shape demolition — the worker closes its own findings; the host k | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/037-loop-shape-demolition/spec.md`). |
| 038 | SUPERSEDED | An environment gap is filed when the pipeline says it is filed | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/038-env-deficiency-filing-honesty/spec.md`). |
| 039 | SUPERSEDED | Loop Health Metrics | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/039-loop-health-metrics/spec.md`). |
| 041 | SUPERSEDED | After a stop — decisions execute, stops re-check | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/041-after-a-stop/spec.md`). |
| 042 | SUPERSEDED | One credential registry — register once, visible at every hop | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/042-one-credential-registry/spec.md`). |
| 043 | SUPERSEDED | The worker reads the loop's own facts | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/043-worker-reads-the-loop/spec.md`). |
| 044 | SUPERSEDED | A stop needs evidence | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/044-a-stop-needs-evidence/spec.md`). |
| 045 | SUPERSEDED | The change is the worker's own | By 046, 2026-09-13: the v1 goal layer and every mechanism these specs added to it were replaced by the v2 rebuild (spec 046). The history is the archive (`git show v1.2.0:specs/045-the-change-is-the-workers-own/spec.md`). |
| 002 | SUPERSEDED | Planning-strategy dial | By 008 (2026-08-15), then with it by 046. |
| 007 | CUT | Autonomous issue claim & dispatch | 2026-09-10: its own gate read `pass: false`; filing a goal from an issue stays the owner's act. |
| 033 | VOID | — | Never issued. |
| 040 | SUPERSEDED | The contract reaches the actor | By 043 (2026-09-10), then with it by 046. |

Every v1 spec (001–045) and every tinyspec under `specs/tiny/` was superseded
by [046](./046-devclaw-v2/spec.md) on 2026-09-13: the v1 goal layer they
describe was deleted and rebuilt from an empty package. Read any of them at
the `v1.2.0` tag.
